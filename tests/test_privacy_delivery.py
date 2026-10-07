import asyncio
import json
from contextlib import suppress

import pytest
from test_games import ScriptedClient
from test_games import setup_platform

from aitrpg.domain.models import Actor
from aitrpg.domain.models import KeeperResponse
from aitrpg.domain.models import RuleCommand

SECRETS = {
    'role-0': '黑玉令牌藏在我胸前的暗袋之中，只有月食之夜才能召来守门人。',
    'role-1': '那年冬夜我亲手烧毁了师门名册，从此再也不敢在同门面前露出真容。',
    'role-2': '我曾用兄长的名字换取禁术，如今每逢雨夜都会听见他的魂魄敲门。',
}
SOLO_READOUTS = (
    '师兄将金色典籍交到你的手中，并让你独自查找第三页的红色记号。',
    '药宗长老坐在静室里，命你辨别炉旁那株带着紫色斑点的草药。',
    '山道尽头的年轻旅者突然回头，催促你检查木桥下面新出现的足迹。',
)


def private_scenario(platform, scenario):
    for role in scenario.roles:
        role.secret_text = SECRETS[role.id]
    scenario.scenes[0].conditions['participants'] = ['role-0']
    scenario.clues[0].text = SECRETS['role-1']
    scenario.clues[0].scene_ids = [scenario.scenes[0].id]
    saved = platform.scenarios.save(scenario)
    return platform.scenarios.approve(saved.id)


def setup_delivery(tmp_path, handler):
    client = ScriptedClient(handler)
    platform, game, _, _ = setup_platform(
        tmp_path,
        client,
        grouped=False,
        secret_scenario=True,
        scenario_transform=private_scenario,
    )
    return platform, game, client


def narration_texts(platform, game_id, actor_id):
    return [
        event['data']['text']
        for event in platform.games.view(game_id, actor_id)['events']
        if event['kind'] == 'narration'
    ]


async def test_opening_scene_change_keeps_original_private_audience(tmp_path):
    def handler(call):
        if call['task'] == 'keeper_opening':
            return KeeperResponse(
                narration=SECRETS['role-0'], scene_id='control'
            )
        return ScriptedClient.default(call)

    platform, game, _ = setup_delivery(tmp_path, handler)
    result = await platform.step_game(game.id)
    assert not result.last_error
    assert result.scene_id == 'control'
    assert SECRETS['role-0'] in narration_texts(platform, game.id, 'player-0')
    for actor_id in ('player-1', 'player-2'):
        assert SECRETS['role-0'] not in narration_texts(
            platform, game.id, actor_id
        )


async def test_opening_cannot_broadcast_solo_source_by_explicit_recipients(
    tmp_path,
):
    readout = '导师与你私下谈论昨夜的见闻，此时其他调查员不在场。'

    def solo_readout(platform, scenario):
        scenario = private_scenario(platform, scenario)
        scenario.scenes[0].public_text = readout
        saved = platform.scenarios.save(scenario)
        return platform.scenarios.approve(saved.id)

    def handler(call):
        if call['task'] == 'keeper_opening':
            return KeeperResponse(
                narration=readout,
                scene_id='control',
                recipient_actor_ids=['player-0', 'player-1'],
            )
        return ScriptedClient.default(call)

    platform, game, _, _ = setup_platform(
        tmp_path,
        ScriptedClient(handler),
        grouped=False,
        secret_scenario=True,
        scenario_transform=solo_readout,
    )
    await platform.step_game(game.id)
    assert readout not in narration_texts(platform, game.id, 'player-1')


