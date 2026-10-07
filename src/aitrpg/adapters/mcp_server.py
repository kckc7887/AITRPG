from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Annotated
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from aitrpg.domain.models import Character
from aitrpg.domain.models import KeeperResponse
from aitrpg.domain.models import PlayerResponse
from aitrpg.domain.models import utc_now

TOKEN = Annotated[str, Field(description='平台签发的席位凭据，桥接模式可省略')]
WAIT = Annotated[float, Field(ge=0, le=20, description='最长等待秒数')]
LIMIT = Annotated[int, Field(ge=1, le=200, description='最多返回记录数')]
DISPATCH = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def tool_payload(result: Any) -> dict[str, Any]:
    if result.is_error:
        message = '；'.join(
            item.text for item in result.content if hasattr(item, 'text')
        )
        raise ToolError(message or '远端工具调用失败')
    value = result.structured_content
    if not isinstance(value, dict):
        raise ToolError('平台没有返回结构化结果，请检查版本')
    return value


def register_agent_tools(server: MCPServer, dispatch: DISPATCH) -> None:
    async def invoke(name: str, arguments: dict) -> dict:
        try:
            return await dispatch(name, arguments)
        except (ValueError, PermissionError) as error:
            raise ToolError(str(error)) from error

    async def join_game(
        token: TOKEN = '', display_name: str = ''
    ) -> dict[str, Any]:
        """加入游戏或查询未开团的建卡身份；名称仅为自报信息。"""
        return await invoke(
            'join_game',
            {
                'token': token,
                'display_name': display_name,
            },
        )

    async def get_my_state(
        token: TOKEN = '', after_sequence: int = 0, limit: LIMIT = 100
    ) -> dict[str, Any]:
        """读取本人可见状态与事件；用 next_sequence 继续读取。"""
        return await invoke(
            'get_my_state',
            {
                'token': token,
                'after_sequence': after_sequence,
                'limit': limit,
            },
        )

    async def wait_for_invitation(
        token: TOKEN = '', wait_seconds: WAIT = 20
    ) -> dict[str, Any]:
        """领取仅分配给本人的发言邀请，无邀请时按 retry_after 重试。"""
        return await invoke(
            'wait_for_invitation',
            {
                'token': token,
                'wait_seconds': wait_seconds,
            },
        )

    async def submit_response(
        invitation_id: str,
        submission_id: str,
        response: dict,
        token: TOKEN = '',
    ) -> dict[str, Any]:
        """提交本人的回应；网络重试必须使用相同提交 ID 和相同内容。"""
        return await invoke(
            'submit_response',
            {
                'token': token,
                'invitation_id': invitation_id,
                'submission_id': submission_id,
                'response': response,
            },
        )

    async def heartbeat(token: TOKEN = '') -> dict[str, Any]:
        """报告在线状态，不延长邀请期限或增加行动次数。"""
        return await invoke('heartbeat', {'token': token})

    async def leave_game(token: TOKEN = '') -> dict[str, Any]:
        """记录外部 Agent 离线；保留席位、角色和游戏记录。"""
        return await invoke('leave_game', {'token': token})

    async def list_my_characters(token: TOKEN = '') -> dict[str, Any]:
        """列出该凭据身份持有的角色卡，不返回其他身份的档案。"""
        return await invoke('list_my_characters', {'token': token})

    async def create_character(
        character: dict, token: TOKEN = '', draft_id: str | None = None
    ) -> dict[str, Any]:
        """提交本人完整 CoC7 角色卡；需要合法职业配点与完整背景。"""
        return await invoke(
            'create_character',
            {
                'token': token,
                'character': character,
                'draft_id': draft_id,
            },
        )

    async def prepare_character(
        token: TOKEN = '',
        age: int = 30,
        age_reductions: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """由服务器骰标准属性并准备建卡草稿，随后用 draft_id 完成人物。"""
        return await invoke(
            'prepare_character',
            {
                'token': token,
                'age': age,
                'age_reductions': age_reductions,
            },
        )

    async def update_character(
        character_id: str, changes: dict, token: TOKEN = ''
    ) -> dict[str, Any]:
        """修改本人未被游戏占用的卡；不能改变归属、版本或占用信息。"""
        return await invoke(
            'update_character',
            {
                'token': token,
                'character_id': character_id,
                'changes': changes,
            },
        )

    read_only = {'get_my_state', 'list_my_characters'}
    idempotent = {'submit_response', 'heartbeat', 'leave_game'} | read_only
    for handler in (
        join_game,
        get_my_state,
        wait_for_invitation,
        submit_response,
        heartbeat,
        leave_game,
        list_my_characters,
        create_character,
        prepare_character,
        update_character,
    ):
        name = handler.__name__
        server.add_tool(
            handler,
            structured_output=True,
            annotations=ToolAnnotations(
                readOnlyHint=name in read_only,
                destructiveHint=False,
                idempotentHint=name in idempotent,
                openWorldHint=False,
            ),
        )


class AgentGateway:
    def __init__(self, platform: Any):
        self.platform = platform

    def _presence(self, identity: dict, is_connected: bool, name='') -> dict:
        key = f'{identity["game_id"] or "unassigned"}:{identity["actor_id"]}'
        previous = self.platform.store.get('agent_presence', key) or {}
        record = {
            'game_id': identity['game_id'],
            'actor_id': identity['actor_id'],
            'is_connected': is_connected,
            'last_seen': utc_now().isoformat(),
            'display_name': name or previous.get('display_name', ''),
            'is_identity_verified': False,
        }
        self.platform.store.put('agent_presence', key, record)
        return record

    async def dispatch(self, name: str, arguments: dict) -> dict:
        identity = self.platform.games.authenticate(arguments.get('token', ''))
        game_id = identity['game_id']
        actor_id = identity['actor_id']
        if game_id is None:
            if name in {'join_game', 'get_my_state'}:
                actor = self.platform.store.get('actor', actor_id)
                if actor is None:
                    raise ValueError('建卡身份不存在')
                self._presence(
                    identity, True, arguments.get('display_name', '')[:120]
                )
                return {
                    'game_status': 'unassigned',
                    'is_keeper': False,
                    'actor': actor,
                    'characters': [
                        card.model_dump(mode='json')
                        for card in self.platform.characters.list(actor_id)
                    ],
                    'workflow': (
                        'prepare_character → create_character(draft_id)；'
                        '开团后改用游戏席位凭据'
                    ),
                }
            if name == 'wait_for_invitation':
                return {
                    'invitation': None,
                    'game_status': 'unassigned',
                    'retry_after': 2,
                }
            if name == 'heartbeat':
                return {
                    **self._presence(identity, True),
                    'game_status': 'unassigned',
                }
            if name not in {
                'list_my_characters',
                'create_character',
                'prepare_character',
                'update_character',
            }:
                raise ValueError('建卡凭据尚未绑定游戏，不能进行游戏操作')
        if name == 'join_game':
            presence = self._presence(
                identity, True, arguments.get('display_name', '')[:120]
            )
            game = self.platform.get_game(game_id)
            return {
                'identity': presence,
                'game_status': game.status,
                'is_keeper': actor_id == game.keeper_actor_id,
                'workflow': 'wait_for_invitation → submit_response → 重复领取',
            }
        if name == 'heartbeat':
            return {
                **self._presence(identity, True),
                'game_status': self.platform.get_game(game_id).status,
            }
        if name == 'leave_game':
            return self._presence(identity, False)
        if name == 'wait_for_invitation':
            self._presence(identity, True)
            result = await self.platform.games.wait_for_invitation(
                arguments['token'], arguments.get('wait_seconds', 20)
            )
            if result.get('id'):
                response_type = (
                    KeeperResponse
                    if result['purpose'].startswith('keeper_')
                    else PlayerResponse
                )
                result['response_schema'] = response_type.model_json_schema()
            return result
        if name == 'get_my_state':
            view = self.platform.games.view(game_id, actor_id)
            events = [
                item
                for item in view['events']
                if item['sequence'] > arguments.get('after_sequence', 0)
            ]
            limit = arguments.get('limit', 100)
            view['events'] = events[:limit]
            view['has_more'] = len(events) > limit
            view['next_sequence'] = (
                view['events'][-1]['sequence']
                if view['events']
                else arguments.get('after_sequence', 0)
            )
            return view
        if name == 'submit_response':
            invitation = self.platform.store.get(
                'invitation', arguments['invitation_id']
            )
            if (
                not invitation
                or invitation['game_id'] != game_id
                or invitation['actor_id'] != actor_id
            ):
                raise ValueError('发言邀请不属于凭据绑定的游戏与身份')
            return self.platform.games.submit(
                arguments['invitation_id'],
                arguments['submission_id'],
                arguments['response'],
                actor_id,
            )
        if name == 'list_my_characters':
            return {
                'characters': [
                    card.model_dump(mode='json')
                    for card in self.platform.characters.list(actor_id)
                ]
            }
        if name == 'prepare_character':
            prepared = self.platform.characters.prepare(
                actor_id,
                age=arguments.get('age', 30),
                age_reductions=arguments.get('age_reductions'),
            )
            return {
                **prepared,
                'character_schema': Character.model_json_schema(),
            }
        if name == 'create_character':
            payload = dict(arguments['character'])
            if payload.get('actor_id', actor_id) != actor_id:
                raise ValueError('不能为其他身份创建角色卡')
            payload['actor_id'] = actor_id
            card = Character.model_validate(payload)
            if self.platform.store.get('character', card.id):
                raise ValueError('角色卡已存在，请使用更新工具')
            if arguments.get('draft_id'):
                return self.platform.characters.finalise_draft(
                    actor_id,
                    arguments['draft_id'],
                    card,
                ).model_dump(mode='json')
            return self.platform.characters.save(card).model_dump(mode='json')
        if name == 'update_character':
            card = self.platform.characters.get(arguments['character_id'])
            if card.actor_id != actor_id:
                raise ValueError('不能修改其他身份的角色卡')
            forbidden = {
                'id',
                'actor_id',
                'version',
                'created_at',
                'locked_game_id',
            }
            if forbidden.intersection(arguments['changes']):
                raise ValueError('不能修改角色归属、版本或占用信息')
            payload = card.model_dump(mode='json') | arguments['changes']
            updated = Character.model_validate(payload)
            return self.platform.characters.save(updated).model_dump(
                mode='json'
            )
        raise ValueError('不支持的 Agent 操作')


def create_mcp_server(platform: Any) -> MCPServer:
    server = MCPServer(
        'aitrpg_mcp',
        instructions=(
            '仅控制凭据绑定的身份。持续游玩需要客户端自己的运行循环；'
            '先加入游戏，再领取邀请，按期限提交回应。不要自行骰点或改当局卡。'
        ),
        subscriptions=False,
    )
    register_agent_tools(server, AgentGateway(platform).dispatch)
    return server


def register_mcp(app: Any, platform: Any) -> MCPServer:
    server = create_mcp_server(platform)
    previous_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with previous_lifespan(application) as state:
            async with server.session_manager.run():
                yield state

    app.router.lifespan_context = lifespan
    app.mount(
        '/mcp',
        server.streamable_http_app(
            streamable_http_path='/',
            stateless_http=True,
            json_response=True,
        ),
    )
    return server
