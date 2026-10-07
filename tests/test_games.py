import asyncio
import inspect
import json
import os
import shutil
import subprocess
import sys
from copy import deepcopy

import pytest

from aitrpg.adapters.process import is_process_alive
from aitrpg.adapters.providers import Generation
from aitrpg.adapters.storage import ConflictError
from aitrpg.application.platform import Platform
from aitrpg.config import Settings
from aitrpg.domain.models import Actor
from aitrpg.domain.models import Character
from aitrpg.domain.models import CheckRequest
from aitrpg.domain.models import KeeperResponse
from aitrpg.domain.models import PlayerResponse
from aitrpg.domain.models import Provider
from aitrpg.domain.models import RuleCommand
from aitrpg.domain.models import Scenario
from aitrpg.domain.models import ScenarioAsset
from aitrpg.domain.models import ScenarioContent
from aitrpg.domain.models import ScenarioRole
from aitrpg.domain.models import ScenarioScene
from aitrpg.domain.models import SourceBlock
from aitrpg.domain.rules import apply_command
from aitrpg.domain.rules import skill_check


class ScriptedClient:
    def __init__(self, handler=None, usage=10):
        self.handler = handler or self.default
        self.usage = usage
        self.calls = []

    async def generate(self, provider, system, context, output_type):
        call = {
            'actor': provider.name,
            'task': context['task'],
            'context': deepcopy(context),
        }
        self.calls.append(call)
        value = self.handler(call)
        if inspect.isawaitable(value):
            value = await value
        if type(value) is not output_type and hasattr(value, 'model_dump'):
            value = value.model_dump(mode='json')
        return Generation(
            value=output_type.model_validate(value), usage=self.usage
        )

    @staticmethod
    def default(call):
        if call['task'] == 'player':
            return PlayerResponse(speech='检查设备', intent='观察标签')
        if call['task'] == 'keeper_opening':
            return KeeperResponse(
                narration='开始调查', invite_actor_ids=['player-0']
            )
        return KeeperResponse(narration='现场保持安静')


def setup_platform(
    tmp_path,
    client=None,
    grouped=True,
    secret_scenario=False,
    scenario_transform=None,
):
    platform = Platform(
        Settings(data_dir=tmp_path / 'platform'), client or ScriptedClient()
    )
    scenario = platform.scenarios.seed_demo()
    if secret_scenario:
        scenario.roles = [
            ScenarioRole(
                id=f'role-{index}',
                name=f'公开席位{index}',
                secret_text=f'PRIVATE_ROLE_{index}',
            )
            for index in range(3)
        ]
        scenario.source_blocks = [
            SourceBlock(
                id='source-keeper',
                file='secret-book.docx',
                locator='秘密段',
                text='arrival KEEPER_SOURCE_SECRET',
            )
        ]
        scenario.scenes[0].source_ids = ['source-keeper']
        scenario.scenes[0].keeper_text = 'KEEPER_SCENE_SECRET'
        scenario.scenes[0].public_text = 'PUBLIC_SCENE_A'
        scenario.scenes[1].public_text = 'PUBLIC_SCENE_B'
        source = platform.scenarios.asset_for_viewer(
            scenario, scenario.assets[0].id, is_keeper=True
        )
        destination = platform.scenarios.scenarios_dir / scenario.id
        destination = destination / 'assets' / 'private-role.png'
        shutil.copyfile(source, destination)
        scenario.assets.append(
            ScenarioAsset(
                id='private-image',
                name='秘密身份照片',
                path='assets/private-role.png',
                mime_type='image/png',
                visibility='roles',
                role_ids=['role-0'],
            )
        )
        for collection in (
            'scenes',
            'roles',
            'npcs',
            'clues',
            'handouts',
            'endings',
            'assets',
        ):
            for item in getattr(scenario, collection):
                item.source_ids = ['source-keeper']
        scenario = platform.scenarios.save(scenario)
        scenario = platform.scenarios.approve(scenario.id)
    if scenario_transform:
        scenario = scenario_transform(platform, scenario)
    for actor_id in ('keeper', 'player-0', 'player-1', 'player-2'):
        provider = platform.save_provider(
            Provider(
                id='provider-' + actor_id,
                name=actor_id,
                base_url='https://example.test',
            )
        )
        platform.save_actor(
            Actor(id=actor_id, name=actor_id, provider_id=provider.id)
        )
    cards = []
    seats = []
    for index in range(3):
        card = Character(
            id=f'card-{index}',
            actor_id=f'player-{index}',
            name=f'调查员{index}',
            attributes={
                name: 60
                for name in (
                    'STR',
                    'CON',
                    'SIZ',
                    'DEX',
                    'APP',
                    'INT',
                    'POW',
                    'EDU',
                )
            },
            skills={
                '侦查': 80,
                '信用评级': 60,
                '克苏鲁神话': 20,
                '格斗：斗殴': 60,
                '闪避': 60,
            },
            max_hp=12,
            current_hp=12,
            max_mp=12,
            current_mp=12,
            current_san=60,
            background={'life_story': f'CARD_SECRET_{index}'},
        )
        platform.store.put('character', card.id, card.model_dump(mode='json'))
        cards.append(card)
        seats.append(
            {
                'actor_id': card.actor_id,
                'character_id': card.id,
                'group_id': chr(ord('A') + index) if grouped else 'main',
                'role_id': f'role-{index}' if secret_scenario else None,
            }
        )
    game = platform.create_game('测试团', scenario.id, 'keeper', seats)
    if grouped:
        game.group_id = 'A'
        platform.store.put('game', game.id, game.model_dump(mode='json'))
    return platform, game, cards, scenario


class FixedDice:
    def __init__(self, values):
        self.values = iter(values)

    def randint(self, minimum, maximum):
        return next(self.values)


def install_fixed_check(monkeypatch):
    rolls = []

    def fixed(*args, **kwargs):
        roll = skill_check(*args, **kwargs, rng=FixedDice([2, 4]))
        rolls.append(roll)
        return roll

    monkeypatch.setattr('aitrpg.application.games.skill_check', fixed)
    return rolls


async def test_concurrent_games_cannot_take_already_used_characters(tmp_path):
    platform, game, cards, scenario = setup_platform(tmp_path)
    await platform.intervene(game.id, '释放原测试占用')
    for card in cards:
        record = platform.store.get('character', card.id)
        record['locked_game_id'] = None
        platform.store.put('character', card.id, record)
    seats = [
        {'actor_id': card.actor_id, 'character_id': card.id} for card in cards
    ]
    results = await asyncio.gather(
        *[
            asyncio.to_thread(
                platform.create_game, name, scenario.id, 'keeper', seats
            )
            for name in ('并发局一', '并发局二')
        ],
        return_exceptions=True,
    )
    successful = [
        result for result in results if not isinstance(result, Exception)
    ]
    failures = [result for result in results if isinstance(result, Exception)]
    assert len(successful) == 1
    assert len(failures) == 1
    assert '占用' in str(failures[0])
    assert {
        platform.characters.get(card.id).locked_game_id for card in cards
    } == {
        successful[0].id,
    }


