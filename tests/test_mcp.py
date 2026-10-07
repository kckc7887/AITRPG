import asyncio
import socket
import sys
from datetime import timedelta
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi import FastAPI
from mcp import Client
from mcp import StdioServerParameters
from mcp.server.mcpserver.exceptions import ToolError
from test_characters import make_character

from aitrpg.adapters.mcp_server import register_mcp
from aitrpg.adapters.mcp_server import tool_payload
from aitrpg.adapters.providers import Generation
from aitrpg.agent import run_agent
from aitrpg.application.platform import Platform
from aitrpg.config import Settings
from aitrpg.domain.models import Actor
from aitrpg.domain.models import Invitation
from aitrpg.domain.models import PlayerResponse
from aitrpg.domain.models import Provider
from aitrpg.domain.models import Scenario
from aitrpg.domain.models import ScenarioRole
from aitrpg.domain.models import ScenarioScene
from aitrpg.domain.models import Seat
from aitrpg.domain.models import new_id
from aitrpg.domain.models import utc_now
from aitrpg.domain.rules import occupation_budget
from aitrpg.domain.rules import validate_allocations


@pytest.fixture
async def table(tmp_path):
    platform = Platform(Settings(data_dir=tmp_path, is_browser_open=False))
    actors = [
        platform.save_actor(Actor(name=name, connection='mcp'))
        for name in ('主持', '甲', '乙')
    ]
    cards = [
        platform.characters.save(make_character(actor.id))
        for actor in actors[1:]
    ]
    scenario = platform.scenarios.save(
        Scenario(
            title='MCP 权限调查',
            min_players=2,
            max_players=2,
            scenes=[
                ScenarioScene(
                    id='hall',
                    title='大厅',
                    public_text='公共大厅',
                    keeper_text='KP 真相',
                )
            ],
            roles=[
                ScenarioRole(id='a', name='甲', secret_text='甲的秘密'),
                ScenarioRole(id='b', name='乙', secret_text='乙的秘密'),
            ],
        )
    )
    platform.scenarios.approve(scenario.id)
    game = platform.create_game(
        '权限测试',
        scenario.id,
        actors[0].id,
        [
            Seat(actor_id=actor.id, character_id=card.id, role_id=role)
            for actor, card, role in zip(
                actors[1:], cards, ('a', 'b'), strict=True
            )
        ],
    )
    tokens = [
        platform.issue_agent_token(game.id, actor.id) for actor in actors
    ]
    app = FastAPI()
    register_mcp(app, platform)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    for _ in range(100):
        if server.started:
            break
        if task.done():
            await task
        await asyncio.sleep(0.02)
    try:
        yield SimpleNamespace(
            platform=platform,
            actors=actors,
            cards=cards,
            game=game,
            tokens=tokens,
            endpoint=f'http://127.0.0.1:{port}/mcp',
        )
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        listener.close()
        platform.store.close()


async def call(client, name, token, **arguments):
    return tool_payload(
        await client.call_tool(
            name,
            {
                'token': token,
                **arguments,
            },
        )
    )


def invitation(table, *, actor=1, seconds=120):
    item = Invitation(
        game_id=table.game.id,
        actor_id=table.actors[actor].id,
        character_id=table.cards[actor - 1].id,
        revision=table.game.revision,
        purpose='player',
        scene_id='hall',
        expires_at=utc_now() + timedelta(seconds=seconds),
        context={'task': 'player', 'scene': '公共大厅'},
    )
    table.platform.store.put(
        'invitation', item.id, item.model_dump(mode='json')
    )
    return item


async def test_http_clients_keep_private_state_and_card_ownership(table):
    async with (
        Client(table.endpoint) as first,
        Client(table.endpoint, mode='legacy') as second,
    ):
        joined = await call(
            first, 'join_game', table.tokens[1], display_name='自称某个模型'
        )
        assert joined['identity']['is_identity_verified'] is False
        states = await asyncio.gather(
            call(first, 'get_my_state', table.tokens[1]),
            call(second, 'get_my_state', table.tokens[2]),
        )
        assert '甲的秘密' in str(states[0])
        assert '乙的秘密' not in str(states[0])
        assert 'KP 真相' not in str(states[0])
        assert '乙的秘密' in str(states[1])
        cards = await call(first, 'list_my_characters', table.tokens[1])
        assert [card['id'] for card in cards['characters']] == [
            table.cards[0].id
        ]
        rejected = await first.call_tool(
            'update_character',
            {
                'token': table.tokens[1],
                'character_id': table.cards[1].id,
                'changes': {'name': '偷改'},
            },
        )
        assert rejected.is_error
        assert (
            table.platform.characters.get(table.cards[1].id).name == '林医生'
        )


