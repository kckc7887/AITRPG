from pydantic import ConfigDict
from pydantic import model_validator

from aitrpg.domain.models import KeeperResponse
from aitrpg.domain.models import RulingRepair
from aitrpg.domain.rules import normalize_skill


def scoped_repair_type(turn_id=None, is_attack_allowed=True):
    if not turn_id:
        return RulingRepair

    class ScopedRulingRepair(RulingRepair):
        model_config = ConfigDict(
            json_schema_extra={
                'description': (
                    f'当前战斗行动者只有{turn_id}。攻击attacker_id须为此ID，'
                    '旧被拒行动没有排队；无法合法修复时commands留空。'
                )
            }
        )

        @model_validator(mode='after')
        def check_turn(self):
            for command in self.commands:
                if command.kind == 'combat_attack' and not is_attack_allowed:
                    raise ValueError('当前玩家已让出，不能修复为主动攻击')
                if command.kind == 'combat_attack' and (
                    command.parameters.get('attacker_id') != turn_id
                ):
                    raise ValueError(
                        f'当前攻击者只能是{turn_id}，旧攻击未排队'
                    )
            for check in self.checks:
                if normalize_skill(check.skill).startswith(
                    ('格斗：', '射击：')
                ):
                    if check.character_id != turn_id:
                        raise ValueError('不能在当前回合补做旧角色的攻击检定')
                    if not is_attack_allowed:
                        raise ValueError('当前玩家已让出，不能补做攻击检定')
            return self

    return ScopedRulingRepair


async def repair_command(service, game, cards, command, work, error):
    return await _repair(
        service, game, cards, command, command.id, work, error, False
    )


async def repair_check(service, game, cards, check, index, work, error):
    return await _repair(
        service, game, cards, check, f'check:{index}', work, error, True
    )


async def _repair(
    service, game, cards, rejected, identifier, work, error, is_check
):
    repairs = work.setdefault('command_repairs', {})
    record = repairs.get(identifier)
    turn = service._combat_turn(game) if game.mode == 'combat' else None
    turn_id = turn.get('id') if turn else None
    actor_id = turn.get('actor_id') if turn else None
    is_attack_allowed = not any(
        response.get('actor_id') == actor_id and response.get('is_pass')
        for response in work['submitted']
    )
    output_type = scoped_repair_type(turn_id, is_attack_allowed)
    reaction_key = identifier
    if record and not record.get('next') and not record.get('next_check'):
        try:
            output_type.model_validate(record['response'])
        except ValueError:
            work.setdefault('rejected_repairs', {})[identifier] = record
            record = None
            reaction_key += ':scope-correction'
    if record is None:
        try:
            repair = await service._invoke(
                game,
                game.keeper_actor_id,
                'keeper_repair',
                output_type,
                extra={
                    'reaction_key': reaction_key,
                    'allowed_attacker_id': turn_id,
                    'is_attack_allowed': is_attack_allowed,
                    (
                        'rejected_check' if is_check else 'rejected_command'
                    ): rejected.model_dump(mode='json'),
                    'validation_error': str(error),
                    'player_actions': work['submitted'],
                    'actual_rule_results': work['events'],
                    'pending_reply': work['reply'],
                    'updated_characters': {
                        key: card.model_dump(mode='json')
                        for key, card in cards.items()
                    },
                    'combat_turn': service._combat_turn(game),
                    'instruction': (
                        '仅修复被拒且尚未产生后果的这一条检定或命令。'
                        '不得改变玩家回应、重投已完成骰或重复既有后果。'
                        '当前角色观察或让出时，不替他攻击；别人的预声明'
                        '到本人合法轮次再处理。不能自动解决后续节点。'
                        'commands可为空，说明原因，并重写尚未发布叙事。'
                        '连同private_messages、improvisation、flags一起'
                        '重写；被拒命令没有发生，不能继续告诉角色已执行。'
                    ),
                },
            )
        except ValueError as repair_error:
            raise ValueError(str(error)) from repair_error
        previous_reply = KeeperResponse.model_validate(work['reply'])
        service._validate_narration(
            game,
            previous_reply.model_copy(
                update={
                    'narration': repair.narration,
                    'private_messages': repair.private_messages,
                    'flags': repair.flags,
                }
            ),
        )
        record = {
            'response': repair.model_dump(mode='json'),
            'next': 0,
            'next_check': 0,
        }
        repairs[identifier] = record
        work['events'].append(
            {
                'kind': 'ruling',
                'is_private': True,
                'data': {
                    'command': 'repair_ruling',
                    'reason': repair.reason,
                    'rejected_command_id': identifier,
                },
            }
        )
        service._checkpoint(game, cards, work)
    repair = RulingRepair.model_validate(record['response'])
    for index, check in enumerate(repair.checks):
        if index < record.get('next_check', 0):
            continue
        if service._is_repeated_check(check, work):
            raise ValueError('修复裁定不能重复已有检定')
        service._apply_check(game, cards, check, work)
        record['next_check'] = index + 1
        service._checkpoint(game, cards, work)
    for index, replacement in enumerate(repair.commands):
        if index < record['next']:
            continue
        await service._apply_command(game, cards, replacement, work)
        record['next'] = index + 1
        service._checkpoint(game, cards, work)
    return repair