async def test_complete_node_commits_rules_events_and_reuses_feedback_command(
    tmp_path,
    monkeypatch,
):
    damage = RuleCommand(
        kind='damage',
        character_id='card-0',
        parameters={'amount': 2, 'armor': 0},
        reason='擦伤',
    )

    def handler(call):
        if call['task'] == 'keeper_resolution':
            return KeeperResponse(
                narration='等待真实骰点',
                checks=[CheckRequest(character_id='card-0', skill='侦查')],
                commands=[damage],
            )
        if call['task'] == 'keeper_feedback':
            return KeeperResponse(
                narration='查到了标签并擦伤',
                commands=[
                    damage.model_copy(update={'reason': '回述同一擦伤'}),
                ],
            )
        return ScriptedClient.default(call)

    client = ScriptedClient(handler)
    platform, game, _, _ = setup_platform(tmp_path, client)
    rolls = install_fixed_check(monkeypatch)
    result = await platform.step_game(game.id)
    assert not result.last_error
    assert result.node_count == 1
    assert platform.characters.get('card-0').current_hp == 10
    assert platform.characters.get('card-0').skill_marks == ['侦查']
    assert [call['task'] for call in client.calls] == [
        'keeper_opening',
        'player',
        'keeper_resolution',
        'keeper_feedback',
    ]
    events = platform.store.events(game.id)
    assert len([event for event in events if event['kind'] == 'check']) == 1
    assert len([event for event in events if event['kind'] == 'ruling']) == 1
    assert events[-1]['data']['text'] == '查到了标签并擦伤'
    assert events[-3]['data']['id'] == rolls[0].id


async def test_feedback_defaults_and_duplicates_do_not_repeat_damage(
    tmp_path,
    monkeypatch,
):
    def handler(call):
        if call['task'] == 'keeper_resolution':
            return KeeperResponse(
                checks=[CheckRequest(character_id='card-0', skill='侦查')],
                commands=[
                    RuleCommand(
                        kind='damage',
                        character_id='card-0',
                        parameters={'amount': 1, 'armor': 0},
                        reason='擦伤',
                    )
                ],
            )
        if call['task'] == 'keeper_feedback':
            repeat = RuleCommand(
                kind='damage',
                character_id='card-0',
                parameters={'amount': 1},
                reason='回述擦伤',
            )
            return KeeperResponse(
                narration='同一擦伤仅扣一次', commands=[repeat, repeat]
            )
        return ScriptedClient.default(call)

    platform, game, _, _ = setup_platform(tmp_path, ScriptedClient(handler))
    install_fixed_check(monkeypatch)
    result = await platform.step_game(game.id)
    assert not result.last_error
    assert platform.characters.get('card-0').current_hp == 11


async def test_pause_restart_reuses_prepared_roll_and_commits_once(
    tmp_path,
    monkeypatch,
):
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(call):
        if call['task'] == 'keeper_resolution':
            return KeeperResponse(
                checks=[CheckRequest(character_id='card-0', skill='侦查')],
                commands=[
                    RuleCommand(
                        kind='damage',
                        character_id='card-0',
                        parameters={'amount': 2},
                        reason='擦伤',
                    )
                ],
            )
        if call['task'] == 'keeper_feedback':
            started.set()
            await release.wait()
            return KeeperResponse(narration='回填完成')
        return ScriptedClient.default(call)

    client = ScriptedClient(handler)
    platform, game, _, _ = setup_platform(tmp_path, client)
    rolls = install_fixed_check(monkeypatch)
    pending = asyncio.create_task(platform.step_game(game.id))
    await asyncio.wait_for(started.wait(), timeout=2)
    assert platform.characters.get('card-0').current_hp == 12
    await platform.pause_game(game.id)
    with pytest.raises(asyncio.CancelledError):
        await pending
    release.set()
    restored = Platform(Settings(data_dir=tmp_path / 'platform'), client)
    result = await restored.step_game(game.id)
    assert not result.last_error
    assert result.node_count == 1
    assert len(rolls) == 1
    assert restored.characters.get('card-0').current_hp == 10
    assert (
        len(
            [
                event
                for event in restored.store.events(game.id)
                if event['kind'] == 'check'
            ]
        )
        == 1
    )


async def test_api_invitation_reuse_does_not_call_model_again(tmp_path):
    client = ScriptedClient()
    platform, game, _, _ = setup_platform(tmp_path, client)
    first = await platform.games._invoke(
        game, 'player-0', 'player', PlayerResponse
    )
    second = await platform.games._invoke(
        game, 'player-0', 'player', PlayerResponse
    )
    assert first.intent == second.intent == '观察标签'
    assert len(client.calls) == 1


async def test_mcp_invitation_replay_accepts_same_reply_only_once(tmp_path):
    platform, game, _, _ = setup_platform(tmp_path)
    platform.save_actor(
        Actor(id='player-0', name='被动玩家', connection='mcp')
    )
    token = platform.issue_agent_token(game.id, 'player-0')
    pending = asyncio.create_task(
        platform.games._invoke(
            game,
            'player-0',
            'player',
            PlayerResponse,
        )
    )
    await asyncio.sleep(0)
    invitation = await platform.games.wait_for_invitation(token, 0)
    first = platform.games.submit(
        invitation['id'],
        'submission-one',
        {'speech': '已收到', 'intent': '观察'},
        'player-0',
    )
    replay = platform.games.submit(
        invitation['id'],
        'submission-two',
        {'speech': '已收到', 'intent': '观察'},
        'player-0',
    )
    assert first == replay
    assert (await pending).intent == '观察'
    repeated = await platform.games._invoke(
        game, 'player-0', 'player', PlayerResponse
    )
    assert repeated.intent == '观察'
    with pytest.raises(ValueError, match='不同的回复'):
        platform.games.submit(
            invitation['id'],
            'submission-three',
            {'intent': '更换行动'},
            'player-0',
        )