async def test_submit_rejects_wrong_game_and_expiry_and_reuses_receipt(table):
    item = invitation(table)
    expired = invitation(table, seconds=-1)
    other_game = table.game.model_copy(update={'id': new_id()})
    table.platform.store.put(
        'game', other_game.id, other_game.model_dump(mode='json')
    )
    other_token = table.platform.issue_agent_token(
        other_game.id, table.actors[1].id
    )
    response = PlayerResponse(
        speech='检查门锁', intent='观察门锁'
    ).model_dump()
    async with Client(table.endpoint) as client:
        wrong_game = await client.call_tool(
            'submit_response',
            {
                'token': other_token,
                'invitation_id': item.id,
                'submission_id': 'same-submit',
                'response': response,
            },
        )
        assert wrong_game.is_error
        wrong_actor = await client.call_tool(
            'submit_response',
            {
                'token': table.tokens[2],
                'invitation_id': item.id,
                'submission_id': 'same-submit',
                'response': response,
            },
        )
        assert wrong_actor.is_error
        claimed = await call(
            client, 'wait_for_invitation', table.tokens[1], wait_seconds=0
        )
        assert claimed['id'] == item.id
        accepted = await call(
            client,
            'submit_response',
            table.tokens[1],
            invitation_id=item.id,
            submission_id='same-submit',
            response=response,
        )
        repeated = await call(
            client,
            'submit_response',
            table.tokens[1],
            invitation_id=item.id,
            submission_id='same-submit',
            response=response,
        )
        assert (
            accepted
            == repeated
            == {'accepted': True, 'invitation_id': item.id}
        )
        refused = await client.call_tool(
            'submit_response',
            {
                'token': table.tokens[1],
                'invitation_id': expired.id,
                'submission_id': 'old-submit',
                'response': response,
            },
        )
        assert refused.is_error
        assert (
            table.platform.store.get('invitation', expired.id)['response']
            is None
        )


async def test_character_tools_create_and_edit_only_free_own_cards(table):
    card = make_character(table.actors[1].id)
    card.name = '新调查员'
    async with Client(table.endpoint) as client:
        created = await call(
            client,
            'create_character',
            table.tokens[1],
            character=card.model_dump(mode='json'),
        )
        edited = await call(
            client,
            'update_character',
            table.tokens[1],
            character_id=created['id'],
            changes={'name': '已改名'},
        )
        assert edited['name'] == '已改名'
        locked = await client.call_tool(
            'update_character',
            {
                'token': table.tokens[1],
                'character_id': table.cards[0].id,
                'changes': {'current_hp': 1},
            },
        )
        assert locked.is_error
        assert table.platform.characters.get(table.cards[0].id).current_hp == 7
        foreign = make_character(table.actors[2].id).model_dump(mode='json')
        refused = await client.call_tool(
            'create_character',
            {
                'token': table.tokens[1],
                'character': foreign,
            },
        )
        assert refused.is_error
        await call(client, 'heartbeat', table.tokens[1])
        left = await call(client, 'leave_game', table.tokens[1])
        assert left['is_connected'] is False


async def test_stdio_bridge_uses_bound_token_instead_of_supplied_token(table):
    parameters = StdioServerParameters(
        command=sys.executable,
        args=['-m', 'aitrpg.bridge', '--endpoint', table.endpoint],
        env={'AITRPG_AGENT_TOKEN': table.tokens[1]},
    )
    async with Client(parameters) as client:
        state = await call(client, 'get_my_state', table.tokens[2])
        assert '甲的秘密' in str(state)
        assert '乙的秘密' not in str(state)


async def test_reference_agent_retries_lost_receipt_without_regenerating(
    table,
):
    item = invitation(table)
    state = {'calls': 0, 'submissions': [], 'lost_ack': False}

    class Generator:
        async def generate(self, provider, system, context, output_type):
            state['calls'] += 1
            return Generation(
                value=PlayerResponse(intent='检查出口'), usage=10
            )

    class LostAckClient:
        def __init__(self, endpoint):
            self.client = Client(endpoint)

        async def __aenter__(self):
            await self.client.__aenter__()
            return self

        async def __aexit__(self, *args):
            return await self.client.__aexit__(*args)

        async def call_tool(self, name, arguments, **kwargs):
            result = await self.client.call_tool(name, arguments, **kwargs)
            if name == 'submit_response':
                state['submissions'].append(arguments)
                if not state['lost_ack']:
                    state['lost_ack'] = True
                    raise OSError('提交已落库，但回执丢失')
                game = table.platform.get_game(table.game.id)
                game.status = 'ended'
                table.platform.store.put(
                    'game', game.id, game.model_dump(mode='json')
                )
            return result

    result = await asyncio.wait_for(
        run_agent(
            table.endpoint,
            table.tokens[1],
            Provider(name='测试生成器', base_url='http://127.0.0.1/unused'),
            provider_client=Generator(),
            client_factory=LostAckClient,
        ),
        10,
    )
    assert result['status'] == 'ended'
    assert state['calls'] == 1
    assert len(state['submissions']) == 2
    assert state['submissions'][0] == state['submissions'][1]
    assert (
        table.platform.store.get('invitation', item.id)['status']
        == 'submitted'
    )


