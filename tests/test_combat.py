from copy import deepcopy

import pytest

from aitrpg.application.combat import apply_game_command
from aitrpg.application.combat import combat_turn
from aitrpg.domain.models import Character
from aitrpg.domain.models import Game
from aitrpg.domain.models import PlayerResponse
from aitrpg.domain.models import RuleCommand
from aitrpg.domain.models import Scenario
from aitrpg.domain.models import ScenarioContent
from aitrpg.domain.models import Seat


class FixedDice:
    def __init__(self, values):
        self.values = iter(values)

    def randint(self, minimum, maximum):
        value = next(self.values)
        if not minimum <= value <= maximum:
            raise ValueError('测试骰点超出范围')
        return value


class GameService:
    def __init__(self, defense='dodge', values=()):
        self.response = PlayerResponse(defense=defense)
        self.rng = FixedDice(values)
        self.reactions = []
        self.snapshot = None
        self.module = Scenario(
            id='scenario',
            title='测试模组',
            npcs=[ScenarioContent(id='cultist', title='邪教徒')],
        )

    def scenario(self, game):
        return self.module

    def _group_actor_ids(self, game):
        return [
            seat.actor_id
            for seat in game.seats
            if seat.group_id == game.group_id
        ]

    async def _invoke(self, game, actor_id, purpose, output_type, extra=None):
        self.reactions.append(
            {'actor_id': actor_id, 'purpose': purpose, 'extra': extra}
        )
        return self.response

    def _checkpoint(self, game, cards, work):
        self.snapshot = {
            'game': game.model_dump(mode='json'),
            'cards': {
                key: value.model_dump(mode='json')
                for key, value in cards.items()
            },
            'work': deepcopy(work),
        }


def card(identifier='investigator', dex=60):
    return Character(
        id=identifier,
        actor_id='actor-' + identifier,
        name='调查员',
        attributes={
            'STR': 60,
            'CON': 60,
            'SIZ': 60,
            'DEX': dex,
            'APP': 60,
            'INT': 60,
            'POW': 60,
            'EDU': 60,
        },
        skills={'格斗：斗殴': 60, '闪避': 60, '射击：手枪': 60},
        max_hp=12,
        current_hp=12,
        max_mp=12,
        current_mp=12,
        current_san=60,
    )


def game_for(cards):
    return Game(
        name='测试团',
        scenario_id='scenario',
        scenario_version=1,
        keeper_actor_id='keeper',
        seats=[
            Seat(actor_id=character.actor_id, character_id=character.id)
            for character in cards.values()
        ],
    )


def npc(**overrides):
    return {
        'id': 'cultist',
        'name': '邪教徒',
        'dex': 80,
        'hp': 20,
        'fighting': 60,
        'dodge': 40,
        'damage': '1D3',
        'armor': 0,
        **overrides,
    }


def work():
    return {'events': [], 'has_rolls': False}


async def start(service, game, cards, working, **parameters):
    await apply_game_command(
        service,
        game,
        cards,
        RuleCommand(
            kind='start_combat',
            reason='现场开始战斗',
            parameters={'npcs': [npc()], **parameters},
        ),
        working,
    )


async def test_initiative_uses_real_card_dex_and_readied_firearm():
    slow = card('slow', dex=30)
    slow.weapons = [{'name': '手枪', 'skill': '射击：手枪', 'damage': '1D10'}]
    fast = card('fast', dex=70)
    cards = {slow.id: slow, fast.id: fast}
    game = game_for(cards)
    service = GameService()
    await start(
        service,
        game,
        cards,
        work(),
        readied_guns=['slow'],
        npcs=[npc(dex=60)],
        player_dex={'fast': 999},
    )
    assert [entry['id'] for entry in game.combat['order']] == [
        'slow',
        'fast',
        'cultist',
    ]
    assert game.combat['order'][0]['dex'] == 80
    assert game.combat['order'][1]['dex'] == 70


async def test_npc_requires_source_and_real_missing_statistics():
    cards = {'investigator': card()}
    game = game_for(cards)
    service = GameService()
    with pytest.raises(ValueError, match='来源不存在'):
        await start(service, game, cards, work(), npcs=[npc(id='invented')])
    missing = npc()
    del missing['dodge']
    with pytest.raises(ValueError, match='dodge'):
        await start(service, game, cards, work(), npcs=[missing])
    await start(
        service,
        game,
        cards,
        work(),
        npcs=[npc(id='invented')],
        improvisation='明确新增巡逻者',
    )
    assert game.mode == 'combat'


