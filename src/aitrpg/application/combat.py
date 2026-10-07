import re
from copy import deepcopy
from typing import Any

from aitrpg.domain.models import Character
from aitrpg.domain.models import Game
from aitrpg.domain.models import PlayerResponse
from aitrpg.domain.models import Roll
from aitrpg.domain.models import RuleCommand
from aitrpg.domain.rules import SUCCESS_LEVELS
from aitrpg.domain.rules import apply_command
from aitrpg.domain.rules import normalize_skill
from aitrpg.domain.rules import opposed_result
from aitrpg.domain.rules import roll_dice
from aitrpg.domain.rules import roll_percentile
from aitrpg.domain.rules import skill_check
from aitrpg.domain.rules import success_level
from aitrpg.domain.rules import weapon_damage

INCAPACITATED = {
    'dead',
    'unconscious',
    'dying',
    'defeated',
    'permanent_insanity',
}
NPC_REQUIRED = {
    'id',
    'name',
    'dex',
    'hp',
    'fighting',
    'dodge',
    'damage',
    'armor',
}
STUN_SOURCE = 'https://cthulhuwiki.chaosium.com/equipment/weapons.html'
KNIFE_NAMES = {'格斗刀', '小刀', '匕首', '中型刀', '大型刀', 'knife'}
CONTACT_TASERS = {'电击器', '接触电击器', 'taser (contact)', 'contact taser'}


class ValidatorDice:
    def randint(self, minimum, maximum):
        return minimum


def _damage_spec(expression: str) -> tuple[str, list[str]]:
    if not isinstance(expression, str):
        raise ValueError('武器伤害必须为安全骰式文字')
    match = re.fullmatch(r'\s*(.+?)\s*\+\s*(stun|眩晕)\s*', expression, re.I)
    if match:
        return match[1].strip(), ['stun']
    return expression, []


def _validate_profile(profile: dict) -> dict:
    expression, effects = _damage_spec(profile['damage'])
    profile['damage'] = expression
    profile['effects'] = effects
    if effects and not re.search(r'\bDB\b', expression, re.I):
        profile['damage_bonus'] = '0'
    weapon_damage(expression, profile['damage_bonus'], rng=ValidatorDice())
    return profile