async def test_reference_agent_reports_invalid_token_without_retrying(table):
    with pytest.raises(ToolError, match='凭据无效'):
        await asyncio.wait_for(
            run_agent(
                table.endpoint,
                'invalid-token',
                Provider(name='未使用', base_url='http://127.0.0.1/unused'),
            ),
            5,
        )


async def test_bootstrap_cards_do_not_grant_game_or_foreign_actor_access(
    table,
):
    actor = table.platform.save_actor(
        Actor(name='未开团身份', connection='mcp')
    )
    token = table.platform.issue_actor_token(actor.id)
    card = make_character(actor.id)
    card.name = '首次建卡'
    item = invitation(table)
    async with Client(table.endpoint) as client:
        joined = await call(client, 'join_game', token)
        assert joined['game_status'] == 'unassigned'
        assert joined['actor']['id'] == actor.id
        created = await call(
            client,
            'create_character',
            token,
            character=card.model_dump(mode='json'),
        )
        updated = await call(
            client,
            'update_character',
            token,
            character_id=created['id'],
            changes={'name': '完成建卡'},
        )
        assert updated['name'] == '完成建卡'
        state = await call(
            client,
            'get_my_state',
            token,
            game_id=table.game.id,
            actor_id=table.actors[0].id,
        )
        assert state['game_status'] == 'unassigned'
        assert [value['id'] for value in state['characters']] == [
            created['id']
        ]
        waiting = await call(
            client, 'wait_for_invitation', token, wait_seconds=0
        )
        assert waiting['game_status'] == 'unassigned'
        assert waiting['invitation'] is None
        refused = await client.call_tool(
            'submit_response',
            {
                'token': token,
                'invitation_id': item.id,
                'submission_id': 'upgrade',
                'response': {'speech': '未经授权开团'},
            },
        )
        assert refused.is_error
        foreign = await client.call_tool(
            'update_character',
            {
                'token': token,
                'character_id': table.cards[0].id,
                'changes': {'name': '试图改他人角色'},
            },
        )
        assert foreign.is_error
        left = await client.call_tool('leave_game', {'token': token})
        assert left.is_error


def draft_character(actor_id, prepared):
    card = make_character(actor_id).model_dump(mode='json')
    for key in (
        'age',
        'attributes',
        'luck',
        'max_hp',
        'max_mp',
        'max_san',
        'current_hp',
        'current_mp',
        'current_san',
        'movement',
        'damage_bonus',
        'build',
        'creation_rolls',
    ):
        card[key] = prepared[key]
    selected = card['allocations']['selected_skills']
    bases = prepared['skill_bases']
    occupational = {'信用评级': 30}
    remaining = (
        occupation_budget(card['occupation_definition'], card['attributes'])
        - 30
    )
    for skill in selected:
        points = min(99 - bases[skill], remaining)
        occupational[skill] = points
        remaining -= points
    interests = {}
    remaining = card['attributes']['INT'] * 2
    for skill in ('潜行', '游泳', '攀爬'):
        points = min(99 - bases[skill], remaining)
        interests[skill] = points
        remaining -= points
    card['allocations']['occupational'] = occupational
    card['allocations']['interests'] = interests
    card['skills'] = validate_allocations(
        card['attributes'],
        card['occupation_definition'],
        selected,
        occupational,
        interests,
    )
    return card


async def test_mcp_draft_rejects_foreign_and_changed_rolls_and_double_use(
    table,
):
    first = table.platform.save_actor(Actor(name='新身份甲', connection='mcp'))
    second = table.platform.save_actor(
        Actor(name='新身份乙', connection='mcp')
    )
    token = table.platform.issue_actor_token(first.id)
    foreign_token = table.platform.issue_actor_token(second.id)
    async with Client(table.endpoint) as client:
        prepared = await call(client, 'prepare_character', token, age=30)
        card = draft_character(first.id, prepared)
        stolen = {**card, 'actor_id': second.id}
        refused = await client.call_tool(
            'create_character',
            {
                'token': foreign_token,
                'draft_id': prepared['draft_id'],
                'character': stolen,
            },
        )
        assert refused.is_error
        forged = {
            **card,
            'attributes': {
                **card['attributes'],
                'STR': card['attributes']['STR'] + 1,
            },
        }
        cheated = await client.call_tool(
            'create_character',
            {
                'token': token,
                'draft_id': prepared['draft_id'],
                'character': forged,
            },
        )
        assert cheated.is_error
        assert table.platform.characters.list(first.id) == []
        saved = await call(
            client,
            'create_character',
            token,
            draft_id=prepared['draft_id'],
            character=card,
        )
        assert saved['actor_id'] == first.id
        duplicate = await client.call_tool(
            'create_character',
            {
                'token': token,
                'draft_id': prepared['draft_id'],
                'character': {**card, 'id': new_id()},
            },
        )
        assert duplicate.is_error
        assert len(table.platform.characters.list(first.id)) == 1
