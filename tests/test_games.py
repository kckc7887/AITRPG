import asyncio
import inspect
import json
import shutil
from copy import deepcopy

import pytest

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
from aitrpg.domain.models import ScenarioAsset
from aitrpg.domain.models import ScenarioRole
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


def setup_platform(tmp_path, client=None, grouped=True, secret_scenario=False):
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
    assert 'PUBLIC_SCENE_B' in text
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
