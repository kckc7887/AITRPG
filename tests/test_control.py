import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from httpx import AsyncClient
from test_games import ScriptedClient
from test_games import install_fixed_check
from test_games import setup_platform

from aitrpg.adapters.storage import Transaction
from aitrpg.api import register_api
from aitrpg.application.platform import Platform
from aitrpg.config import Settings
from aitrpg.domain.models import CheckRequest
from aitrpg.domain.models import KeeperControl
from aitrpg.domain.models import KeeperResponse

EXACT_NARRATION = '第二天清晨六点离岸，两小时后到达控制室码头。'


class ControlClient(ScriptedClient):
    async def generate(self, provider, system, context, output_type):
        if output_type is KeeperControl:
            context = {**context, 'task': 'keeper_control'}
        return await super().generate(provider, system, context, output_type)


def prepare_control_game(
    tmp_path, narration, projection, *, is_check=False, flags=None
):
    def handler(call):
        if call['task'] == 'keeper_control':
            return projection
        if call['task'] == 'keeper_resolution':
            return KeeperResponse(
                narration=narration,
                flags=flags or {},
                checks=(
                    [CheckRequest(character_id='card-0', skill='侦查')]
                    if is_check
                    else []
                ),
            )
        if call['task'] == 'keeper_feedback':
            return KeeperResponse(narration=narration)
        return ScriptedClient.default(call)

    client = ControlClient(handler)
    platform, game, cards, _ = setup_platform(tmp_path, client)
    game.hour = 8.5
    platform.store.put('game', game.id, game.model_dump(mode='json'))
    card = cards[0]
    card.current_hp = 7
    card.current_mp = 1
    card.conditions = ['temporary_insanity']
    card.allocations['runtime'] = {'temporary_insanity_hours': 50}
    platform.store.put('character', card.id, card.model_dump(mode='json'))
    return platform, game, client


def exact_projection(**changes):
    return KeeperControl(
        clock_status='supported',
        scene_status='supported',
        target_day=1,
        target_hour=8,
        scene_id='control',
        clock_quote=EXACT_NARRATION,
        scene_quote='到达控制室码头',
    ).model_copy(update=changes)


def natural_recoveries(platform, game_id, character_id='card-0'):
    return [
        event
        for event in platform.store.events(game_id)
        if event['kind'] == 'ruling'
        and event['data'].get('command') == 'natural_recovery'
        and event['data'].get('character_id') == character_id
    ]


def prepare_committed_history(tmp_path, projection):
    platform, game, client = prepare_control_game(
        tmp_path, EXACT_NARRATION, projection
    )
    game.node_count = 4
    game.revision = 4
    game.last_keeper = KeeperResponse(narration=EXACT_NARRATION).model_dump()
    platform.store.put('game', game.id, game.model_dump(mode='json'))
    with platform.store.transaction() as transaction:
        transaction.append_event(
            game.id,
            'player',
            {
                'actor_id': 'player-0',
                'speech': '检查器材',
                'intent': '整理装备',
            },
            actor_ids=['player-0'],
        )
        transaction.append_event(
            game.id,
            'narration',
            {'text': EXACT_NARRATION},
            actor_ids=['player-0'],
        )
    return platform, game, client


async def test_future_departure_plan_does_not_advance_clock_or_scene(tmp_path):
    narration = '戈登说：“第二天六点离岸，到达控制室后再调查。”'
    platform, game, client = prepare_control_game(
        tmp_path,
        narration,
        KeeperControl(clock_status='unchanged', scene_status='unchanged'),
    )
    result = await platform.step_game(game.id)
    card = platform.characters.get('card-0')
    assert not result.last_error
    assert result.node_count == 1
    assert (result.day, result.hour, result.scene_id) == (0, 8.5, 'arrival')
    assert (card.current_hp, card.current_mp) == (7, 1)
    assert not natural_recoveries(platform, game.id)
    assert any(
        event['data'].get('text') == narration
        for event in platform.store.events(game.id)
    )
    assert sum(call['task'] == 'keeper_control' for call in client.calls) == 1


