from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL
from uuid import uuid5

import httpx2
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError

from aitrpg.adapters.mcp_server import tool_payload
from aitrpg.adapters.providers import ProviderClient
from aitrpg.application.games import CONTROL_INSTRUCTION
from aitrpg.application.games import KEEPER_INSTRUCTION
from aitrpg.application.games import PLAYER_INSTRUCTION
from aitrpg.config import environment_value
from aitrpg.domain.models import KeeperControl
from aitrpg.domain.models import KeeperResponse
from aitrpg.domain.models import PlayerResponse
from aitrpg.domain.models import Provider

CONNECTION_ERRORS = (
    OSError,
    TimeoutError,
    MCPError,
    httpx2.TransportError,
)


def load_provider(path: Path | None = None) -> Provider:
    if path is not None:
        return Provider.model_validate(json.loads(path.read_text('utf-8')))
    base = environment_value('AITRPG_AGENT_BASE_URL') or environment_value(
        'DEEPSEEK_BASE_URL_OPENAI'
    )
    if not base:
        raise ValueError('请配置客户端模型地址或指定 --provider-config')
    return Provider(
        name='外部参考 Agent 模型',
        base_url=base,
        protocol=os.environ.get('AITRPG_AGENT_PROTOCOL', 'openai'),
        model=os.environ.get('AITRPG_AGENT_MODEL', 'deepseek-flash'),
        key_env=os.environ.get('AITRPG_AGENT_KEY_ENV', 'DEEPSEEK_API_KEY'),
    )


async def _call(client: Any, name: str, token: str, **arguments) -> dict:
    return tool_payload(
        await client.call_tool(
            name,
            {'token': token, **arguments},
            read_timeout_seconds=30,
        )
    )


async def _heartbeat(client: Any, token: str) -> None:
    while True:
        await asyncio.sleep(15)
        await _call(client, 'heartbeat', token)


def _error_message(error: BaseException) -> str:
    if isinstance(error, BaseExceptionGroup):
        return '；'.join(_error_message(item) for item in error.exceptions)
    return str(error)


async def run_agent(
    endpoint: str,
    token: str,
    provider: Provider,
    *,
    display_name: str = '',
    provider_client: Any = None,
    client_factory: Any = Client,
) -> dict:
    generator = provider_client or ProviderClient()
    pending = None
    retry_seconds = 1
    while True:
        try:
            async with client_factory(endpoint) as client:
                joined = await _call(
                    client, 'join_game', token, display_name=display_name
                )
                heartbeat = asyncio.create_task(_heartbeat(client, token))
                try:
                    while True:
                        if pending is not None:
                            try:
                                await _call(
                                    client, 'submit_response', token, **pending
                                )
                            except ToolError as error:
                                if not any(
                                    word in str(error)
                                    for word in ('过期', '失效')
                                ):
                                    raise
                                print(
                                    '邀请已失效，等待新的任务。',
                                    file=sys.stderr,
                                )
                            pending = None
                        work = await _call(
                            client,
                            'wait_for_invitation',
                            token,
                            wait_seconds=20,
                        )
                        retry_seconds = 1
                        if work.get('invitation') is None and 'id' not in work:
                            if work.get('game_status') == 'ended':
                                await _call(client, 'leave_game', token)
                                return {'status': 'ended'}
                            await asyncio.sleep(work.get('retry_after', 2))
                            continue
                        is_keeper = joined['is_keeper']
                        output_type = (
                            KeeperResponse if is_keeper else PlayerResponse
                        )
                        instruction = (
                            KEEPER_INSTRUCTION
                            if is_keeper
                            else PLAYER_INSTRUCTION
                        )
                        if is_keeper and work['purpose'] == 'keeper_control':
                            output_type = KeeperControl
                            instruction = CONTROL_INSTRUCTION
                        if is_keeper and work['purpose'] == 'keeper_repair':
                            from aitrpg.application.ruling_repair import (
                                scoped_repair_type,
                            )

                            output_type = scoped_repair_type(
                                work['context'].get('allowed_attacker_id'),
                                work['context'].get('is_attack_allowed', True),
                            )
                        result = await generator.generate(
                            provider,
                            instruction,
                            work['context'],
                            output_type,
                        )
                        pending = {
                            'invitation_id': work['id'],
                            'submission_id': str(
                                uuid5(NAMESPACE_URL, work['id'])
                            ),
                            'response': result.value.model_dump(mode='json'),
                        }
                finally:
                    heartbeat.cancel()
                    with suppress(
                        asyncio.CancelledError,
                        ToolError,
                        MCPError,
                        OSError,
                        httpx2.TransportError,
                    ):
                        await heartbeat
        except (*CONNECTION_ERRORS, ExceptionGroup) as error:
            if isinstance(error, ExceptionGroup):
                _, other = error.split(CONNECTION_ERRORS)
                if other is not None:
                    _, unknown = other.split((ValueError, ToolError))
                    if unknown is None:
                        raise ToolError(_error_message(other)) from error
                    raise
            print('平台连接中断，保留未确认回复并重连。', file=sys.stderr)
            await asyncio.sleep(retry_seconds)
            retry_seconds = min(retry_seconds * 2, 30)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description='常驻外部 TRPG Agent')
    parser.add_argument(
        '--endpoint',
        default=os.environ.get('AITRPG_MCP_URL', 'http://127.0.0.1:8080/mcp'),
    )
    parser.add_argument('--token-env', default='AITRPG_AGENT_TOKEN')
    parser.add_argument('--provider-config', type=Path)
    parser.add_argument('--name', default='参考客户端')
    arguments = parser.parse_args(argv)
    token = environment_value(arguments.token_env)
    if not token:
        parser.error('请将席位凭据放入 --token-env 指定的环境变量')
    try:
        provider = load_provider(arguments.provider_config)
        asyncio.run(
            run_agent(
                arguments.endpoint,
                token,
                provider,
                display_name=arguments.name,
            )
        )
    except KeyboardInterrupt:
        print('参考客户端已停止。', file=sys.stderr)
    except (ValueError, ToolError) as error:
        print(f'参考客户端停止：{error}', file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == '__main__':
    main()