@pytest.mark.parametrize('delivery', ['public', 'wrong_private', 'widened'])
async def test_another_role_secret_is_rejected_before_publication(
    tmp_path, delivery
):
    def handler(call):
        if call['task'] == 'keeper_opening':
            if delivery == 'wrong_private':
                return KeeperResponse(
                    narration='中继站传来雨声。',
                    private_messages={'player-0': SECRETS['role-1']},
                )
            if delivery == 'widened':
                return KeeperResponse(
                    narration=SECRETS['role-0'],
                    scene_id='control',
                    recipient_actor_ids=['player-0', 'player-1'],
                )
            return KeeperResponse(narration=SECRETS['role-1'])
        return ScriptedClient.default(call)

    platform, game, client = setup_delivery(tmp_path, handler)
    result = await platform.step_game(game.id)
    assert result.status == 'paused'
    if delivery == 'widened':
        assert '开场受众不能超出原镜头授权成员' in result.last_error
    else:
        assert '未获授权的身份秘密' in result.last_error
    assert result.node_count == 0
    assert not any(
        event['kind'] == 'narration'
        for event in platform.store.events(game.id)
    )
    assert sum(call['task'] == 'keeper_opening' for call in client.calls) == 2


async def test_owner_private_messages_reach_only_each_owner(tmp_path):
    def handler(call):
        if call['task'] == 'keeper_opening':
            return KeeperResponse(
                narration='每人收到一封密信。',
                scene_id='control',
                private_messages={
                    f'player-{index}': SECRETS[f'role-{index}']
                    for index in range(3)
                },
            )
        return ScriptedClient.default(call)

    platform, game, _ = setup_delivery(tmp_path, handler)
    result = await platform.step_game(game.id)
    assert not result.last_error
    for index in range(3):
        actor_id = f'player-{index}'
        texts = narration_texts(platform, game.id, actor_id)
        assert SECRETS[f'role-{index}'] in texts
        for other in range(3):
            if other != index:
                assert SECRETS[f'role-{other}'] not in texts
        context = platform.games.context(result, actor_id, 'player')
        visible_history = json.dumps(context['events'], ensure_ascii=False)
        assert SECRETS[f'role-{index}'] in visible_history
        for other in range(3):
            if other != index:
                assert SECRETS[f'role-{other}'] not in visible_history


async def test_passive_mcp_opening_cannot_bypass_secret_delivery_guard(
    tmp_path,
):
    player_started = asyncio.Event()

    def handler(call):
        if call['task'] == 'player':
            player_started.set()
        return ScriptedClient.default(call)

    platform, game, _ = setup_delivery(tmp_path, handler)
    platform.save_actor(Actor(id='keeper', name='被动主持', connection='mcp'))
    token = platform.issue_agent_token(game.id, 'keeper')
    pending = asyncio.create_task(platform.step_game(game.id))
    await asyncio.sleep(0)
    invitation = await platform.games.wait_for_invitation(token, 0)
    is_rejected = False
    try:
        platform.games.submit(
            invitation['id'],
            'mcp-private-opening',
            KeeperResponse(narration=SECRETS['role-1']).model_dump(),
            'keeper',
        )
    except ValueError as error:
        assert '未获授权的身份秘密' in str(error)
        is_rejected = True
    if not is_rejected:
        reached_player = asyncio.create_task(player_started.wait())
        completed, _ = await asyncio.wait(
            {pending, reached_player},
            timeout=2,
            return_when=asyncio.FIRST_COMPLETED,
        )
        assert completed
        reached_player.cancel()
        with suppress(asyncio.CancelledError):
            await reached_player
    await platform.pause_game(game.id)
    with suppress(asyncio.CancelledError):
        await pending
    assert SECRETS['role-1'] not in narration_texts(
        platform, game.id, 'player-0'
    )
    assert platform.games.get(game.id).node_count == 0