async def test_intervention_invalidates_in_flight_api_response(tmp_path):
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(call):
        started.set()
        await release.wait()
        return PlayerResponse(intent='过时的行动')

    platform, game, _, _ = setup_platform(tmp_path, ScriptedClient(handler))
    pending = asyncio.create_task(
        platform.games._invoke(
            game,
            'player-0',
            'player',
            PlayerResponse,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=2)
    corrected = await platform.intervene(game.id, '现场已经改变')
    release.set()
    with pytest.raises(ConflictError):
        await pending
    assert platform.get_game(game.id).revision == corrected.revision
    assert platform.characters.get('card-0').current_hp == 12
    assert not any(
        event['kind'] == 'player' for event in platform.store.events(game.id)
    )


def test_group_role_source_and_private_image_remain_isolated(tmp_path):
    platform, game, _, _ = setup_platform(tmp_path, secret_scenario=True)
    game.scheduler['group_scenes'] = {'A': 'arrival', 'B': 'control'}
    game.combat = {
        'order': [
            {
                'id': 'hidden-npc',
                'name': '陌生人',
                'dex': 50,
                'keeper_note': 'NPC_SECRET',
            }
        ]
    }
    platform.store.put('game', game.id, game.model_dump(mode='json'))
    with platform.store.transaction() as transaction:
        transaction.append_event(
            game.id,
            'narration',
            {'text': 'GROUP_A_DISCOVERY'},
            actor_ids=['player-0'],
        )
    viewer = platform.games.view(game.id, 'player-1')
    text = json.dumps(viewer, ensure_ascii=False)
    assert 'PRIVATE_ROLE_1' in text
    assert 'PUBLIC_SCENE_B' not in text
    assert viewer['game']['scene_id'] == 'control'
    for secret in (
        'PRIVATE_ROLE_0',
        'PRIVATE_ROLE_2',
        'KEEPER_SOURCE_SECRET',
        'KEEPER_SCENE_SECRET',
        'CARD_SECRET_0',
        'NPC_SECRET',
        'GROUP_A_DISCOVERY',
        'PUBLIC_SCENE_A',
        'private-role.png',
    ):
        assert secret not in text
    context = json.dumps(
        platform.games.context(game, 'player-0', 'player'), ensure_ascii=False
    )
    assert 'PRIVATE_ROLE_0' in context
    assert 'secret-book.docx' not in context
    assert 'private-role.png' not in context
    with pytest.raises(PermissionError):
        platform.games.asset_path(game.id, 'private-image', 'player-1')
    assert platform.games.asset_path(
        game.id, 'private-image', 'player-0'
    ).is_file()


async def test_ending_releases_cards_and_keeps_continuous_life(
    tmp_path,
):
    def handler(call):
        if call['task'] == 'keeper_resolution':
            return KeeperResponse(
                narration='平安结束',
                is_finished=True,
                ending='离开了中继站',
                advance_days=3,
                commands=[
                    RuleCommand(
                        kind='damage',
                        character_id='card-0',
                        parameters={'amount': 5},
                        reason='擦伤',
                    )
                ],
            )
        return ScriptedClient.default(call)

    platform, game, cards, scenario = setup_platform(
        tmp_path, ScriptedClient(handler)
    )
    result = await platform.step_game(game.id)
    assert result.status == 'ended'
    character = platform.characters.get('card-0')
    assert character.locked_game_id is None
    assert character.current_hp == 10
    assert character.background['life_story'] == 'CARD_SECRET_0'
    assert character.experiences == ['测试团：离开了中继站']
    assert character.allocations['runtime']['lifetime_day'] == 3
    next_game = platform.create_game(
        '续团',
        scenario.id,
        'keeper',
        [
            {'actor_id': card.actor_id, 'character_id': card.id}
            for card in cards
        ],
    )
    assert next_game.scheduler['base_day'] == 3
    assert platform.characters.get('card-0').current_hp == 10


async def test_start_combat_does_not_consume_first_dex_action(tmp_path):
    def handler(call):
        return KeeperResponse(
            narration='遭遇袭击',
            commands=[
                RuleCommand(
                    kind='start_combat',
                    reason='原创敌人出现',
                    parameters={
                        'npcs': [
                            {
                                'id': 'demo-enemy',
                                'name': '演示敌人',
                                'dex': 30,
                                'hp': 10,
                                'fighting': 30,
                                'dodge': 20,
                                'damage': '1D3',
                                'armor': 0,
                            }
                        ],
                        'improvisation': '为原创演示增加对立者',
                    },
                )
            ],
        )

    platform, game, _, _ = setup_platform(tmp_path, ScriptedClient(handler))
    result = await platform.step_game(game.id)
    assert not result.last_error
    assert result.mode == 'combat'
    assert platform.games._combat_turn(result)['id'] == 'card-0'
    assert result.combat['round'] == 1


@pytest.mark.parametrize('mode', ['ordinary', 'all_players', 'combat'])
async def test_player_timeout_skip_or_pause_policy(tmp_path, mode):
    def handler(call):
        if call['task'] == 'player':
            raise TimeoutError('测试玩家离线')
        if call['task'] == 'keeper_opening':
            return KeeperResponse(
                narration='等待玩家',
                invite_actor_ids=['player-0'],
                is_all_players=mode == 'all_players',
            )
        return KeeperResponse(narration='跳过离线玩家')

    platform, game, _, _ = setup_platform(tmp_path, ScriptedClient(handler))
    if mode == 'combat':
        game.mode = 'combat'
        game.last_keeper = KeeperResponse(narration='已有开场').model_dump()
        game.combat = {
            'order': [{'id': 'card-0', 'actor_id': 'player-0', 'dex': 60}],
            'turn': 0,
            'round': 1,
        }
        platform.store.put('game', game.id, game.model_dump(mode='json'))
    result = await platform.step_game(game.id)
    if mode == 'ordinary':
        assert result.node_count == 1
        assert not result.last_error
        assert any(
            event['data'].get('text') == '玩家回应超时'
            for event in platform.store.events(game.id)
        )
    else:
        assert result.node_count == 0
        assert result.status == 'paused'
        assert '离线' in result.last_error


async def test_credit_and_mythos_success_do_not_create_growth_marks(
    tmp_path,
    monkeypatch,
):
    def handler(call):
        if call['task'] == 'keeper_resolution':
            return KeeperResponse(
                checks=[
                    CheckRequest(character_id='card-0', skill='信用评级'),
                    CheckRequest(character_id='card-0', skill='克苏鲁神话'),
                ]
            )
        return ScriptedClient.default(call)

    platform, game, _, _ = setup_platform(tmp_path, ScriptedClient(handler))

    def successful(*args, **kwargs):
        return skill_check(*args, **kwargs, rng=FixedDice([1, 0]))

    monkeypatch.setattr('aitrpg.application.games.skill_check', successful)
    result = await platform.step_game(game.id)
    assert not result.last_error
    assert platform.characters.get('card-0').skill_marks == []


async def test_token_limit_stops_further_calls_within_node(tmp_path):
    client = ScriptedClient(usage=10)
    platform, game, _, _ = setup_platform(tmp_path, client)
    game.token_usage = 990
    game.budget_tokens = 1000
    platform.store.put('game', game.id, game.model_dump(mode='json'))
    result = await platform.step_game(game.id)
    assert result.status == 'paused'
    assert len(client.calls) == 1
    assert result.token_usage == 1000
    assert result.node_count == 0


async def test_switching_group_does_not_redirect_previous_player_speech(
    tmp_path,
):
    def handler(call):
        if call['task'] == 'player':
            return PlayerResponse(
                speech='GROUP_A_PLAYER_SECRET', intent='A队观察'
            )
        if call['task'] == 'keeper_resolution':
            return KeeperResponse(narration='接下来切换B队', group_id='B')
        return ScriptedClient.default(call)

    platform, game, _, _ = setup_platform(tmp_path, ScriptedClient(handler))
    result = await platform.step_game(game.id)
    assert not result.last_error
    assert result.group_id == 'B'
    player_a = json.dumps(
        platform.games.view(game.id, 'player-0'), ensure_ascii=False
    )
    player_b = json.dumps(
        platform.games.view(game.id, 'player-1'), ensure_ascii=False
    )
    assert 'GROUP_A_PLAYER_SECRET' in player_a
    assert 'GROUP_A_PLAYER_SECRET' not in player_b


async def test_invalid_feedback_keeps_character_and_rule_events_uncommitted(
    tmp_path,
    monkeypatch,
):
    def handler(call):
        if call['task'] == 'keeper_resolution':
            return KeeperResponse(
                checks=[CheckRequest(character_id='card-0', skill='侦查')],
                commands=[
                    RuleCommand(
                        kind='damage',
                        character_id='card-0',
                        parameters={'amount': 2},
                        reason='擦伤',
                    )
                ],
            )
        if call['task'] == 'keeper_feedback':
            return KeeperResponse(
                narration='不合法的场景切换', scene_id='missing-scene'
            )
        return ScriptedClient.default(call)

    platform, game, _, _ = setup_platform(tmp_path, ScriptedClient(handler))
    install_fixed_check(monkeypatch)
    result = await platform.step_game(game.id)
    assert '不存在' in result.last_error
    assert result.node_count == 0
    assert platform.characters.get('card-0').current_hp == 12
    assert not any(
        event['kind'] in ('check', 'ruling')
        for event in platform.store.events(game.id)
    )


async def test_real_combat_player_selects_card_weapon_for_fight_back(tmp_path):
    def handler(call):
        if call['task'] == 'reaction':
            return PlayerResponse(defense='fight_back', defense_weapon_index=0)
        if call['task'] == 'keeper_resolution':
            return KeeperResponse(
                commands=[
                    RuleCommand(
                        kind='combat_attack',
                        reason='NPC主动攻击',
                        parameters={
                            'attacker_id': 'enemy',
                            'target_id': 'card-0',
                        },
                    )
                ]
            )
        return KeeperResponse(narration='使用佩剑反击')

    client = ScriptedClient(handler)
    platform, game, _, _ = setup_platform(tmp_path, client)
    player = platform.characters.get('card-0')
    player.skills['格斗：斗殴'] = 1
    player.skills['格斗：剑'] = 80
    player.weapons = [
        {
            'name': '佩剑',
            'skill': '格斗：剑',
            'damage': '1D8',
            'is_impaling': True,
        }
    ]
    platform.store.put('character', player.id, player.model_dump(mode='json'))
    game.mode = 'combat'
    game.last_keeper = KeeperResponse(narration='已有战斗开场').model_dump()
    game.combat = {
        'turn': 0,
        'round': 1,
        'defenses': {},
        'order': [
            {
                'id': 'enemy',
                'name': '原创敌人',
                'dex': 80,
                'hp': 20,
                'max_hp': 20,
                'fighting': 60,
                'dodge': 20,
                'damage': '1D3',
                'armor': 0,
                'conditions': [],
            },
            {
                'id': player.id,
                'actor_id': player.actor_id,
                'dex': 60,
                'conditions': [],
            },
        ],
    }
    platform.store.put('game', game.id, game.model_dump(mode='json'))
    platform.games.rng = FixedDice([5, 5, 0, 3, 3])
    result = await platform.step_game(game.id)
    assert not result.last_error
    assert result.combat['order'][0]['hp'] == 17
    assert platform.characters.get('card-0').current_hp == 12
    assert any(call['task'] == 'reaction' for call in client.calls)


async def test_successful_legal_push_records_skill_growth(
    tmp_path, monkeypatch
):
    def handler(call):
        if call['task'] == 'keeper_resolution':
            return KeeperResponse(
                checks=[
                    CheckRequest(
                        character_id='card-0',
                        skill='侦查',
                        is_pushed=True,
                        pushed_from_id='prior-failure',
                    )
                ]
            )
        return ScriptedClient.default(call)

    platform, game, _, _ = setup_platform(tmp_path, ScriptedClient(handler))
    with platform.store.transaction() as transaction:
        transaction.append_event(
            game.id,
            'check',
            {
                'id': 'prior-failure',
                'character_id': 'card-0',
                'scene_id': game.scene_id,
                'pushed_from_id': None,
                'details': {
                    'skill': '侦查',
                    'is_success': False,
                    'level': 'failure',
                },
            },
            actor_ids=['player-0'],
        )
    install_fixed_check(monkeypatch)
    result = await platform.step_game(game.id)
    assert not result.last_error
    assert platform.characters.get('card-0').skill_marks == ['侦查']


async def test_permanently_insane_player_is_not_invited_to_act(tmp_path):
    client = ScriptedClient()
    platform, game, _, _ = setup_platform(tmp_path, client)
    retired = platform.store.get('character', 'card-0')
    retired['current_san'] = 0
    retired['conditions'] = ['permanent_insanity']
    platform.store.put('character', 'card-0', retired)
    result = await platform.step_game(game.id)
    assert not result.last_error
    assert result.node_count == 1
    assert not any(
        call['actor'] == 'player-0' and call['task'] == 'player'
        for call in client.calls
    )


async def test_keeper_private_recipient_does_not_forward_player_input(
    tmp_path,
):
    def handler(call):
        if call['task'] == 'player':
            return PlayerResponse(
                speech='GROUP_A_SOURCE_ONLY', intent='A队私密行动'
            )
        if call['task'] == 'keeper_resolution':
            return KeeperResponse(
                narration='PRIVATE_GM_TO_B', recipient_actor_ids=['player-1']
            )
        return ScriptedClient.default(call)

    platform, game, _, _ = setup_platform(tmp_path, ScriptedClient(handler))
    result = await platform.step_game(game.id)
    assert not result.last_error
    viewer_a = json.dumps(
        platform.games.view(game.id, 'player-0'), ensure_ascii=False
    )
    viewer_b = json.dumps(
        platform.games.view(game.id, 'player-1'), ensure_ascii=False
    )
    assert 'GROUP_A_SOURCE_ONLY' in viewer_a
    assert 'GROUP_A_SOURCE_ONLY' not in viewer_b
    assert 'PRIVATE_GM_TO_B' not in viewer_a
    assert 'PRIVATE_GM_TO_B' in viewer_b


async def test_switching_group_restores_its_own_saved_scene(tmp_path):
    def handler(call):
        if call['task'] == 'keeper_resolution':
            return KeeperResponse(narration='继续B队原有调查', group_id='B')
        return ScriptedClient.default(call)

    platform, game, _, _ = setup_platform(tmp_path, ScriptedClient(handler))
    game.scheduler['group_scenes'] = {'A': 'arrival', 'B': 'control'}
    platform.store.put('game', game.id, game.model_dump(mode='json'))
    result = await platform.step_game(game.id)
    assert not result.last_error
    assert result.group_id == 'B'
    assert result.scene_id == 'control'
    viewer_a = platform.games.view(game.id, 'player-0')
    viewer_b = platform.games.view(game.id, 'player-1')
    assert viewer_a['scenario']['scenes'][0]['id'] == 'arrival'
    assert viewer_b['scenario']['scenes'][0]['id'] == 'control'


async def test_pause_during_reaction_resumes_preceding_check_without_reroll(
    tmp_path,
    monkeypatch,
):
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(call):
        if call['task'] == 'keeper_resolution':
            return KeeperResponse(
                checks=[CheckRequest(character_id='card-0', skill='侦查')],
                commands=[
                    RuleCommand(
                        kind='combat_attack',
                        reason='敌人攻击',
                        parameters={
                            'attacker_id': 'enemy',
                            'target_id': 'card-0',
                        },
                    )
                ],
            )
        if call['task'] == 'reaction':
            started.set()
            await release.wait()
            return PlayerResponse(defense='dodge')
        return KeeperResponse(narration='根据真实结果继续')

    client = ScriptedClient(handler)
    platform, game, _, _ = setup_platform(tmp_path, client)
    game.mode = 'combat'
    game.last_keeper = KeeperResponse(narration='已有战斗开场').model_dump()
    game.combat = {
        'turn': 0,
        'round': 1,
        'defenses': {},
        'order': [
            {
                'id': 'enemy',
                'name': '原创敌人',
                'dex': 80,
                'hp': 20,
                'max_hp': 20,
                'fighting': 60,
                'dodge': 20,
                'damage': '1D3',
                'armor': 0,
                'conditions': [],
            },
            {
                'id': 'card-0',
                'actor_id': 'player-0',
                'dex': 60,
                'conditions': [],
            },
        ],
    }
    platform.store.put('game', game.id, game.model_dump(mode='json'))
    rolls = install_fixed_check(monkeypatch)
    pending = asyncio.create_task(platform.step_game(game.id))
    await asyncio.wait_for(started.wait(), timeout=2)
    await platform.pause_game(game.id)
    with pytest.raises(asyncio.CancelledError):
        await pending
    release.set()
    restored = Platform(Settings(data_dir=tmp_path / 'platform'), client)
    restored.games.rng = FixedDice([0, 8, 0, 2])
    result = await restored.step_game(game.id)
    assert not result.last_error
    assert result.node_count == 1
    assert len(rolls) == 1
    events = restored.store.events(game.id)
    assert (
        sum(
            event['kind'] == 'check'
            and event['data'].get('details', {}).get('skill') == '侦查'
            for event in events
        )
        == 1
    )
    assert any(
        event['kind'] == 'ruling'
        and event['data'].get('command') == 'combat_attack'
        for event in events
    )


@pytest.mark.parametrize('visibility', ['keeper', 'public'])
async def test_private_intent_is_hidden_from_other_investigators(
    tmp_path, visibility
):
    def handler(call):
        if call['task'] == 'player':
            return PlayerResponse(
                speech='这句是公开台词',
                intent='SECRET_TACTICAL_INTENT',
                intent_visibility=visibility,
            )
        return ScriptedClient.default(call)

    platform, game, _, _ = setup_platform(
        tmp_path, ScriptedClient(handler), grouped=False
    )
    await platform.step_game(game.id)
    submitted = next(
        event['data']['actor_id']
        for event in platform.store.events(game.id)
        if event['kind'] == 'player'
    )
    other = next(
        actor
        for actor in ('player-0', 'player-1', 'player-2')
        if actor != submitted
    )
    own_view = json.dumps(
        platform.games.view(game.id, submitted), ensure_ascii=False
    )
    other_view = json.dumps(
        platform.games.view(game.id, other), ensure_ascii=False
    )
    keeper_view = json.dumps(
        platform.games.view(game.id, 'keeper'), ensure_ascii=False
    )
    assert 'SECRET_TACTICAL_INTENT' in own_view
    assert 'SECRET_TACTICAL_INTENT' in keeper_view
    assert ('SECRET_TACTICAL_INTENT' in other_view) == (visibility == 'public')
    assert '这句是公开台词' in other_view


async def test_manual_intervention_text_is_private_to_keeper(tmp_path):
    platform, game, _, _ = setup_platform(tmp_path)
    await platform.intervene(game.id, 'KEEPER_ONLY_CORRECTION')
    player = json.dumps(platform.games.view(game.id, 'player-0'))
    keeper = json.dumps(platform.games.view(game.id, 'keeper'))
    assert 'KEEPER_ONLY_CORRECTION' not in player
    assert 'KEEPER_ONLY_CORRECTION' in keeper


async def test_elapsed_hours_restore_mp_and_expire_insanity_without_daily_hp(
    tmp_path,
):
    def handler(call):
        if call['task'] == 'keeper_resolution':
            return KeeperResponse(narration='三个小时过去', advance_hours=3)
        return ScriptedClient.default(call)

    platform, game, _, _ = setup_platform(tmp_path, ScriptedClient(handler))
    card = platform.characters.get('card-0')
    card.current_hp = 7
    card.current_mp = 1
    card.conditions = ['temporary_insanity']
    card.allocations['runtime'] = {'temporary_insanity_hours': 5}
    platform.store.put('character', card.id, card.model_dump(mode='json'))
    first = await platform.step_game(game.id)
    halfway = platform.characters.get(card.id)
    assert first.day == 0 and first.hour == 11
    assert halfway.current_mp == 4
    assert halfway.current_hp == 7
    assert 'temporary_insanity' in halfway.conditions
    second = await platform.step_game(game.id)
    recovered = platform.characters.get(card.id)
    assert second.day == 0 and second.hour == 14
    assert recovered.current_mp == 7
    assert recovered.current_hp == 7
    assert 'temporary_insanity' not in recovered.conditions


async def test_cross_midnight_adds_one_day_of_hp_and_two_hours_of_mp(tmp_path):
    def handler(call):
        if call['task'] == 'keeper_resolution':
            return KeeperResponse(narration='经过午夜', advance_hours=2)
        return ScriptedClient.default(call)

    platform, game, _, _ = setup_platform(tmp_path, ScriptedClient(handler))
    game.hour = 23
    platform.store.put('game', game.id, game.model_dump(mode='json'))
    card = platform.characters.get('card-0')
    card.current_hp = 7
    card.current_mp = 1
    platform.store.put('character', card.id, card.model_dump(mode='json'))
    result = await platform.step_game(game.id)
    healed = platform.characters.get(card.id)
    assert result.day == 1 and result.hour == 1
    assert healed.current_hp == 8
    assert healed.current_mp == 3


def test_first_aid_checks_low_skill_healer_not_high_skill_patient(tmp_path):
    platform, _, _, _ = setup_platform(tmp_path)
    patient = platform.characters.get('card-0')
    patient.current_hp = 6
    patient.skills['急救'] = 95
    doctor = platform.characters.get('card-1')
    doctor.skills['急救'] = 15
    result, rolls, details = apply_command(
        patient,
        'heal',
        {'source': 'first_aid', 'skill_value': 99},
        healer=doctor,
        rng=FixedDice([0, 4]),
    )
    assert result.current_hp == 6
    assert not rolls[0].details['is_success']
    assert rolls[0].details['target'] == 15
    assert details['healer_id'] == doctor.id


def test_medicine_checks_high_skill_healer_and_changes_only_patient(tmp_path):
    platform, _, _, _ = setup_platform(tmp_path)
    patient = platform.characters.get('card-0')
    patient.current_hp = 6
    patient.skills['医学'] = 1
    doctor = platform.characters.get('card-1')
    doctor.skills['医学'] = 80
    result, rolls, details = apply_command(
        patient,
        'heal',
        {'source': 'medicine'},
        healer=doctor,
        rng=FixedDice([0, 5, 2]),
    )
    assert result.current_hp == 8
    assert rolls[0].details['target'] == 80
    assert details['healer_id'] == doctor.id
    assert patient.current_hp == 6
    assert doctor.current_hp == 12


@pytest.mark.parametrize('same_top_level', [False, True])
async def test_time_command_normalisation_and_cached_resume_apply_once(
    tmp_path,
    monkeypatch,
    same_top_level,
):
    def handler(call):
        if call['task'] == 'keeper_resolution':
            return KeeperResponse(
                narration='过去半小时',
                advance_hours=0.5 if same_top_level else 0,
                commands=[
                    RuleCommand(
                        kind='advance_hours',
                        reason='现场等待半小时',
                        parameters={'hours': 0.5},
                    )
                ],
            )
        return ScriptedClient.default(call)

    client = ScriptedClient(handler)
    platform, game, _, _ = setup_platform(tmp_path, client)
    original_put = platform.store.put
    interrupted = []

    def fail_before_preparation(kind, identity, data):
        if kind == 'resolution_work' and not interrupted:
            interrupted.append(True)
            raise RuntimeError('模拟保存工作帧前退出')
        return original_put(kind, identity, data)

    monkeypatch.setattr(platform.store, 'put', fail_before_preparation)
    paused = await platform.step_game(game.id)
    assert '工作帧' in paused.last_error
    assert paused.hour == 8
    previous_calls = len(client.calls)
    restored = Platform(Settings(data_dir=tmp_path / 'platform'), client)
    result = await restored.step_game(game.id)
    assert not result.last_error
    assert result.node_count == 1
    assert result.hour == 8.5
    assert len(client.calls) == previous_calls


@pytest.mark.parametrize('has_success', [False, True])
async def test_empty_growth_request_needs_current_success_and_never_repeats_it(
    tmp_path,
    monkeypatch,
    has_success,
):
    def handler(call):
        if call['task'] == 'keeper_resolution':
            if has_success:
                return KeeperResponse(
                    checks=[
                        CheckRequest(
                            character_id='card-0',
                            skill='侦查',
                        )
                    ]
                )
            return KeeperResponse(
                commands=[
                    RuleCommand(
                        kind='mark_skill',
                        character_id='card-0',
                        parameters={},
                        reason='未提供成功依据',
                    )
                ]
            )
        if call['task'] == 'keeper_feedback':
            return KeeperResponse(
                narration='自动成功标记已记录',
                commands=[
                    RuleCommand(
                        kind='mark_skill',
                        character_id='card-0',
                        parameters={},
                        reason='回述自动记录的标记',
                    ),
                ],
            )
        return ScriptedClient.default(call)

    platform, game, _, _ = setup_platform(tmp_path, ScriptedClient(handler))
    rolls = install_fixed_check(monkeypatch)
    if not has_success:
        previous = platform.store.get('character', 'card-0')
        previous['skill_marks'] = ['侦查']
        platform.store.put('character', 'card-0', previous)
    result = await platform.step_game(game.id)
    if has_success:
        assert not result.last_error
        assert result.node_count == 1
        assert len(rolls) == 1
        assert platform.characters.get('card-0').skill_marks == ['侦查']
    else:
        assert '必须指定已成功' in result.last_error
        assert result.node_count == 0
        assert len(rolls) == 0


async def test_partial_clue_note_is_private_and_does_not_reveal_full_clue(
    tmp_path,
):
    def handler(call):
        if call['task'] == 'keeper_resolution':
            return KeeperResponse(
                narration='公开只看见日志的一小段',
                scene_id='control',
                commands=[
                    RuleCommand(
                        kind='custom',
                        reason='只读到了残缺内容',
                        parameters={
                            'note': 'KEEPER_PARTIAL_NOTE',
                            'clue_partial': 'log',
                        },
                    ),
                ],
            )
        return ScriptedClient.default(call)

    platform, game, _, scenario = setup_platform(
        tmp_path, ScriptedClient(handler)
    )
    before = [
        (
            card.current_hp,
            card.current_mp,
            card.current_san,
            deepcopy(card.skills),
            deepcopy(card.background),
        )
        for card in platform.characters.list()
    ]
    result = await platform.step_game(game.id)
    assert not result.last_error
    after = [
        (
            card.current_hp,
            card.current_mp,
            card.current_san,
            card.skills,
            card.background,
        )
        for card in platform.characters.list()
    ]
    assert after == before
    player = json.dumps(
        platform.games.view(game.id, 'player-0'), ensure_ascii=False
    )
    keeper = json.dumps(
        platform.games.view(game.id, 'keeper'), ensure_ascii=False
    )
    assert '公开只看见日志的一小段' in player
    assert 'KEEPER_PARTIAL_NOTE' not in player
    assert scenario.clues[0].text not in player
    assert 'KEEPER_PARTIAL_NOTE' in keeper
    assert 'log' not in result.knowledge.get('player-0', [])


async def test_global_custom_changes_without_character_are_rejected(tmp_path):
    def handler(call):
        if call['task'] == 'keeper_resolution':
            return KeeperResponse(
                commands=[
                    RuleCommand(
                        kind='custom',
                        reason='无归属的状态修改',
                        parameters={'changes': {'current_hp': 1}},
                    )
                ]
            )
        return ScriptedClient.default(call)

    platform, game, _, _ = setup_platform(tmp_path, ScriptedClient(handler))
    result = await platform.step_game(game.id)
    assert '无角色' in result.last_error
    assert result.node_count == 0
    assert all(card.current_hp == 12 for card in platform.characters.list())


async def test_bounded_context_selects_future_scene_without_player_leaks(
    tmp_path,
):
    def richer_scenario(platform, scenario):
        scenario.source_blocks.extend(
            [
                SourceBlock(
                    id=f'noise-{index}',
                    file=f'INDEX_NOISE_{index}.docx',
                    locator=f'其他章第{index}段',
                    text='另一个场景的无关资料',
                )
                for index in range(500)
            ]
        )
        scenario.endings[0].text = 'PRIVATE_ENDING_PLAN：完整的终局条件与秘密'
        saved = platform.scenarios.save(scenario)
        return platform.scenarios.approve(saved.id)

    def handler(call):
        context = call['context']
        if call['task'] == 'player':
            return PlayerResponse(
                speech=context['self_character_name'] + '决定继续调查',
                intent='由' + context['self_actor_id'] + '观察设备',
            )
        if call['task'] == 'keeper_resolution':
            target = next(
                scene
                for scene in context['scene_catalog']
                if scene['title'] == '控制室'
            )
            ending = next(
                ending
                for ending in context['scenario']['endings']
                if ending['id'] == 'safe'
            )
            return KeeperResponse(
                narration='调查员走进控制室',
                scene_id=target['id'],
                improvisation=ending['text'],
            )
        return ScriptedClient.default(call)

    client = ScriptedClient(handler)
    platform, game, _, _ = setup_platform(
        tmp_path,
        client,
        secret_scenario=True,
        scenario_transform=richer_scenario,
    )
    card = platform.store.get('character', 'card-0')
    card['allocations']['history'] = 'ALLOCATION_HISTORY_ONLY' + 'x' * 30000
    card['creation_rolls'] = [
        {
            'expression': '1D100',
            'total': 42,
            'reason': 'RAW_CREATION_AUDIT_ONLY' + 'y' * 30000,
        }
    ]
    platform.store.put('character', 'card-0', card)
    result = await platform.step_game(game.id)
    assert not result.last_error
    assert result.scene_id == 'control'
    player_calls = [call for call in client.calls if call['task'] == 'player']
    assert player_calls[0]['actor'] == 'player-0'
    events = platform.store.events(game.id)
    assert any(
        event['kind'] == 'player'
        and event['data']['speech'] == '调查员0决定继续调查'
        for event in events
    )
    resolution = next(
        call['context']
        for call in client.calls
        if call['task'] == 'keeper_resolution'
    )
    prompt = json.dumps(resolution, ensure_ascii=False)
    assert len(prompt) < 20000
    assert 'KEEPER_SOURCE_SECRET' in prompt
    for call in client.calls:
        if call['task'] == 'player':
            assert 'KEEPER_SOURCE_SECRET' not in json.dumps(call['context'])
    player_context = json.dumps(
        platform.games.context(result, 'player-0', 'player'),
        ensure_ascii=False,
    )
    assert 'PRIVATE_ENDING_PLAN' not in player_context
    assert 'ALLOCATION_HISTORY_ONLY' not in prompt
    assert 'RAW_CREATION_AUDIT_ONLY' not in prompt
    assert 'INDEX_NOISE_' not in prompt


async def test_second_service_does_not_pause_live_process_game(tmp_path):
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(call):
        if call['task'] == 'keeper_opening':
            started.set()
            await release.wait()
        return ScriptedClient.default(call)

    client = ScriptedClient(handler)
    first, game, _, _ = setup_platform(tmp_path, client)
    pending = asyncio.create_task(first.step_game(game.id))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        second = Platform(Settings(data_dir=tmp_path / 'platform'), client)
        observed = second.get_game(game.id)
        assert observed.status == 'running'
        assert first.get_game(game.id).status == 'running'
        assert not observed.last_error
        assert is_process_alive(os.getpid())
    finally:
        await first.pause_game(game.id)
        with pytest.raises(asyncio.CancelledError):
            await pending


@pytest.mark.parametrize('exit_code', [0, 259])
def test_dead_runner_process_restores_paused_without_losing_progress(
    tmp_path,
    exit_code,
):
    platform, game, _, _ = setup_platform(tmp_path)
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    with subprocess.Popen(
        [sys.executable, '-c', f'import sys; sys.exit({exit_code})'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    ) as helper:
        helper.wait(timeout=5)
        game.status = 'waiting'
        game.node_count = 7
        game.day = 3
        game.hour = 17.5
        game.scheduler['runner_pid'] = helper.pid
        platform.store.put('game', game.id, game.model_dump(mode='json'))
        restored = Platform(Settings(data_dir=tmp_path / 'platform'))
        recovered = restored.get_game(game.id)
        assert recovered.status == 'paused'
        assert recovered.node_count == 7
        assert recovered.day == 3 and recovered.hour == 17.5
        assert '重新启动' in recovered.last_error
        assert restored.characters.get('card-0').locked_game_id == game.id


async def test_next_module_keeps_injuries_growth_items_and_background(
    tmp_path,
):
    def handler(call):
        if call['task'] == 'keeper_resolution':
            return KeeperResponse(
                narration='本次调查结束',
                is_finished=True,
                ending='完成本次调查',
            )
        return ScriptedClient.default(call)

    platform, game, cards, _ = setup_platform(
        tmp_path, ScriptedClient(handler)
    )
    veteran = platform.characters.get('card-0')
    veteran.current_hp = 7
    veteran.current_mp = 3
    veteran.current_san = 41
    veteran.skills['侦查'] = 88
    veteran.skill_marks = ['侦查']
    veteran.conditions = ['major_wound', 'temporary_insanity']
    veteran.allocations['runtime'] = {
        'lifetime_day': 7,
        'temporary_insanity_hours': 4,
    }
    veteran.inventory = [{'id': 'key', 'name': '铜钥匙'}]
    veteran.weapons = [
        {'name': '旧手枪', 'skill': '射击：手枪', 'damage': '1D10'}
    ]
    veteran.assets = {'cash': 25, 'armor': 1, 'details': '继承的旧宅'}
    veteran.experiences = ['完成了前一个模组']
    veteran.relationships = ['与医生成为了朋友']
    veteran.background['significant_people'] = '失踪的姐姐'
    platform.store.put(
        'character', veteran.id, veteran.model_dump(mode='json')
    )
    game.scheduler['base_day'] = 7
    platform.store.put('game', game.id, game.model_dump(mode='json'))
    ended = await platform.step_game(game.id)
    assert ended.status == 'ended'
    next_scenario = platform.scenarios.save(
        Scenario(
            title='不同的后续模组',
            source='builtin:test-next-module',
            author='测试',
            rights='原创测试内容',
            scenes=[
                ScenarioScene(
                    id='start',
                    title='新的入口',
                    public_text='踏入新调查',
                    keeper_text='新的真相',
                )
            ],
            endings=[
                ScenarioContent(id='done', title='后续结局', text='新的结束')
            ],
        )
    )
    next_scenario = platform.scenarios.approve(next_scenario.id)
    next_game = platform.create_game(
        '下一模组',
        next_scenario.id,
        'keeper',
        [
            {'actor_id': card.actor_id, 'character_id': card.id}
            for card in cards
        ],
    )
    continued = platform.characters.get(veteran.id)
    assert continued.locked_game_id == next_game.id
    assert (
        continued.current_hp,
        continued.current_mp,
        continued.current_san,
    ) == (
        7,
        3,
        41,
    )
    assert continued.skills['侦查'] == 88
    assert continued.skill_marks == ['侦查']
    assert continued.conditions == ['major_wound', 'temporary_insanity']
    assert continued.inventory[0]['name'] == '铜钥匙'
    assert continued.weapons[0]['name'] == '旧手枪'
    assert continued.assets['details'] == '继承的旧宅'
    assert continued.relationships == ['与医生成为了朋友']
    assert continued.background['significant_people'] == '失踪的姐姐'
    assert continued.experiences == [
        '完成了前一个模组',
        '测试团：完成本次调查',
    ]
    assert continued.allocations['runtime']['temporary_insanity_hours'] == 4
    assert next_game.scheduler['base_day'] == 7


async def test_model_receives_authorised_map_without_storing_image_payload(
    tmp_path,
):
    client = ScriptedClient()
    platform, game, _, scenario = setup_platform(tmp_path, client)
    for provider in platform.list_providers():
        provider.is_vision = True
        platform.save_provider(provider)
    original = scenario.assets[0]
    original.is_map = True
    original.visibility = 'keeper'
    scenario.assets = [original]
    platform.store.put(
        'scenario_version',
        f'{scenario.id}@{scenario.version}',
        scenario.model_dump(mode='json'),
    )
    result = await platform.step_game(game.id)
    assert not result.last_error
    opening = next(
        call for call in client.calls if call['task'] == 'keeper_opening'
    )
    players = [call for call in client.calls if call['task'] == 'player']
    assert opening['context']['_images'][0]['url'].startswith('data:image/')
    assert all(not call['context'].get('_images') for call in players)
    persisted = platform.store.list('invitation', game_id=game.id)
    assert all(
        not invitation['context'].get('_images') for invitation in persisted
    )


async def test_feedback_time_command_preserves_omitted_scene_and_days(
    tmp_path, monkeypatch
):
    def handler(call):
        if call['task'] == 'keeper_resolution':
            return KeeperResponse(
                narration='查明线索，次日进入控制室。',
                scene_id='control',
                advance_days=1,
                reveal_clue_ids=[scenario.clues[0].id],
                checks=[CheckRequest(character_id='card-0', skill='侦查')],
            )
        if call['task'] == 'keeper_feedback':
            return KeeperResponse(
                narration='线索已确认，进入控制室后又调查一小时。',
                commands=[
                    RuleCommand(
                        kind='advance_hours',
                        parameters={'hours': 1},
                        reason='实际调查耗时',
                    )
                ],
            )
        return ScriptedClient.default(call)

    client = ScriptedClient(handler)
    platform, game, _, scenario = setup_platform(tmp_path, client)
    rolls = install_fixed_check(monkeypatch)
    result = await platform.step_game(game.id)
    assert not result.last_error
    assert (result.day, result.hour, result.scene_id) == (1, 9, 'control')
    assert scenario.clues[0].id in result.knowledge['player-0']
    assert len(rolls) == 1


async def test_failed_model_responses_count_tokens_and_keep_safe_diagnostics(
    tmp_path, monkeypatch
):
    import httpx

    from aitrpg.adapters.providers import ProviderClient

    monkeypatch.setenv('DEEPSEEK_API_KEY', 'never-log-this-test-key')
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                'choices': [{'message': {'content': 'invalid-json'}}],
                'usage': {'total_tokens': 123},
            },
        )

    platform, game, _, _ = setup_platform(tmp_path)
    platform.provider_client = ProviderClient(httpx.MockTransport(respond))
    result = await platform.step_game(game.id)
    failures = platform.store.list('provider_failure', game_id=game.id)
    assert result.status == 'paused'
    assert result.node_count == 0
    assert result.token_usage == 246
    assert len(calls) == 2
    assert len(failures) == 1
    assert failures[0]['purpose'] == 'keeper_opening'
    assert 'never-log-this-test-key' not in str(failures)
    assert 'invalid-json' not in str(failures)


