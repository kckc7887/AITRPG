import io

import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from httpx import AsyncClient
from PIL import Image
from test_games import setup_platform

from aitrpg.api import register_api
from aitrpg.application.platform import Platform
from aitrpg.config import Settings
from aitrpg.domain.models import Scenario
from aitrpg.domain.models import ScenarioAsset
from aitrpg.domain.models import ScenarioRole
from aitrpg.domain.models import ScenarioScene


def picture_draft(tmp_path):
    platform = Platform(Settings(data_dir=tmp_path, is_browser_open=False))
    scenario = Scenario(
        title='导入图片编辑',
        scenes=[ScenarioScene(id='hall', title='门厅')],
        roles=[ScenarioRole(id='ho1', name='甲')],
    )
    directory = platform.scenarios.scenarios_dir / scenario.id
    (directory / 'assets').mkdir(parents=True)
    Image.new('RGB', (100, 50), 'red').save(directory / 'assets' / 'photo.png')
    scenario.assets = [
        ScenarioAsset(
            id='photo',
            name='原始图片',
            path='assets/photo.png',
            mime_type='image/png',
        )
    ]
    return platform, platform.scenarios.save(scenario)


def node(**changes):
    return {
        'id': 'entry',
        'label': '入口',
        'x': 0.2,
        'y': 0.5,
        'visibility': 'public',
        **changes,
    }


def region(**changes):
    return {
        'id': 'left',
        'label': '入口区域',
        'shape': 'rect',
        'bounds': [0, 0, 0.5, 1],
        'visibility': 'public',
        **changes,
    }


async def test_draft_picture_preview_and_annotation_save_through_api(tmp_path):
    platform, scenario = picture_draft(tmp_path)
    app = FastAPI()
    register_api(app, platform)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='http://test'
    ) as api:
        preview = await api.get(
            f'/api/v1/scenarios/{scenario.id}/assets/photo'
        )
        assert preview.status_code == 200
        with Image.open(io.BytesIO(preview.content)) as image:
            assert image.getpixel((10, 10)) == (255, 0, 0)
        saved = await api.post(
            f'/api/v1/scenarios/{scenario.id}/assets/photo/annotations',
            json={
                'nodes': [node()],
                'regions': [region()],
                'name': '门厅地图',
                'caption': '人工核对入口',
                'is_map': True,
                'visibility': 'roles',
                'role_ids': ['ho1'],
                'scene_ids': ['hall'],
            },
        )
        assert saved.status_code == 200
    actual = platform.scenarios.get(scenario.id)
    asset = actual.assets[0]
    assert asset.name == '门厅地图'
    assert asset.caption == '人工核对入口'
    assert asset.is_map
    visible = platform.scenarios.asset_for_viewer(
        actual, 'photo', role_id='ho1'
    )
    with Image.open(visible) as image:
        assert image.getpixel((10, 10))[:3] == (255, 0, 0)
        assert image.getpixel((80, 10))[:3] != (255, 0, 0)
    with pytest.raises(PermissionError):
        platform.scenarios.asset_for_viewer(actual, 'photo', role_id='ho2')


@pytest.mark.parametrize(
    ('nodes', 'regions'),
    [
        ([node(x=-0.1)], [region()]),
        ([node(x=float('nan'))], [region()]),
        ([node(y=float('inf'))], [region()]),
        ([node(x=True)], [region()]),
        ([node(), node()], [region()]),
        ([node()], [region(), region()]),
        ([node(visibility='roles', role_ids=[])], [region()]),
        ([node(role_ids=['missing-role'])], [region()]),
        ([node(scene_ids=['missing-scene'])], [region()]),
        ([node(next_node_ids=['missing-node'])], [region()]),
        ([node(region_id='missing-region')], [region()]),
        ([node(region_id=['left'])], [region()]),
        ([node(visibility=['public'])], [region()]),
        ([node()], [region(bounds=[0.7, 0.2, 0.1, 0.8])]),
    ],
)
def test_invalid_annotations_do_not_modify_saved_draft(
    tmp_path, nodes, regions
):
    platform, scenario = picture_draft(tmp_path)
    before = platform.scenarios.get(scenario.id).model_dump(mode='json')
    with pytest.raises(ValueError):
        platform.scenarios.update_map_annotations(
            scenario.id, 'photo', nodes, regions, is_map=True
        )
    assert platform.scenarios.get(scenario.id).model_dump(mode='json') == (
        before
    )