@pytest.mark.parametrize('is_previously_known', [True, False])
async def test_authorised_clue_repetition_is_not_blocked_as_identity_secret(
    tmp_path, is_previously_known
):
    def handler(call):
        if call['task'] == 'keeper_resolution':
            return KeeperResponse(
                narration=SECRETS['role-1'],
                reveal_clue_ids=[] if is_previously_known else ['log'],
            )
        return ScriptedClient.default(call)

    platform, game, _ = setup_delivery(tmp_path, handler)
    if is_previously_known:
        game.knowledge['player-0'] = ['log']
        platform.store.put('game', game.id, game.model_dump(mode='json'))
    result = await platform.step_game(game.id)
    assert not result.last_error
    assert result.node_count == 1
    assert SECRETS['role-1'] in narration_texts(platform, game.id, 'player-0')
    view = platform.games.view(game.id, 'player-0')
    assert any(
        clue['id'] == 'log' and clue['text'] == SECRETS['role-1']
        for clue in view['scenario']['clues']
    )


async def test_split_group_keeps_other_start_when_first_group_advances(
    tmp_path,
):
    resolutions = []

    def handler(call):
        if call['task'] == 'keeper_resolution':
            resolutions.append(call)
            if len(resolutions) == 1:
                return KeeperResponse(
                    narration='A队进入控制室，B队留在门口。',
                    scene_id='control',
                    commands=[
                        RuleCommand(
                            kind='split_groups',
                            parameters={
                                'groups': {
                                    'A': ['player-0'],
                                    'B': ['player-1', 'player-2'],
                                }
                            },
                            reason='两队分头调查。',
                        )
                    ],
                )
            return KeeperResponse(narration='镜头转回门口。', group_id='B')
        return ScriptedClient.default(call)

    client = ScriptedClient(handler)
    platform, game, _, _ = setup_platform(tmp_path, client, grouped=False)
    first = await platform.step_game(game.id)
    assert not first.last_error
    assert platform.games.view(game.id, 'player-0')['game']['scene_id'] == (
        'control'
    )
    assert platform.games.view(game.id, 'player-1')['game']['scene_id'] == (
        'arrival'
    )
    second = await platform.step_game(game.id)
    assert not second.last_error
    assert (second.group_id, second.scene_id) == ('B', 'arrival')
    assert platform.games.view(game.id, 'player-0')['game']['scene_id'] == (
        'control'
    )


async def test_explicit_solo_group_starts_use_each_role_visibility(tmp_path):
    def solo_scenario(platform, scenario):
        scenario = private_scenario(platform, scenario)
        for index, scene in enumerate(scenario.scenes):
            scene.conditions['participants'] = [f'role-{index}']
            scene.public_text = SOLO_READOUTS[index]
        saved = platform.scenarios.save(scenario)
        return platform.scenarios.approve(saved.id)

    def handler(call):
        if call['task'] == 'keeper_resolution':
            return KeeperResponse(
                narration='各队确认自己的位置。',
                private_messages={
                    f'player-{index}': text
                    for index, text in enumerate(SOLO_READOUTS)
                },
                commands=[
                    RuleCommand(
                        kind='split_groups',
                        parameters={
                            'groups': {
                                'A': ['player-0'],
                                'B': ['player-1'],
                                'C': ['player-2'],
                            },
                            'group_scenes': {
                                'A': 'arrival',
                                'B': 'control',
                                'C': 'departure',
                            },
                        },
                        reason='三份原始单人镜头各自展开。',
                    )
                ],
            )
        return ScriptedClient.default(call)

    platform, game, _, _ = setup_platform(
        tmp_path,
        ScriptedClient(handler),
        grouped=False,
        secret_scenario=True,
        scenario_transform=solo_scenario,
    )
    for index in range(3):
        before = json.dumps(
            platform.games.view(game.id, f'player-{index}'),
            ensure_ascii=False,
        )
        assert all(text not in before for text in SOLO_READOUTS)
    result = await platform.step_game(game.id)
    assert not result.last_error
    for index, scene_id in enumerate(('arrival', 'control', 'departure')):
        view = platform.games.view(game.id, f'player-{index}')
        assert view['game']['scene_id'] == scene_id
        assert [scene['id'] for scene in view['scenario']['scenes']] == [
            scene_id
        ]
        texts = narration_texts(platform, game.id, f'player-{index}')
        assert SOLO_READOUTS[index] in texts
        assert all(
            text not in texts
            for other, text in enumerate(SOLO_READOUTS)
            if other != index
        )


