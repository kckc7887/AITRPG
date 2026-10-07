from __future__ import annotations

import argparse
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx2
from mcp import Client
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError

from aitrpg.adapters.mcp_server import register_agent_tools
from aitrpg.adapters.mcp_server import tool_payload
from aitrpg.config import environment_value


def create_bridge(endpoint: str, token: str) -> MCPServer:
    connection: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(_server):
        async with Client(endpoint) as client:
            connection['client'] = client
            try:
                yield
            finally:
                connection.clear()

    async def forward(name: str, arguments: dict) -> dict:
        values = {**arguments, 'token': token}
        try:
            result = await connection['client'].call_tool(
                name,
                values,
                read_timeout_seconds=30,
            )
            return tool_payload(result)
        except (
            MCPError,
            OSError,
            TimeoutError,
            httpx2.TransportError,
        ) as error:
            raise ToolError(
                '平台连接中断，请重启桥接后继续领取邀请'
            ) from error

    server = MCPServer(
        'aitrpg_mcp_bridge',
        lifespan=lifespan,
        subscriptions=False,
        instructions='凭据已由环境变量绑定，工具调用可以省略 token。',
    )
    register_agent_tools(server, forward)
    return server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description='本地 stdio 到平台 MCP 桥接')
    parser.add_argument(
        '--endpoint',
        default=os.environ.get('AITRPG_MCP_URL', 'http://127.0.0.1:8080/mcp'),
    )
    parser.add_argument('--token-env', default='AITRPG_AGENT_TOKEN')
    arguments = parser.parse_args(argv)
    token = environment_value(arguments.token_env)
    if not token:
        parser.error('请通过配置 env 向桥接进程提供席位凭据')
    create_bridge(arguments.endpoint, token).run(transport='stdio')


if __name__ == '__main__':
    main()