async def test_absolute_next_day_clock_advances_and_recovers_once(tmp_path):
    platform, game, _ = prepare_control_game(
        tmp_path, EXACT_NARRATION, exact_projection()
    )
    result = await platform.step_game(game.id)
    card = platform.characters.get('card-0')
    assert not result.last_error
    assert (result.day, result.hour, result.scene_id) == (1, 8, 'control')
    assert result.last_keeper['advance_hours'] == 23.5
    assert (card.current_hp, card.current_mp) == (8, 12)
    assert card.allocations['runtime']['temporary_insanity_hours'] == 26
    assert len(natural_recoveries(platform, game.id)) == 1


async def test_location_flag_updates_scene_without_inventing_elapsed_time(
    tmp_path,
):
    narration = '调查员已在控制室整理器材。'
    platform, game, _ = prepare_control_game(
        tmp_path,
        narration,
        KeeperControl(
            clock_status='unchanged',
            scene_status='supported',
            scene_id='control',
            scene_quote='调查员已在控制室',
        ),
        flags={'at_control_room': True},
    )
    result = await platform.step_game(game.id)
    card = platform.characters.get('card-0')
    assert not result.last_error
    assert (result.day, result.hour, result.scene_id) == (0, 8.5, 'control')
    assert (card.current_hp, card.current_mp) == (7, 1)
    assert not natural_recoveries(platform, game.id)


async def test_unmapped_destination_pauses_instead_of_retaining_old_scene(
    tmp_path,
):
    narration = '第二天清晨六点离岸，两小时后到达一处未知码头。'
    platform, game, _ = prepare_control_game(
        tmp_path,
        narration,
        KeeperControl(
            clock_status='supported',
            scene_status='needs_review',
            target_day=1,
            target_hour=8,
            clock_quote=narration,
            reason='目录没有该码头，无法确认场景。',
        ),
    )
    result = await platform.step_game(game.id)
    assert '无法确认场景' in result.last_error
    assert result.node_count == 0
    assert (result.day, result.hour, result.scene_id) == (0, 8.5, 'arrival')
    assert not natural_recoveries(platform, game.id)


async def test_unknown_arrival_time_requires_review_without_recovery(tmp_path):
    narration = '调查员过夜后，第二天晚些时候到达控制室。'
    platform, game, _ = prepare_control_game(
        tmp_path,
        narration,
        KeeperControl(
            clock_status='needs_review',
            scene_status='supported',
            scene_id='control',
            scene_quote='到达控制室',
            reason='无法确定到达时刻。',
        ),
    )
    result = await platform.step_game(game.id)
    card = platform.characters.get('card-0')
    assert '无法确定到达时刻' in result.last_error
    assert result.node_count == 0
    assert (result.day, result.hour, result.scene_id) == (0, 8.5, 'arrival')
    assert (card.current_hp, card.current_mp) == (7, 1)
    assert not natural_recoveries(platform, game.id)


@pytest.mark.parametrize(
    ('changes', 'error'),
    [
        ({'clock_quote': '叙事中不存在的时间'}, '时间投影缺少'),
        ({'scene_quote': '叙事中不存在的地点'}, '场景投影缺少'),
        ({'target_day': 0, 'target_hour': 7.5}, '时间倒退'),
        ({'scene_id': 'unknown-port'}, '不存在的场景'),
    ],
)
async def test_invalid_projection_pauses_without_committing_state(
    tmp_path, changes, error
):
    platform, game, _ = prepare_control_game(
        tmp_path, EXACT_NARRATION, exact_projection(**changes)
    )
    result = await platform.step_game(game.id)
    card = platform.characters.get('card-0')
    assert result.status == 'paused'
    assert error in result.last_error
    assert result.node_count == 0
    assert (result.day, result.hour, result.scene_id) == (0, 8.5, 'arrival')
    assert (card.current_hp, card.current_mp) == (7, 1)
    assert not natural_recoveries(platform, game.id)
    assert not any(
        event['data'].get('text') == EXACT_NARRATION
        for event in platform.store.events(game.id)
    )


