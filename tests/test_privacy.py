import json
from pathlib import Path

import pytest
from PIL import Image
from test_games import FixedDice
from test_games import ScriptedClient
from test_games import setup_platform

from aitrpg.adapters.mcp_server import AgentGateway
from aitrpg.adapters.storage import Store
from aitrpg.application.scenarios import ScenarioDraft
from aitrpg.application.scenarios import ScenarioService
from aitrpg.domain.models import KeeperControl
from aitrpg.domain.models import KeeperResponse
from aitrpg.domain.models import PlayerResponse
from aitrpg.domain.models import Scenario
from aitrpg.domain.models import ScenarioAsset
from aitrpg.domain.models import ScenarioContent
from aitrpg.domain.models import ScenarioRole
from aitrpg.domain.models import ScenarioScene
from aitrpg.domain.models import SourceBlock
from aitrpg.domain.privacy import retrieve_sources
from aitrpg.domain.privacy import scenario_for_viewer
from aitrpg.domain.rules import skill_check


def test_merging_public_npc_does_not_publish_later_private_revelation():
    scenario = Scenario(title='合并权限')
    first = ScenarioDraft(
        npcs=[
            ScenarioContent(
                id='doctor',
                title='医生',
                text='医生会为村民看病。',
                visibility='public',
            )
        ]
    )
    second = ScenarioDraft(
        npcs=[
            ScenarioContent(
                id='doctor',
                title='医生',
                text='医生其实操纵着地下机器。',
            )
        ]
    )
    ScenarioService._merge_draft(scenario, first, 0)
    ScenarioService._merge_draft(scenario, second, 1)
    player = json.dumps(scenario_for_viewer(scenario), ensure_ascii=False)
    keeper = json.dumps(
        scenario_for_viewer(scenario, is_keeper=True), ensure_ascii=False
    )
    assert '医生会为村民看病' in player
    assert '操纵着地下机器' not in player
    assert '操纵着地下机器' in keeper


def test_player_context_excludes_other_secrets_and_original_sources():
    scenario = Scenario(
        title='秘密测试',
        directory='C:/private/module',
        scenes=[
            ScenarioScene(
                id='start',
                title='门厅',
                public_text='钟声响了。',
                keeper_text='凶手在地下室。',
            )
        ],
        roles=[
            ScenarioRole(id='ho1', name='医生', secret_text='我曾来过这里。'),
            ScenarioRole(id='ho2', name='记者', secret_text='我藏着录音。'),
        ],
        clues=[
            ScenarioContent(id='secret', title='真相', text='幕后有一台机器'),
            ScenarioContent(
                id='own',
                title='病历',
                text='只有医生能看',
                visibility='roles',
                role_ids=['ho1'],
            ),
        ],
        source_blocks=[
            SourceBlock(
                file='raw/book.docx',
                locator='table:1',
                text='全文同时记载两个角色的秘密。',
            )
        ],
        assets=[
            ScenarioAsset(
                name='秘密图', path='assets/secret.png', mime_type='image/png'
            )
        ],
    )
    context = scenario_for_viewer(scenario, role_id='ho1')
    text = json.dumps(context, ensure_ascii=False)
    assert '我曾来过这里' in text
    assert '只有医生能看' in text
    for secret in (
        '我藏着录音',
        '凶手在地下室',
        '幕后有一台机器',
        '全文同时',
        'C:/private',
        'assets/secret.png',
    ):
        assert secret not in text
    assert retrieve_sources(scenario, '角色', is_keeper=False) == []
    revealed = scenario_for_viewer(
        scenario, role_id='ho1', revealed_ids=['secret']
    )
    assert any(clue['text'] == '幕后有一台机器' for clue in revealed['clues'])


