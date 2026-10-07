import asyncio
import json

import pytest
from test_games import ScriptedClient
from test_games import install_fixed_check
from test_games import setup_platform

from aitrpg.application.platform import Platform
from aitrpg.config import Settings
from aitrpg.domain.models import CheckRequest
from aitrpg.domain.models import KeeperResponse
from aitrpg.domain.models import PlayerResponse
from aitrpg.domain.models import RuleCommand
from aitrpg.domain.models import RulingRepair


class CountingDice:
    def __init__(self):
        self.calls = []

    def randint(self, minimum, maximum):
        self.calls.append((minimum, maximum))
        return minimum


@pytest.mark.parametrize('attacker_id', ['card-1', 'card-0'])
async def test_pass_and_unapproved_attack_are_repaired_without_damage(
    tmp_path, attacker_id
):
    wrong_private = 'UNEXECUTED_ATTACK_PRIVATE_RESULT'
    wrong_narration = 'UNEXECUTED_ATTACK_PUBLIC_RESULT'

    def handler(call):
        if call['task'] == 'player':
            return PlayerResponse(speech='我先观察，暂不攻击。', is_pass=True)
        if call['task'] == 'keeper_resolution':
            return KeeperResponse(
                narration=wrong_narration,
                private_messages={'player-1': wrong_private},
                flags={'enemy_was_hit': True},
                commands=[
                    RuleCommand(
                        kind='combat_attack',
                        parameters={
                            'attacker_id': attacker_id,
                            'target_id': 'enemy',
                            'mode': 'melee',
                        },
                        reason='错误地替未获攻击机会的角色发动攻击。',
                    )
                ],
            )
        if call['task'] == 'keeper_repair':
            return RulingRepair(
                narration='本轮暂未发动攻击，下一位按DEX次序行动。',
                reason='当前玩家让出机会，另一位尚未轮到。',
            ).model_dump()
        return ScriptedClient.default(call)

    client = ScriptedClient(handler)
    platform, game, _, _ = setup_platform(tmp_path, client)
    card = platform.characters.get('card-0')
    card.attributes['DEX'] = 80
    platform.store.put('character', card.id, card.model_dump(mode='json'))
    game.mode = 'combat'
    game.last_keeper = KeeperResponse(narration='已有战斗开场').model_dump()
    game.combat = {
        'turn': 0,
        'round': 1,
        'defenses': {},
        'order': [
            {
                'id': 'card-0',
                'actor_id': 'player-0',
                'name': '调查员0',
                'dex': 80,
                'hp': 12,
                'max_hp': 12,
                'conditions': [],
            },
            {
                'id': 'enemy',
                'name': '原创敌人',
                'dex': 70,
                'hp': 20,
                'max_hp': 20,
                'fighting': 40,
                'dodge': 30,
                'damage': '1D3',
                'armor': 0,
                'conditions': [],
            },
            {
                'id': 'card-1',
                'actor_id': 'player-1',
                'name': '调查员1',
                'dex': 60,
                'hp': 12,
                'max_hp': 12,
                'conditions': [],
            },
        ],
    }
    platform.store.put('game', game.id, game.model_dump(mode='json'))
    dice = CountingDice()
    platform.games.rng = dice
    result = await platform.step_game(game.id)
    assert not result.last_error
    assert result.node_count == 1
    assert platform.games._combat_turn(result)['id'] == 'enemy'
    assert result.combat['order'][1]['hp'] == 20
    assert platform.characters.get('card-0').current_hp == 12
    assert platform.characters.get('card-1').current_hp == 12
    assert not dice.calls
    assert 'enemy_was_hit' not in result.flags
    history = json.dumps(platform.store.events(game.id), ensure_ascii=False)
    assert wrong_narration not in history
    assert wrong_private not in history
    repair_calls = [
        call for call in client.calls if call['task'] == 'keeper_repair'
    ]
    assert len(repair_calls) == 1
    assert repair_calls[0]['actor'] == 'keeper'
    assert not any(call['task'] == 'reaction' for call in client.calls)


async def test_restart_after_repair_checkpoint_does_not_repeat_damage_or_check(
    tmp_path, monkeypatch
):
    def handler(call):
        if call['task'] == 'keeper_resolution':
            return KeeperResponse(
                narration='手背被碎玻璃划伤。',
                checks=[CheckRequest(character_id='card-0', skill='侦查')],
                commands=[
                    RuleCommand(
                        kind='damage',
                        character_id='card-0',
                        parameters={'amount': '两点'},
                        reason='手背擦伤固定扣两点HP。',
                    )
                ],
            )
        if call['task'] == 'keeper_repair':
            return RulingRepair(
                narration='碎玻璃造成擦伤，伤害为两点。',
                reason='仅把损伤量修正为引擎接受的整数。',
                commands=[
                    RuleCommand(
                        kind='damage',
                        character_id='card-0',
                        parameters={'amount': 2},
                        reason='同一手背擦伤，修正参数类型。',
                    )
                ],
            ).model_dump()
        if call['task'] == 'keeper_feedback':
            return KeeperResponse(narration='发现标签，擦伤只扣了两点HP。')
        return ScriptedClient.default(call)

    client = ScriptedClient(handler)
    platform, game, _, _ = setup_platform(tmp_path, client)
    rolls = install_fixed_check(monkeypatch)
    checkpoint = platform.games._checkpoint

    def cancel_after_replacement(current, cards, work):
        checkpoint(current, cards, work)
        if any(
            record['next'] == 1
            for record in work.get('command_repairs', {}).values()
        ):
            raise asyncio.CancelledError

    monkeypatch.setattr(
        platform.games, '_checkpoint', cancel_after_replacement
    )
    with pytest.raises(asyncio.CancelledError):
        await platform.step_game(game.id)
    assert platform.characters.get('card-0').current_hp == 12
    assert len(rolls) == 1
    first_roll_id = rolls[0].id
    restored = Platform(Settings(data_dir=tmp_path / 'platform'), client)
    result = await restored.step_game(game.id)
    assert not result.last_error
    assert result.node_count == 1
    assert restored.characters.get('card-0').current_hp == 10
    assert len(rolls) == 1
    events = restored.store.events(game.id)
    check_events = [event for event in events if event['kind'] == 'check']
    assert len(check_events) == 1
    assert check_events[0]['data']['id'] == first_roll_id
    assert (
        sum(
            event['kind'] == 'ruling'
            and event['data'].get('command') == 'damage'
            for event in events
        )
        == 1
    )
    assert sum(call['task'] == 'keeper_repair' for call in client.calls) == 1
    assert sum(call['task'] == 'keeper_feedback' for call in client.calls) == 1