def _integer(value: Any, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum or value > 10000:
        raise ValueError(f'{name}必须为{minimum}至10000的整数')
    return value


def _event(work: dict, kind: str, data: dict, is_private: bool) -> None:
    work['events'].append(
        {
            'kind': kind,
            'data': data,
            'is_private': is_private,
        }
    )
    if kind == 'check':
        work['has_rolls'] = True


def _roll_event(work: dict, roll: Roll, entity_id: str, private: bool) -> None:
    _event(
        work,
        'check',
        {
            **roll.model_dump(mode='json'),
            'character_id': entity_id,
        },
        private,
    )


def _checkpoint(service, game, cards, work) -> None:
    callback = getattr(service, '_checkpoint', None)
    if callback is not None:
        callback(game, cards, work)


def _complete_attack(service, game, cards, command, work, action_id) -> None:
    completed = game.combat.setdefault('completed_actions', [])
    if action_id not in completed:
        completed.append(action_id)
    command_ids = game.combat.setdefault('completed_command_ids', [])
    if command.id not in command_ids:
        command_ids.append(command.id)
    game.combat.setdefault('attack_progress', {}).pop(command.id, None)
    _checkpoint(service, game, cards, work)


def _is_incapacitated(entry: dict) -> bool:
    return bool(INCAPACITATED.intersection(entry.get('conditions', [])))


def combat_turn(game: Game, cards: dict | None = None) -> dict | None:
    order = game.combat.get('order', [])
    if not order:
        return None
    if cards:
        for entry in order:
            if entry['id'] in cards:
                entry['conditions'] = list(cards[entry['id']].conditions)
                entry['hp'] = cards[entry['id']].current_hp
    turn = game.combat.get('turn', 0) % len(order)
    for _ in order:
        entry = order[turn]
        if not _is_incapacitated(entry):
            game.combat['turn'] = turn
            return entry
        turn += 1
        if turn >= len(order):
            turn = 0
            game.combat['round'] = game.combat.get('round', 1) + 1
            game.combat['defenses'] = {}
    return None


def _start_combat(service, game, cards, command, work) -> None:
    if game.mode == 'combat':
        raise ValueError('战斗已开始，不能用重新开战重置行动')
    parameters = command.parameters
    participants = parameters.get('participants')
    group_actors = set(service._group_actor_ids(game))
    available = {
        key: card
        for key, card in cards.items()
        if card.actor_id in group_actors
    }
    if participants is None:
        participants = list(available)
    if (
        not isinstance(participants, list)
        or any(not isinstance(item, str) for item in participants)
        or not set(participants) <= set(available)
    ):
        raise ValueError('战斗调查员必须属于当前分队')
    readied = parameters.get('readied_guns', [])
    if (
        not isinstance(readied, list)
        or any(not isinstance(item, str) for item in readied)
        or not set(readied) <= set(participants)
    ):
        raise ValueError('准备枪械者必须是参与战斗的调查员')
    order = []
    for identifier in participants:
        card = available[identifier]
        if INCAPACITATED.intersection(card.conditions):
            continue
        has_firearm = any(
            normalize_skill(str(weapon.get('skill', ''))).startswith('射击：')
            for weapon in card.weapons
        )
        if identifier in readied and not has_firearm:
            raise ValueError(f'{card.name}没有可准备的枪械')
        dexterity = card.attributes['DEX']
        order.append(
            {
                'id': identifier,
                'name': card.name,
                'actor_id': card.actor_id,
                'dex': dexterity + (50 if identifier in readied else 0),
                'base_dex': dexterity,
                'hp': card.current_hp,
                'max_hp': card.max_hp,
                'conditions': list(card.conditions),
                'combat_skill': max(
                    (
                        value
                        for skill, value in card.skills.items()
                        if skill.startswith(('格斗：', '射击：'))
                    ),
                    default=0,
                ),
            }
        )
    scenario = service.scenario(game)
    known_npcs = {npc.id for npc in scenario.npcs}
    improvisation = parameters.get('improvisation') or work.get(
        'reply', {}
    ).get('improvisation')
    for original in parameters.get('npcs', []):
        if not isinstance(original, dict):
            raise ValueError('战斗NPC必须是包含统计数据的对象')
        missing = NPC_REQUIRED - set(original)
        if missing:
            raise ValueError('NPC缺少统计字段：' + '、'.join(sorted(missing)))
        npc = deepcopy(original)
        npc.pop('actor_id', None)
        if not isinstance(npc['id'], str) or not npc['id'] or not npc['name']:
            raise ValueError('NPC必须具有有效ID和名称')
        source_id = npc.get('source_id', npc['id'])
        if source_id not in known_npcs and not improvisation:
            raise ValueError(f'NPC来源不存在：{source_id}；即兴NPC须说明依据')
        for field in ('dex', 'hp', 'fighting', 'dodge', 'armor'):
            _integer(npc[field], f'NPC {field}', 1 if field == 'hp' else 0)
        if npc.get('con') is not None:
            _integer(npc['con'], 'NPC con', 1)
        if 'firearms' in npc:
            _integer(npc['firearms'], 'NPC firearms')
        if 'attacks_per_round' in npc:
            _integer(npc['attacks_per_round'], 'NPC attacks_per_round', 1)
        if not isinstance(npc['damage'], str):
            raise ValueError('NPC伤害必须为骰式')

        _profile(npc, None, {'mode': 'melee'})
        npc.update(
            {
                'max_hp': npc['hp'],
                'conditions': [],
                'combat_skill': max(npc['fighting'], npc.get('firearms', 0)),
                'source_id': source_id,
            }
        )
        npc['base_dex'] = npc['dex']
        if npc.get('is_readied_gun'):
            if 'firearms' not in npc:
                raise ValueError('准备枪械的NPC必须提供firearms技能')
            _integer(npc['firearms'], 'NPC firearms')
            npc['dex'] += 50
        order.append(npc)
    if not order or len({entry['id'] for entry in order}) != len(order):
        raise ValueError('战斗参与者不能为空，且ID必须唯一')
    order.sort(key=lambda entry: (-entry['dex'], -entry['combat_skill']))
    game.mode = 'combat'
    game.combat = {
        'order': order,
        'turn': 0,
        'round': 1,
        'defenses': {},
        'forfeit_attacks': {},
        'completed_actions': [],
    }
    _event(
        work,
        'ruling',
        {
            'command': 'start_combat',
            'reason': command.reason,
            'initiative': [
                {'id': entry['id'], 'dex': entry['dex']} for entry in order
            ],
            'npc_source_ids': [
                entry.get('source_id')
                for entry in order
                if not entry.get('actor_id')
            ],
            'improvisation': improvisation or '',
        },
        True,
    )


def _split_groups(service, game, command: RuleCommand, work: dict) -> None:
    if game.mode == 'combat':
        raise ValueError('战斗中不能直接分队，请先结束战斗')
    groups = command.parameters.get('groups')
    if not isinstance(groups, dict) or not groups:
        raise ValueError('分队须提供groups：分队名到身份ID列表')
    assignments = {}
    known = {seat.actor_id for seat in game.seats}
    for group_id, actor_ids in groups.items():
        if not isinstance(group_id, str) or not group_id.strip():
            raise ValueError('分队名称不能为空')
        if not isinstance(actor_ids, list) or not actor_ids:
            raise ValueError('每个分队须具有成员')
        for actor_id in actor_ids:
            if (
                not isinstance(actor_id, str)
                or actor_id not in known
                or actor_id in assignments
            ):
                raise ValueError('分队成员必须属于游戏且不能重复')
            assignments[actor_id] = group_id
    if set(assignments) != known:
        raise ValueError('分队必须为每个调查员安排一支队伍')
    requested_scenes = command.parameters.get('group_scenes', {})
    if not isinstance(requested_scenes, dict) or not set(
        requested_scenes
    ).issubset(groups):
        raise ValueError('分队场景必须属于本次分队')
    for scene_id in requested_scenes.values():
        service._validate_scene(game, scene_id)
    previous_scenes = game.scheduler.get('group_scenes', {})
    game.scheduler['group_scenes'] = {
        group_id: requested_scenes.get(
            group_id, previous_scenes.get(group_id, game.scene_id)
        )
        for group_id in groups
    }
    game.seats = [
        seat.model_copy(update={'group_id': assignments[seat.actor_id]})
        for seat in game.seats
    ]
    if game.group_id not in groups:
        game.group_id = next(iter(groups))
    game.scene_id = game.scheduler['group_scenes'][game.group_id]
    _event(
        work,
        'ruling',
        {
            'command': 'split_groups',
            'groups': groups,
            'group_scenes': game.scheduler['group_scenes'],
            'reason': command.reason,
        },
        command.is_private,
    )


def _profile(entry: dict, card: Character | None, parameters: dict) -> dict:
    if parameters.get('mode', 'melee') not in ('melee', 'firearm'):
        raise ValueError('攻击模式须为melee或firearm')
    if card is None:
        is_firearm = parameters.get('mode', 'melee') == 'firearm'
        key = 'firearms' if is_firearm else 'fighting'
        if key not in entry:
            raise ValueError(f'NPC缺少攻击技能：{key}')
        return _validate_profile(
            {
                'skill': key,
                'target': entry[key],
                'damage': entry['damage'],
                'damage_bonus': entry.get('damage_bonus', '0'),
                'is_firearm': is_firearm,
                'is_impaling': bool(entry.get('is_impaling', is_firearm)),
                'name': entry.get('weapon_name', 'NPC攻击'),
            }
        )
    index = parameters.get('weapon_index')
    if index is None:
        weapon = {'name': '徒手', 'skill': '格斗：斗殴', 'damage': '1D3'}
    else:
        _integer(index, '武器序号')
        if index >= len(card.weapons):
            raise ValueError('角色卡没有该武器')
        weapon = card.weapons[index]
        if not weapon.get('damage'):
            raise ValueError('角色卡武器缺少伤害资料')
    official_skill = (
        '格斗：斗殴' if weapon.get('name', '').lower() in KNIFE_NAMES else None
    )
    contact_taser = (
        weapon.get('name', '').lower() in CONTACT_TASERS
        and (
            '接触' in str(weapon.get('notes', ''))
            or 'contact' in str(weapon.get('notes', '')).lower()
        )
        and _damage_spec(weapon['damage'])[1] == ['stun']
    )
    contact_skill = '格斗：斗殴' if contact_taser else None
    declared_skill = weapon.get('skill') or official_skill or contact_skill
    skill_name = declared_skill or parameters.get('skill')
    if not skill_name:
        raise ValueError(
            '角色卡武器未记录技能，主持须以skill指定卡上真实技能并说明依据'
        )
    skill = normalize_skill(skill_name)
    if skill not in card.skills:
        raise ValueError('角色卡没有武器使用技能')
    if (
        declared_skill
        and parameters.get('skill')
        and normalize_skill(parameters['skill']) != skill
    ):
        raise ValueError('不能用主持指定技能替换武器的真实技能')
    is_firearm = skill.startswith('射击：')
    if parameters.get('mode') and parameters['mode'] != (
        'firearm' if is_firearm else 'melee'
    ):
        raise ValueError('攻击模式与角色卡武器不一致')
    return _validate_profile(
        {
            'skill': skill,
            'target': card.skills[skill],
            'damage': weapon['damage'],
            'damage_bonus': '0' if is_firearm else card.damage_bonus,
            'is_firearm': is_firearm,
            'is_impaling': bool(
                weapon.get('is_impaling', is_firearm or bool(official_skill))
            ),
            'name': weapon.get('name', skill),
            'skill_ruling': not bool(declared_skill),
            'skill_source': (
                '角色卡武器'
                if weapon.get('skill')
                else (
                    '官方刀具使用技能'
                    if official_skill
                    else (
                        '接触式电击使用卡上斗殴，适用性待主持确认'
                        if contact_skill
                        else '主持指明角色卡真实技能'
                    )
                )
            ),
        }
    )


def _weapon_effects(profile, target, damage, work, private, rng):
    for effect in profile.get('effects', []):
        duration = roll_dice('1D6', rng=rng, reason='若眩晕生效的持续轮数')
        duration.details['is_conditional'] = True
        _roll_event(work, duration, target['id'], private)
        _event(
            work,
            'ruling',
            {
                'command': 'weapon_effect',
                'character_id': target['id'],
                'details': {
                    'effect': effect,
                    'weapon': profile['name'],
                    'damage': damage,
                    'duration_rounds_if_applied': duration.total,
                    'duration_roll_id': duration.id,
                    'source': STUN_SOURCE,
                    'status': 'pending_keeper_ruling',
                    'needs_keeper_ruling': (
                        '数值伤害已结算；眩晕是否适用该目标由主持裁定，'
                        '生效时采用已骰轮数，不重新投骰'
                    ),
                },
            },
            private,
        )


def _check(
    card: Character | None,
    skill: str,
    target: int,
    bonus: int,
    reason: str,
    rng: Any,
) -> Roll:
    bonus = max(-2, min(2, bonus))
    if card:
        return skill_check(
            card,
            skill,
            bonus_dice=bonus,
            mode='combat',
            reason=reason,
            rng=rng,
        )
    roll = roll_percentile(bonus, rng=rng, reason=reason)
    level = success_level(target, roll.total)
    roll.details.update(
        {
            'skill': skill,
            'target': target,
            'threshold': target,
            'difficulty': 'regular',
            'level': level,
            'is_success': SUCCESS_LEVELS[level] >= 2,
        }
    )
    return roll


async def _defense(
    service,
    game,
    entry,
    profile,
    work,
    attack_index=0,
    pending_character: Character | None = None,
) -> tuple[str, int | None]:
    if _is_incapacitated(entry):
        return 'none', None
    allowed = ['cover'] if profile['is_firearm'] else ['dodge', 'fight_back']
    if entry.get('actor_id'):
        response = await service._invoke(
            game,
            entry['actor_id'],
            'reaction',
            PlayerResponse,
            extra={
                'pending_character': (
                    pending_character.model_dump(mode='json')
                    if pending_character is not None
                    else None
                ),
                'reaction_key': (
                    f'{game.combat["round"]}:{game.combat["turn"]}:'
                    f'{attack_index}'
                ),
                'attack': {
                    'weapon': profile['name'],
                    'mode': ('firearm' if profile['is_firearm'] else 'melee'),
                    'target_id': entry['id'],
                    'allowed_defenses': allowed,
                    'instruction': '明确选择defense字段，枪击仅能选择cover。',
                },
            },
        )
        defense = response.defense
        weapon_index = response.defense_weapon_index
        _event(
            work,
            'reaction',
            {
                'actor_id': entry['actor_id'],
                **response.model_dump(mode='json'),
            },
            False,
        )
    else:
        weapon_index = None
        defense = entry.get('defense')
        if defense is None:
            defense = (
                'cover'
                if profile['is_firearm']
                else (
                    'fight_back'
                    if entry['fighting'] >= entry['dodge']
                    else 'dodge'
                )
            )
    if defense not in allowed:
        raise ValueError(
            '防御者必须明确选择合法防御方式：' + '、'.join(allowed)
        )
    return defense, weapon_index


def _damage_target(game, cards, entry, amount, armor, work, private, rng):
    if entry['id'] in cards:
        updated, rolls, details = apply_command(
            cards[entry['id']],
            'damage',
            {'amount': amount, 'armor': armor},
            day=game.day,
            rng=rng,
        )
        cards[updated.id] = updated
        entry['hp'] = updated.current_hp
        entry['conditions'] = list(updated.conditions)
        for roll in rolls:
            _roll_event(work, roll, entry['id'], private)
    else:
        damage = max(0, amount - armor)
        entry['hp'] = max(0, entry['hp'] - damage)
        conditions = entry['conditions']
        details = {'damage': damage, 'current_hp': entry['hp']}
        if damage >= entry['max_hp']:
            conditions.append('dead')
        elif damage * 2 >= entry['max_hp']:
            if 'major_wound' not in conditions:
                conditions.append('major_wound')
            if entry.get('con') is not None:
                check = _check(None, 'CON', entry['con'], 0, 'NPC重伤', rng)
                _roll_event(work, check, entry['id'], private)
                if not check.details['is_success']:
                    conditions.append('unconscious')
            else:
                details['needs_keeper_ruling'] = 'NPC重伤缺少CON资料'
        if entry['hp'] == 0 and 'dead' not in conditions:
            conditions.append('defeated')
        entry['conditions'] = list(dict.fromkeys(conditions))
        details['conditions'] = entry['conditions']
    _event(
        work,
        'ruling',
        {
            'command': 'combat_damage',
            'character_id': entry['id'],
            'details': details,
        },
        private,
    )


async def _attack(service, game, cards, command, work) -> None:
    if game.mode != 'combat':
        raise ValueError('战斗攻击须先开始战斗')
    if command.id in game.combat.get('completed_command_ids', []):
        return
    progress = game.combat.setdefault('attack_progress', {}).get(command.id)
    current = (
        next(
            (
                entry
                for entry in game.combat['order']
                if entry['id'] == progress['attacker_id']
            ),
            None,
        )
        if progress
        else combat_turn(game, cards)
    )
    if current is None:
        raise ValueError('当前没有可行动的战斗参与者')
    parameters = command.parameters
    attacker_id = parameters.get('attacker_id') or command.character_id
    attacker_id = attacker_id or current['id']
    if attacker_id != current['id']:
        raise ValueError('只能由当前DEX顺序中的参与者执行攻击')
    action_id = f'{game.combat["round"]}:{game.combat["turn"]}'
    completed = game.combat.setdefault('completed_actions', [])
    if action_id in completed and not progress:
        raise ValueError('当前行动已完成，不能重复攻击')
    target_id = parameters.get('target_id')
    target = next(
        (entry for entry in game.combat['order'] if entry['id'] == target_id),
        None,
    )
    if target is None or target_id == attacker_id:
        raise ValueError('攻击目标必须是其他战斗参与者')
    if 'dead' in target.get('conditions', []) and not progress:
        raise ValueError('不能重复攻击已经死亡的目标')
    forfeit = game.combat.setdefault('forfeit_attacks', {})
    if forfeit.get(attacker_id, 0):
        forfeit[attacker_id] -= 1
        completed.append(action_id)
        _event(
            work,
            'ruling',
            {
                'command': 'combat_attack',
                'character_id': attacker_id,
                'details': {
                    'attack_forfeited': True,
                    'reason': '先前俯身寻找掩体',
                },
            },
            command.is_private,
        )
        _complete_attack(service, game, cards, command, work, action_id)
        return
    card = cards.get(attacker_id)
    defender_card = cards.get(target_id)
    profile = _profile(current, card, parameters)
    shots = _integer(parameters.get('shots', 1), '本次攻击次数', 1)
    is_handgun = profile['skill'] == '射击：手枪' or (
        card is None and current.get('firearm_kind') == 'handgun'
    )
    limit = (
        3
        if profile['is_firearm'] and is_handgun
        else (current.get('attacks_per_round', 1) if card is None else 1)
    )
    if shots > limit:
        raise ValueError('攻击次数超过武器或NPC资料允许范围')
    if progress:
        if progress['parameters'] != parameters:
            raise ValueError('续跑不能修改已经开始的攻击参数')
        action_id = progress['action_id']
    else:
        progress = {
            'attacker_id': attacker_id,
            'target_id': target_id,
            'parameters': deepcopy(parameters),
            'shots': shots,
            'next_attack': 0,
            'action_id': action_id,
            'cover_applied': False,
            'cover_bonus': 0,
        }
        game.combat['attack_progress'][command.id] = progress
        _checkpoint(service, game, cards, work)
    rng = getattr(service, 'rng', None)
    defenses = game.combat.setdefault('defenses', {})
    bonus = parameters.get('bonus_dice', 0)
    if type(bonus) is not int or not -2 <= bonus <= 2:
        raise ValueError('攻击净奖惩骰必须在-2至2之间')
    if profile['is_firearm'] and shots > 1:
        bonus -= 1
    for attack_index in range(progress['next_attack'], shots):
        if _is_incapacitated(current) or _is_incapacitated(target):
            break
        defender_card = cards.get(target_id)
        if profile['is_firearm'] and progress['cover_applied']:
            defense = progress['firearm_defense']
            defense_weapon_index = None
        else:
            defense, defense_weapon_index = await _defense(
                service,
                game,
                target,
                profile,
                work,
                attack_index,
                pending_character=defender_card,
            )
        if profile['is_firearm'] and not progress['cover_applied']:
            progress['firearm_defense'] = defense
            if defense == 'cover':
                cover = _check(
                    defender_card,
                    '闪避',
                    target.get('dodge', 0),
                    0,
                    '俯身寻找掩体',
                    rng,
                )
                _roll_event(work, cover, target_id, command.is_private)
                if cover.details['is_success']:
                    progress['cover_bonus'] = -1
                forfeit[target_id] = forfeit.get(target_id, 0) + 1
            progress['cover_applied'] = True
        counter_profile = None
        if defense == 'fight_back':
            choices = (
                {}
                if defense_weapon_index is None
                else {
                    'weapon_index': defense_weapon_index,
                }
            )
            counter_profile = _profile(target, defender_card, choices)
            if counter_profile['is_firearm']:
                raise ValueError('枪械射击不能作为近战还击方式')
        attack_bonus = bonus + progress['cover_bonus']
        if not profile['is_firearm'] and defenses.get(target_id, 0):
            attack_bonus += 1
        attack = _check(
            card,
            profile['skill'],
            profile['target'],
            attack_bonus,
            command.reason,
            rng,
        )
        _roll_event(work, attack, attacker_id, command.is_private)
        winner = 'attacker' if attack.details['is_success'] else 'neither'
        response_roll = None
        if not profile['is_firearm'] and defense != 'none':
            defense_skill = (
                '闪避' if defense == 'dodge' else (counter_profile['skill'])
            )
            response_roll = _check(
                defender_card,
                defense_skill,
                target['dodge' if defense == 'dodge' else 'fighting']
                if defender_card is None
                else 0,
                0,
                '近战防御',
                rng,
            )
            _roll_event(work, response_roll, target_id, command.is_private)
            winner = opposed_result(attack, response_roll, defense=defense)
            defenses[target_id] = defenses.get(target_id, 0) + 1
        damaged = None
        damage_profile = profile
        if winner == 'attacker':
            damaged = target
        elif winner == 'defender' and defense == 'fight_back':
            damaged = current
            damage_profile = counter_profile
        if damaged is not None:
            damage_roll = weapon_damage(
                damage_profile['damage'],
                damage_profile['damage_bonus'],
                is_extreme=SUCCESS_LEVELS[attack.details['level']] >= 4,
                is_impaling=damage_profile['is_impaling'],
                is_fight_back=winner == 'defender',
                rng=rng,
            )
            _roll_event(work, damage_roll, damaged['id'], command.is_private)
            armor = damaged.get('armor', 0)
            if damaged['id'] in cards:
                armor = cards[damaged['id']].assets.get('armor', 0)
            _integer(armor, '护甲')
            _damage_target(
                game,
                cards,
                damaged,
                damage_roll.total,
                armor,
                work,
                command.is_private,
                rng,
            )
            _weapon_effects(
                damage_profile,
                damaged,
                damage_roll.total,
                work,
                command.is_private,
                rng,
            )
        _event(
            work,
            'ruling',
            {
                'command': 'combat_attack',
                'reason': command.reason,
                'attacker_id': attacker_id,
                'target_id': target_id,
                'defense': defense,
                'winner': winner,
                'weapon': profile['name'],
                'skill': profile['skill'],
                'skill_value': profile['target'],
                'skill_source': (
                    '主持指明使用角色卡真实技能'
                    if profile.get('skill_ruling')
                    else profile.get('skill_source', 'NPC来源资料')
                ),
            },
            command.is_private,
        )
        progress['next_attack'] = attack_index + 1
        _checkpoint(service, game, cards, work)
        if _is_incapacitated(target) or _is_incapacitated(current):
            break
    _complete_attack(service, game, cards, command, work, action_id)


async def apply_game_command(
    service,
    game: Game,
    cards: dict,
    command: RuleCommand,
    work: dict,
) -> None:
    if command.kind == 'start_combat':
        _start_combat(service, game, cards, command, work)
    elif command.kind == 'end_combat':
        game.mode = 'exploration'
        game.combat = {}
        _event(
            work,
            'ruling',
            {
                'command': 'end_combat',
                'reason': command.reason,
            },
            command.is_private,
        )
    elif command.kind == 'split_groups':
        _split_groups(service, game, command, work)
    elif command.kind == 'combat_attack':
        await _attack(service, game, cards, command, work)
    else:
        raise ValueError(f'不支持的游戏命令：{command.kind}')