def test_source_retrieval_uses_keeper_scope_and_relevance():
    scenario = Scenario(
        title='检索',
        source_blocks=[
            SourceBlock(
                file='a.pdf', locator='page:1', text='海岸灯塔的维修记录。'
            ),
            SourceBlock(
                file='a.pdf', locator='page:2', text='药谷中的秘密通道。'
            ),
            SourceBlock(
                file='a.pdf', locator='page:3', text='地下室里的电源。'
            ),
        ],
    )
    found = retrieve_sources(scenario, '药谷', is_keeper=True, limit=1)
    assert found[0]['text'] == '药谷中的秘密通道。'
    found = retrieve_sources(scenario, '', is_keeper=True, max_chars=5)
    assert sum(len(block['text']) for block in found) == 5
    assert found[0]['is_truncated']


def test_solo_scene_read_aloud_stays_with_its_participant():
    scenario = Scenario(
        title='单人间章',
        scenes=[
            ScenarioScene(
                id='solo',
                title='HO1间章',
                public_text='你在师兄房中发现私信。',
                conditions={'participants': ['ho1']},
            )
        ],
    )
    own = json.dumps(
        scenario_for_viewer(scenario, role_id='ho1'), ensure_ascii=False
    )
    other = json.dumps(
        scenario_for_viewer(scenario, role_id='ho2'), ensure_ascii=False
    )
    assert '师兄房中发现私信' in own
    assert '师兄房中发现私信' not in other


def test_entering_other_solo_keeps_already_revealed_evidence():
    scenario = Scenario(
        title='已知资料',
        scenes=[
            ScenarioScene(id='hall', title='门厅'),
            ScenarioScene(
                id='solo',
                title='HO1间章',
                public_text='师兄房中的私信',
                conditions={'participants': ['ho1']},
            ),
        ],
        clues=[
            ScenarioContent(
                id='read_note',
                title='已读便笺',
                text='门厅的失物线索',
                scene_ids=['hall'],
            ),
            ScenarioContent(
                id='unread_note',
                title='未读便笺',
                text='尚未发现的地道',
                scene_ids=['hall'],
            ),
        ],
        assets=[
            ScenarioAsset(
                id='hall_map',
                name='已经取得的门厅图',
                path='assets/hall.png',
                mime_type='image/png',
                scene_ids=['hall'],
            ),
        ],
    )
    other = json.dumps(
        scenario_for_viewer(
            scenario,
            role_id='ho2',
            scene_id='solo',
            revealed_ids=['read_note', 'hall_map'],
        ),
        ensure_ascii=False,
    )
    assert '门厅的失物线索' in other
    assert '已经取得的门厅图' in other
    assert '尚未发现的地道' not in other
    assert '师兄房中的私信' not in other
    assert 'assets/hall.png' not in other