@pytest.mark.parametrize('is_allowed', [True, False])
async def test_estimated_time_respects_game_configuration(
    tmp_path, is_allowed
):
    narration = '第二天六点离岸，大约两小时后到达控制室码头。'
    platform, game, _ = prepare_control_game(
        tmp_path,
        narration,
        exact_projection(
            clock_status='estimated',
            clock_quote=narration,
            reason='主持将约两小时裁定为两小时。',
        ),
    )
    platform.settings.is_estimated_time = is_allowed
    result = await platform.step_game(game.id)
    card = platform.characters.get('card-0')
    if is_allowed:
        assert not result.last_error
        assert result.node_count == 1
        assert (result.day, result.hour, result.scene_id) == (1, 8, 'control')
        assert (card.current_hp, card.current_mp) == (8, 12)
        assert len(natural_recoveries(platform, game.id)) == 1
    else:
        assert '当前配置要求人工核对' in result.last_error
        assert result.node_count == 0
        assert (result.day, result.hour, result.scene_id) == (
            0,
            8.5,
            'arrival',
        )
        assert (card.current_hp, card.current_mp) == (7, 1)
        assert not natural_recoveries(platform, game.id)


async def test_restart_after_projection_reuses_reply_and_real_dice(
    tmp_path, monkeypatch
):
    platform, game, client = prepare_control_game(
        tmp_path, EXACT_NARRATION, exact_projection(), is_check=True
    )
    rolls = install_fixed_check(monkeypatch)
    projected = asyncio.Event()
    release = asyncio.Event()
    synchronise = platform.games._synchronise_controls

    async def wait_after_projection(*args):
        reply = await synchronise(*args)
        projected.set()
        await release.wait()
        return reply

    monkeypatch.setattr(
        platform.games, '_synchronise_controls', wait_after_projection
    )
    pending = asyncio.create_task(platform.step_game(game.id))
    await asyncio.wait_for(projected.wait(), timeout=2)
    await platform.pause_game(game.id)
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert platform.characters.get('card-0').current_hp == 7
    restored = Platform(Settings(data_dir=tmp_path / 'platform'), client)
    result = await restored.step_game(game.id)
    card = restored.characters.get('card-0')
    assert not result.last_error
    assert result.node_count == 1
    assert (result.day, result.hour, result.scene_id) == (1, 8, 'control')
    assert (card.current_hp, card.current_mp) == (8, 12)
    assert len(rolls) == 1
    assert sum(call['task'] == 'keeper_control' for call in client.calls) == 1
    assert sum(call['task'] == 'keeper_feedback' for call in client.calls) == 1
    assert len(natural_recoveries(restored, game.id)) == 1


async def test_restart_after_time_checkpoint_does_not_recover_twice(
    tmp_path, monkeypatch
):
    platform, game, client = prepare_control_game(
        tmp_path, EXACT_NARRATION, exact_projection(), is_check=True
    )
    rolls = install_fixed_check(monkeypatch)
    checkpoint = platform.games._checkpoint

    def cancel_after_time(*args):
        checkpoint(*args)
        if args[-1].get('time_applied'):
            raise asyncio.CancelledError

    monkeypatch.setattr(platform.games, '_checkpoint', cancel_after_time)
    with pytest.raises(asyncio.CancelledError):
        await platform.step_game(game.id)
    assert platform.characters.get('card-0').current_hp == 7
    restored = Platform(Settings(data_dir=tmp_path / 'platform'), client)
    result = await restored.step_game(game.id)
    card = restored.characters.get('card-0')
    assert not result.last_error
    assert result.node_count == 1
    assert (result.day, result.hour, result.scene_id) == (1, 8, 'control')
    assert (card.current_hp, card.current_mp) == (8, 12)
    assert card.allocations['runtime']['temporary_insanity_hours'] == 26
    assert len(rolls) == 1
    assert sum(call['task'] == 'keeper_control' for call in client.calls) == 1
    assert len(natural_recoveries(restored, game.id)) == 1


async def test_synchronise_api_repairs_committed_clock_without_new_actions(
    tmp_path,
):
    platform, game, client = prepare_committed_history(
        tmp_path, exact_projection()
    )

    def handler(call):
        if len(client.calls) == 1:
            return exact_projection()
        return KeeperControl(
            clock_status='unchanged', scene_status='unchanged'
        )

    client.handler = handler
    app = FastAPI()
    register_api(app, platform)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='http://test'
    ) as api:
        first = await api.post(f'/api/v1/games/{game.id}/synchronise')
        assert first.status_code == 200
        result = first.json()
        assert (result['day'], result['hour'], result['scene_id']) == (
            1,
            8,
            'control',
        )
        assert result['node_count'] == 4
        card = platform.characters.get('card-0')
        assert (card.current_hp, card.current_mp) == (8, 12)
        second = await api.post(f'/api/v1/games/{game.id}/synchronise')
        assert second.status_code == 200
        assert (second.json()['day'], second.json()['hour']) == (1, 8)
    card = platform.characters.get('card-0')
    assert (card.current_hp, card.current_mp) == (8, 12)
    assert card.allocations['runtime']['temporary_insanity_hours'] == 26
    events = platform.store.events(game.id)
    assert sum(event['kind'] == 'player' for event in events) == 1
    assert sum(event['kind'] == 'narration' for event in events) == 1
    assert len(natural_recoveries(platform, game.id)) == 1
    assert all(call['task'] == 'keeper_control' for call in client.calls)
    assert len(client.calls) == 2