async def test_player_dodge_choice_overrides_keeper_and_wins_melee_tie():
    cards = {'investigator': card()}
    game = game_for(cards)
    service = GameService(defense='dodge', values=[5, 5, 5, 5])
    working = work()
    await start(service, game, cards, working)
    await apply_game_command(
        service,
        game,
        cards,
        RuleCommand(
            kind='combat_attack',
            reason='近战进攻',
            parameters={
                'target_id': 'investigator',
                'defense': 'fight_back',
            },
        ),
        working,
    )
    assert service.reactions[0]['actor_id'] == 'actor-investigator'
    assert service.reactions[0]['purpose'] == 'reaction'
    assert cards['investigator'].current_hp == 12
    assert working['events'][-1]['data']['winner'] == 'defender'


async def test_fight_back_tie_favors_attack_and_armor_reduces_damage():
    defender = card()
    defender.assets['armor'] = 1
    cards = {defender.id: defender}
    game = game_for(cards)
    service = GameService(defense='fight_back', values=[5, 5, 5, 5, 3])
    working = work()
    await start(service, game, cards, working)
    await apply_game_command(
        service,
        game,
        cards,
        RuleCommand(
            kind='combat_attack',
            reason='邪教徒挥拳',
            parameters={'target_id': defender.id},
        ),
        working,
    )
    assert cards[defender.id].current_hp == 10
    assert working['events'][-1]['data']['winner'] == 'attacker'


async def test_gun_cover_penalty_and_next_attack_forfeit():
    cards = {'investigator': card()}
    game = game_for(cards)
    service = GameService(defense='cover', values=[0, 2, 5, 2, 8])
    working = work()
    await start(
        service, game, cards, working, npcs=[npc(firearms=80, damage='1D10')]
    )
    await apply_game_command(
        service,
        game,
        cards,
        RuleCommand(
            kind='combat_attack',
            reason='邪教徒射击',
            parameters={
                'target_id': 'investigator',
                'mode': 'firearm',
            },
        ),
        working,
    )
    assert cards['investigator'].current_hp == 12
    shots = [
        event['data']
        for event in working['events']
        if event['kind'] == 'check'
    ]
    assert shots[1]['total'] == 85
    assert shots[1]['details']['bonus_dice'] == -1
    game.combat['turn'] = 1
    await apply_game_command(
        service,
        game,
        cards,
        RuleCommand(
            kind='combat_attack',
            reason='调查员尝试还击',
            parameters={'target_id': 'cultist'},
        ),
        working,
    )
    assert working['events'][-1]['data']['details']['attack_forfeited']
    assert game.combat['order'][0]['hp'] == 20


async def test_outnumbered_applies_after_first_defense_in_same_round():
    cards = {'investigator': card()}
    game = game_for(cards)
    service = GameService(defense='dodge', values=[0, 7, 0, 8, 0, 1])
    working = work()
    await start(service, game, cards, working)
    game.combat['defenses']['investigator'] = 1
    await apply_game_command(
        service,
        game,
        cards,
        RuleCommand(
            kind='combat_attack',
            reason='第二名敌人进攻',
            parameters={'target_id': 'investigator'},
        ),
        working,
    )
    check = next(
        event['data']
        for event in working['events']
        if event['kind'] == 'check'
    )
    assert check['details']['bonus_dice'] == 1


async def test_cannot_attack_out_of_turn_or_repeat_same_action():
    cards = {'investigator': card()}
    game = game_for(cards)
    service = GameService(defense='dodge', values=[0, 8, 0, 2])
    working = work()
    await start(service, game, cards, working)
    with pytest.raises(ValueError, match='当前DEX'):
        await apply_game_command(
            service,
            game,
            cards,
            RuleCommand(
                kind='combat_attack',
                reason='越序攻击',
                parameters={
                    'attacker_id': 'investigator',
                    'target_id': 'cultist',
                },
            ),
            working,
        )
    attack = RuleCommand(
        kind='combat_attack',
        reason='轮到邪教徒',
        parameters={'target_id': 'investigator'},
    )
    await apply_game_command(service, game, cards, attack, working)
    events_before = len(working['events'])
    await apply_game_command(service, game, cards, attack, working)
    assert len(working['events']) == events_before
    with pytest.raises(ValueError, match='已完成'):
        await apply_game_command(
            service,
            game,
            cards,
            RuleCommand(
                kind='combat_attack',
                reason='同一回合另一条重复攻击',
                parameters={'target_id': 'investigator'},
            ),
            working,
        )


async def test_split_groups_changes_only_memberships_and_rejects_omission():
    cards = {'first': card('first'), 'second': card('second')}
    game = game_for(cards)
    service = GameService()
    with pytest.raises(ValueError, match='每个调查员'):
        await apply_game_command(
            service,
            game,
            cards,
            RuleCommand(
                kind='split_groups',
                reason='分队',
                parameters={'groups': {'A': ['actor-first']}},
            ),
            work(),
        )
    await apply_game_command(
        service,
        game,
        cards,
        RuleCommand(
            kind='split_groups',
            reason='两路调查',
            parameters={
                'groups': {'A': ['actor-first'], 'B': ['actor-second']},
            },
        ),
        work(),
    )
    assert [(seat.actor_id, seat.group_id) for seat in game.seats] == [
        ('actor-first', 'A'),
        ('actor-second', 'B'),
    ]