async def test_failed_sighting_does_not_read_script_through_game_or_mcp(
    tmp_path: Path,
):
    truth = '黑鸟的眉眼似人'
    unused_summary = '剧情摘要中尚未发生的结局'

    def transform(platform, scenario):
        scene = scenario.scenes[0]
        scenario.description = unused_summary
        scene.public_text = f'预编写场景朗读：{truth}。'
        scene.keeper_text = f'目击成功后才能说明：{truth}。'
        scenario.clues.append(
            ScenarioContent(
                id='sighting',
                title='目击细节',
                text=truth,
                scene_ids=[scene.id],
                source_ids=scene.source_ids,
            )
        )
        platform.scenarios.save(scenario)
        return platform.scenarios.approve(scenario.id)

    def handler(call):
        if call['task'] == 'player':
            return PlayerResponse(speech='你看清了吗？', intent='询问目击者')
        if call['task'] == 'keeper_control':
            return KeeperControl(
                clock_status='unchanged',
                scene_status='unchanged',
            )
        return KeeperResponse(
            narration=f'目击的同伴解释了刚才所见：{truth}。',
            reveal_clue_ids=['sighting'],
        )

    platform, game, cards, _ = setup_platform(
        tmp_path,
        ScriptedClient(handler),
        grouped=False,
        scenario_transform=transform,
    )
    try:
        failed = skill_check(cards[0], '侦查', rng=FixedDice([9, 9]))
        assert not failed.details['is_success']
        game.node_count = game.revision = 1
        game.last_keeper = {'narration': '只看见黑鸟的背面。'}
        with platform.store.transaction() as transaction:
            transaction.put('game', game.id, game.model_dump(mode='json'))
            transaction.append_event(
                game.id,
                'check',
                {
                    **failed.model_dump(mode='json'),
                    'character_id': cards[0].id,
                    'scene_id': game.scene_id,
                },
            )
            transaction.append_event(
                game.id,
                'narration',
                {'text': '只看见黑鸟的背面。'},
            )
        actor_id = cards[0].actor_id
        token = platform.issue_agent_token(game.id, actor_id)
        gateway = AgentGateway(platform)
        views = [
            platform.games.view(game.id, actor_id),
            platform.games.context(game, actor_id, 'player'),
            await gateway.dispatch('get_my_state', {'token': token}),
        ]
        for view in views:
            text = json.dumps(view, ensure_ascii=False)
            assert truth not in text
            assert unused_summary not in text
            assert '只看见黑鸟的背面' in text
        finished = await platform.step_game(game.id)
        assert not finished.last_error
        views = [
            platform.games.view(game.id, actor_id),
            platform.games.context(finished, actor_id, 'player'),
            await gateway.dispatch('get_my_state', {'token': token}),
        ]
        for view in views:
            text = json.dumps(view, ensure_ascii=False)
            assert truth in text
            assert '目击的同伴解释了刚才所见' in text
            assert unused_summary not in text
    finally:
        platform.store.close()


def test_map_fog_hides_pixels_and_role_reveal_isolated(tmp_path: Path):
    store = Store(tmp_path / 'state.db')
    service = ScenarioService(store, None, tmp_path / 'data')
    scenario = Scenario(title='地图')
    directory = service.scenarios_dir / scenario.id
    (directory / 'assets').mkdir(parents=True)
    original = directory / 'assets' / 'map.png'
    Image.new('RGB', (100, 50), 'red').save(original)
    scenario.directory = str(directory)
    asset = ScenarioAsset(
        id='map',
        name='地图',
        path='assets/map.png',
        mime_type='image/png',
        visibility='public',
        is_map=True,
        regions=[
            {'id': 'public', 'visibility': 'public', 'bounds': [0, 0, 0.3, 1]},
            {
                'id': 'ho1',
                'visibility': 'roles',
                'role_ids': ['ho1'],
                'bounds': [0.3, 0, 0.6, 1],
            },
            {'id': 'hidden', 'visibility': 'keeper', 'bounds': [0.6, 0, 1, 1]},
        ],
    )
    scenario.assets = [asset]
    path = service.asset_for_viewer(scenario, 'map', role_id='ho2')
    assert path != original
    with Image.open(path) as image:
        assert image.getpixel((10, 10))[:3] == (255, 0, 0)
        assert image.getpixel((40, 10))[:3] != (255, 0, 0)
        assert image.getpixel((80, 10))[:3] != (255, 0, 0)
    own = service.asset_for_viewer(scenario, 'map', role_id='ho1')
    with Image.open(own) as image:
        assert image.getpixel((40, 10))[:3] == (255, 0, 0)
        assert image.getpixel((80, 10))[:3] != (255, 0, 0)
    revealed = service.asset_for_viewer(
        scenario, 'map', role_id='ho2', revealed_regions=['hidden']
    )
    with Image.open(revealed) as image:
        assert image.getpixel((80, 10))[:3] == (255, 0, 0)
    asset.visibility = 'keeper'
    with pytest.raises(PermissionError):
        service.asset_for_viewer(scenario, 'map', role_id='ho1')
    assert (
        service.asset_for_viewer(scenario, 'map', is_keeper=True) == original
    )
    store.close()