async def test_dependent_sanity_checks_reuse_existing_percentiles(
    tmp_path, monkeypatch
):
    feedback_count = 0

    def sanity_id(call, card_id):
        return next(
            event['data']['id']
            for event in call['context']['actual_rule_results']
            if event['kind'] == 'check'
            and event['data'].get('character_id') == card_id
            and event['data'].get('details', {}).get('skill') == 'SAN'
        )

    def handler(call):
        nonlocal feedback_count
        if call['task'] == 'keeper_resolution':
            return KeeperResponse(
                narration='第二名调查员已看见；第一名尚须仔细观察。',
                checks=[
                    CheckRequest(character_id='card-0', skill='侦查'),
                    CheckRequest(character_id='card-1', skill='SAN'),
                ],
            )
        if call['task'] == 'keeper_feedback':
            feedback_count += 1
            if feedback_count == 1:
                return KeeperResponse(
                    narration='第一名已看清，进行必要理智检定。',
                    checks=[CheckRequest(character_id='card-0', skill='SAN')],
                    commands=[
                        RuleCommand(
                            kind='sanity',
                            character_id='card-1',
                            parameters={
                                'success_loss': '1',
                                'failure_loss': '1D4',
                                'sanity_check_id': sanity_id(call, 'card-1'),
                            },
                            reason='复用第二名已投理智',
                        )
                    ],
                )
            if feedback_count == 2:
                return KeeperResponse(
                    narration='第一名检定已完成，结算损失。',
                    commands=[
                        RuleCommand(
                            kind='sanity',
                            character_id='card-0',
                            parameters={
                                'success_loss': '1',
                                'failure_loss': '1D4',
                                'sanity_check_id': sanity_id(call, 'card-0'),
                            },
                            reason='结算第一名已投理智',
                        )
                    ],
                )
            return KeeperResponse(
                narration='两人均成功稳住精神，各损失1点理智。'
            )
        return ScriptedClient.default(call)

    platform, game, _, _ = setup_platform(tmp_path, ScriptedClient(handler))
    rolls = install_fixed_check(monkeypatch)
    result = await platform.step_game(game.id)
    assert not result.last_error
    assert result.node_count == 1
    assert len(rolls) == 3
    assert platform.characters.get('card-0').current_san == 59
    assert platform.characters.get('card-1').current_san == 59
    checks = [
        event
        for event in platform.store.events(game.id)
        if event['kind'] == 'check' and event['data']['expression'] == '1D100'
    ]
    assert len(checks) == 3
    assert feedback_count == 3


async def test_internal_api_reply_outlives_mcp_deadline(tmp_path, monkeypatch):
    from datetime import timedelta

    from aitrpg.domain.models import utc_now

    clock = utc_now()
    monkeypatch.setattr('aitrpg.application.games.utc_now', lambda: clock)

    class SlowClient(ScriptedClient):
        async def generate(self, *args, **kwargs):
            nonlocal clock
            clock += timedelta(seconds=130)
            return await super().generate(*args, **kwargs)

    platform, game, _, _ = setup_platform(tmp_path, SlowClient())
    result = await platform.step_game(game.id)
    assert not result.last_error
    assert result.node_count == 1
    assert platform.store.events(game.id)[-1]['kind'] == 'narration'