def test_combat_turn_skips_defeated_npc():
    game = game_for({'investigator': card()})
    game.combat = {
        'turn': 0,
        'round': 1,
        'order': [
            {'id': 'cultist', 'conditions': ['dead']},
            {'id': 'investigator', 'actor_id': 'actor-investigator'},
        ],
    }
    assert combat_turn(game)['id'] == 'investigator'
    assert game.combat['turn'] == 1


async def test_extreme_attack_updates_player_major_wound_and_consciousness():
    cards = {'investigator': card()}
    game = game_for(cards)
    service = GameService(defense='dodge', values=[0, 1, 0, 8, 0, 8])
    working = work()
    await start(service, game, cards, working, npcs=[npc(damage='1D8')])
    await apply_game_command(
        service,
        game,
        cards,
        RuleCommand(
            kind='combat_attack',
            reason='极难成功的砍击',
            parameters={'target_id': 'investigator'},
        ),
        working,
    )
    assert cards['investigator'].current_hp == 4
    assert 'major_wound' in cards['investigator'].conditions
    assert 'unconscious' in cards['investigator'].conditions


async def test_critical_fight_back_still_uses_ordinary_damage():
    cards = {'investigator': card()}
    game = game_for(cards)
    service = GameService(defense='fight_back', values=[0, 5, 1, 0, 2])
    working = work()
    await start(service, game, cards, working)
    await apply_game_command(
        service,
        game,
        cards,
        RuleCommand(
            kind='combat_attack',
            reason='反击',
            parameters={'target_id': 'investigator'},
        ),
        working,
    )
    assert game.combat['order'][0]['hp'] == 18
    assert cards['investigator'].current_hp == 12


async def test_multiple_melee_attacks_request_each_player_defense():
    cards = {'investigator': card()}
    game = game_for(cards)
    service = GameService(defense='dodge', values=[0, 8, 0, 2, 0, 7, 4, 0, 2])
    working = work()
    await start(service, game, cards, working, npcs=[npc(attacks_per_round=2)])
    await apply_game_command(
        service,
        game,
        cards,
        RuleCommand(
            kind='combat_attack',
            reason='两次攻击',
            parameters={'target_id': 'investigator', 'shots': 2},
        ),
        working,
    )
    assert len(service.reactions) == 2
    assert (
        service.reactions[0]['extra']['reaction_key']
        != (service.reactions[1]['extra']['reaction_key'])
    )
    attacks = [
        event['data']
        for event in working['events']
        if event['kind'] == 'check'
        and event['data']['character_id'] == 'cultist'
    ]
    assert attacks[0]['details']['bonus_dice'] == 0
    assert attacks[1]['details']['bonus_dice'] == 1
    assert cards['investigator'].current_hp == 12


async def test_checkpoint_resumes_second_attack_without_replaying_first_hit():
    class InterruptedService(GameService):
        async def _invoke(self, *args, **kwargs):
            response = await super()._invoke(*args, **kwargs)
            if len(self.reactions) == 2:
                raise TimeoutError('第二次防御断线')
            return response

    cards = {'investigator': card()}
    game = game_for(cards)
    service = InterruptedService(defense='dodge', values=[5, 5, 0, 8, 2])
    working = work()
    await start(service, game, cards, working, npcs=[npc(attacks_per_round=2)])
    attack = RuleCommand(
        kind='combat_attack',
        reason='连续两击',
        parameters={'target_id': 'investigator', 'shots': 2},
    )
    with pytest.raises(TimeoutError):
        await apply_game_command(service, game, cards, attack, working)
    assert cards['investigator'].current_hp == 10
    assert (
        service.reactions[-1]['extra']['pending_character']['current_hp'] == 10
    )
    snapshot = service.snapshot
    restored_game = Game.model_validate(snapshot['game'])
    restored_cards = {
        key: Character.model_validate(value)
        for key, value in snapshot['cards'].items()
    }
    restored_work = deepcopy(snapshot['work'])
    resumed = GameService(defense='dodge', values=[0, 8, 7, 0, 2])
    await apply_game_command(
        resumed, restored_game, restored_cards, attack, restored_work
    )
    assert len(resumed.reactions) == 1
    assert restored_cards['investigator'].current_hp == 10
    assert (
        sum(
            event['kind'] == 'check' and event['data']['total'] == 55
            for event in restored_work['events']
        )
        == 1
    )
    events_before = len(restored_work['events'])
    await apply_game_command(
        resumed, restored_game, restored_cards, attack, restored_work
    )
    assert len(restored_work['events']) == events_before