def test_approved_map_edit_creates_draft_without_moving_running_game(tmp_path):
    def annotated(platform, scenario):
        scenario.assets[0].nodes = [node()]
        saved = platform.scenarios.save(scenario)
        return platform.scenarios.approve(saved.id)

    platform, game, _, scenario = setup_platform(
        tmp_path, scenario_transform=annotated
    )
    asset_id = scenario.assets[0].id
    before = platform.games.scenario(game).model_dump(mode='json')
    edited = platform.scenarios.update_map_annotations(
        scenario.id,
        asset_id,
        [node(id='new-room', x=0.8)],
        [region(bounds=[0, 0, 1, 1])],
        name='新地图草稿',
    )
    assert edited.status == 'draft'
    assert edited.version == scenario.version + 1
    assert platform.games.scenario(game).model_dump(mode='json') == before
    placed = platform.games.update_map(
        game.id,
        asset_id,
        {'card-0': {'node_id': 'entry'}},
        {},
    )
    assert placed.flags['map_positions'][asset_id]['card-0']['x'] == 0.2
    assert platform.scenarios.get(scenario.id).assets[0].nodes[0]['x'] == 0.8


def test_removing_last_region_keeps_fog_until_explicitly_disabled(tmp_path):
    platform, scenario = picture_draft(tmp_path)
    first = platform.scenarios.update_map_annotations(
        scenario.id, 'photo', [], [region()], is_map=True, visibility='public'
    )
    cleared = platform.scenarios.update_map_annotations(
        first.id, 'photo', [], []
    )
    masked = platform.scenarios.asset_for_viewer(cleared, 'photo')
    with Image.open(masked) as image:
        assert image.getpixel((10, 10))[:3] != (255, 0, 0)
        assert image.getpixel((80, 10))[:3] != (255, 0, 0)
    released = platform.scenarios.update_map_annotations(
        cleared.id, 'photo', [], [], is_fog_enabled=False
    )
    visible = platform.scenarios.asset_for_viewer(released, 'photo')
    with Image.open(visible) as image:
        assert image.getpixel((80, 10))[:3] == (255, 0, 0)


async def test_marker_free_image_still_masks_and_rejects_hidden_assets(
    tmp_path,
):
    platform, game, _, scenario = setup_platform(tmp_path)
    asset_id = scenario.assets[0].id
    platform.games.update_map(
        game.id, asset_id, {'card-0': {'x': 0.1, 'y': 0.5}}, {}
    )
    app = FastAPI()
    register_api(app, platform)
    url = f'/api/v1/games/{game.id}/assets/{asset_id}?actor_id=player-0'
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='http://test'
    ) as api:
        base = await api.get(url + '&markers=false')
        marked = await api.get(url)
    assert base.status_code == marked.status_code == 200
    with Image.open(io.BytesIO(base.content)) as image:
        assert image.getpixel((60, 150))[:3] == (219, 228, 223)
        assert image.getpixel((500, 150))[:3] != (219, 228, 223)
    with Image.open(io.BytesIO(marked.content)) as image:
        assert image.getpixel((60, 150))[:3] != (219, 228, 223)
    frozen = platform.games.scenario(game)
    frozen.assets[0].visibility = 'keeper'
    platform.store.put(
        'scenario_version',
        f'{scenario.id}@{scenario.version}',
        frozen.model_dump(mode='json'),
    )
    with pytest.raises(PermissionError):
        platform.games.asset_path(
            game.id, asset_id, 'player-0', include_markers=False
        )