async def test_spliced_quote_is_repaired_once_before_history_recovery(
    tmp_path,
):
    platform, game, client = prepare_committed_history(
        tmp_path,
        exact_projection(clock_quote='第二天清晨六点离岸到达控制室码头'),
    )

    def handler(call):
        if call['context'].get('reaction_key') == 'quote_repair':
            return exact_projection()
        return exact_projection(clock_quote='第二天清晨六点离岸到达控制室码头')

    client.handler = handler
    result = await platform.games.synchronise_controls(game.id)
    card = platform.characters.get('card-0')
    assert (result.day, result.hour, result.scene_id) == (1, 8, 'control')
    assert result.node_count == 4
    assert (card.current_hp, card.current_mp) == (8, 12)
    assert len(client.calls) == 2
    assert len(natural_recoveries(platform, game.id)) == 1
    assert not any(call['task'] == 'player' for call in client.calls)


async def test_bad_quote_after_one_repair_stays_paused_without_more_calls(
    tmp_path,
):
    platform, game, client = prepare_committed_history(
        tmp_path,
        exact_projection(clock_quote='第二天清晨六点离岸到达控制室码头'),
    )
    with pytest.raises(ValueError, match='时间投影缺少'):
        await platform.games.synchronise_controls(game.id)
    restored = Platform(Settings(data_dir=tmp_path / 'platform'), client)
    with pytest.raises(ValueError, match='时间投影缺少'):
        await restored.games.synchronise_controls(game.id)
    result = restored.games.get(game.id)
    card = restored.characters.get('card-0')
    assert len(client.calls) == 2
    assert result.node_count == 4
    assert (result.day, result.hour, result.scene_id) == (0, 8.5, 'arrival')
    assert (card.current_hp, card.current_mp) == (7, 1)
    assert not natural_recoveries(restored, game.id)


async def test_history_commit_failure_retries_cached_projection_atomically(
    tmp_path, monkeypatch
):
    platform, game, client = prepare_committed_history(
        tmp_path, exact_projection()
    )
    append_event = Transaction.append_event
    failed = []

    def fail_after_first_recovery(
        transaction, identifier, kind, data, **kwargs
    ):
        if (
            identifier == game.id
            and kind == 'ruling'
            and data.get('command') == 'natural_recovery'
            and data.get('character_id') == 'card-1'
            and not failed
        ):
            failed.append(True)
            raise RuntimeError('模拟核对事务中途失败')
        return append_event(transaction, identifier, kind, data, **kwargs)

    monkeypatch.setattr(Transaction, 'append_event', fail_after_first_recovery)
    with pytest.raises(RuntimeError, match='核对事务中途失败'):
        await platform.games.synchronise_controls(game.id)
    card = platform.characters.get('card-0')
    assert (card.current_hp, card.current_mp) == (7, 1)
    assert not natural_recoveries(platform, game.id)
    restored = Platform(Settings(data_dir=tmp_path / 'platform'), client)
    result = await restored.games.synchronise_controls(game.id)
    card = restored.characters.get('card-0')
    assert (result.day, result.hour, result.scene_id) == (1, 8, 'control')
    assert result.node_count == 4
    assert (card.current_hp, card.current_mp) == (8, 12)
    assert card.allocations['runtime']['temporary_insanity_hours'] == 26
    assert len(client.calls) == 1
    assert len(natural_recoveries(restored, game.id)) == 1
    events = restored.store.events(game.id)
    assert sum(event['kind'] == 'player' for event in events) == 1
    assert sum(event['kind'] == 'narration' for event in events) == 1