@pytest.mark.parametrize('stage', ['keeper_opening', 'keeper_resolution'])
async def test_narration_without_authorised_witnesses_is_keeper_only(
    tmp_path, stage
):
    marker = 'NO_AUTHORISED_WITNESS_NARRATION'

    def other_role_scene(platform, scenario):
        scenario = private_scenario(platform, scenario)
        scenario.scenes[0].conditions['participants'] = ['role-1']
        saved = platform.scenarios.save(scenario)
        return platform.scenarios.approve(saved.id)

    def handler(call):
        if call['task'] == stage:
            return KeeperResponse(narration=marker)
        return ScriptedClient.default(call)

    platform, game, _, _ = setup_platform(
        tmp_path,
        ScriptedClient(handler),
        grouped=True,
        secret_scenario=True,
        scenario_transform=other_role_scene,
    )
    result = await platform.step_game(game.id)
    assert not result.last_error
    assert marker in narration_texts(platform, game.id, 'keeper')
    for actor_id in ('player-0', 'player-1', 'player-2'):
        assert marker not in narration_texts(platform, game.id, actor_id)


async def test_split_group_rejects_nonexistent_start_scene(tmp_path):
    def handler(call):
        if call['task'] == 'keeper_resolution':
            return KeeperResponse(
                narration='试图从错误位置开始。',
                commands=[
                    RuleCommand(
                        kind='split_groups',
                        parameters={
                            'groups': {
                                'A': ['player-0'],
                                'B': ['player-1', 'player-2'],
                            },
                            'group_scenes': {'B': 'missing-scene'},
                        },
                        reason='不合法的目的地。',
                    )
                ],
            )
        return ScriptedClient.default(call)

    platform, game, _, _ = setup_platform(
        tmp_path, ScriptedClient(handler), grouped=False
    )
    result = await platform.step_game(game.id)
    assert '不存在的场景' in result.last_error
    assert result.node_count == 0
    assert all(seat.group_id == 'main' for seat in result.seats)


async def test_public_clue_does_not_unlock_other_private_recipient(
    tmp_path,
):
    def handler(call):
        if call['task'] == 'keeper_resolution':
            return KeeperResponse(
                narration='将日志交给现场调查员。',
                reveal_clue_ids=['log'],
                private_messages={'player-2': SECRETS['role-1']},
            )
        return ScriptedClient.default(call)

    platform, game, _ = setup_delivery(tmp_path, handler)
    result = await platform.step_game(game.id)
    assert '未获授权的身份秘密' in result.last_error
    assert result.node_count == 0
    assert SECRETS['role-1'] not in narration_texts(
        platform, game.id, 'player-2'
    )


async def test_hidden_description_is_not_public_secret_permission(tmp_path):
    def private_summary(platform, scenario):
        scenario = private_scenario(platform, scenario)
        scenario.description = SECRETS['role-1']
        saved = platform.scenarios.save(scenario)
        return platform.scenarios.approve(saved.id)

    def handler(call):
        if call['task'] == 'keeper_resolution':
            return KeeperResponse(narration=SECRETS['role-1'])
        return ScriptedClient.default(call)

    platform, game, _, _ = setup_platform(
        tmp_path,
        ScriptedClient(handler),
        grouped=False,
        secret_scenario=True,
        scenario_transform=private_summary,
    )
    before = json.dumps(
        platform.games.view(game.id, 'player-0'), ensure_ascii=False
    )
    assert SECRETS['role-1'] not in before
    result = await platform.step_game(game.id)
    assert '未获授权的身份秘密' in result.last_error
    assert SECRETS['role-1'] not in narration_texts(
        platform, game.id, 'player-0'
    )
