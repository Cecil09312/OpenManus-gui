from contextlib import AsyncExitStack
from typing import Dict, List, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.types import ListToolsResult, TextContent

from app.logger import logger
from app.tool.base import BaseTool, ToolResult
from app.tool.tool_collection import ToolCollection


class MCPClientTool(BaseTool):
    """表示可以在客户端调用的 MCP 服务器上的工具代理。"""

    session: Optional[ClientSession] = None
    server_id: str = ""  # 添加服务器标识符
    original_name: str = ""

    async def execute(self, **kwargs) -> ToolResult:
        """通过向 MCP 服务器发起远程调用来执行工具。"""
        if not self.session:
            return ToolResult(error="未连接到 MCP 服务器")

        try:
            logger.info(f"正在执行工具：{self.original_name}")
            result = await self.session.call_tool(self.original_name, kwargs)
            content_str = ", ".join(
                item.text for item in result.content if isinstance(item, TextContent)
            )
            return ToolResult(output=content_str or "无输出返回。")
        except Exception as e:
            return ToolResult(error=f"执行工具时出错：{str(e)}")


class MCPClients(ToolCollection):
    """
    连接多个 MCP 服务器并通过模型上下文协议管理可用工具的工具集合。
    """

    sessions: Dict[str, ClientSession] = {}
    exit_stacks: Dict[str, AsyncExitStack] = {}
    description: str = "用于服务器交互的 MCP 客户端工具"

    def __init__(self):
        super().__init__()  # 使用空工具列表初始化
        self.name = "mcp"  # 保留名称以保持向后兼容

    async def connect_sse(self, server_url: str, server_id: str = "") -> None:
        """使用 SSE 传输连接到 MCP 服务器。"""
        if not server_url:
            raise ValueError("服务器 URL 是必填的。")

        server_id = server_id or server_url

        # 始终确保在新连接前进行干净的断开
        if server_id in self.sessions:
            await self.disconnect(server_id)

        exit_stack = AsyncExitStack()
        self.exit_stacks[server_id] = exit_stack

        streams_context = sse_client(url=server_url)
        streams = await exit_stack.enter_async_context(streams_context)
        session = await exit_stack.enter_async_context(ClientSession(*streams))
        self.sessions[server_id] = session

        await self._initialize_and_list_tools(server_id)

    async def connect_stdio(
        self, command: str, args: List[str], server_id: str = ""
    ) -> None:
        """使用 stdio 传输连接到 MCP 服务器。"""
        if not command:
            raise ValueError("服务器命令是必填的。")

        server_id = server_id or command

        # 始终确保在新连接前进行干净的断开
        if server_id in self.sessions:
            await self.disconnect(server_id)

        exit_stack = AsyncExitStack()
        self.exit_stacks[server_id] = exit_stack

        server_params = StdioServerParameters(command=command, args=args)
        stdio_transport = await exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        read, write = stdio_transport
        session = await exit_stack.enter_async_context(ClientSession(read, write))
        self.sessions[server_id] = session

        await self._initialize_and_list_tools(server_id)

    async def _initialize_and_list_tools(self, server_id: str) -> None:
        """初始化会话并填充工具映射。"""
        session = self.sessions.get(server_id)
        if not session:
            raise RuntimeError(f"服务器 {server_id} 的会话未初始化")

        await session.initialize()
        response = await session.list_tools()

        # 为每个服务器工具创建适当的工具对象
        for tool in response.tools:
            original_name = tool.name
            tool_name = f"mcp_{server_id}_{original_name}"
            tool_name = self._sanitize_tool_name(tool_name)

            server_tool = MCPClientTool(
                name=tool_name,
                description=tool.description,
                parameters=tool.inputSchema,
                session=session,
                server_id=server_id,
                original_name=original_name,
            )
            self.tool_map[tool_name] = server_tool

        # 更新工具元组
        self.tools = tuple(self.tool_map.values())
        logger.info(
            f"已连接到服务器 {server_id}，可用工具：{[tool.name for tool in response.tools]}"
        )

    def _sanitize_tool_name(self, name: str) -> str:
        """清理工具名称以满足 MCPClientTool 的要求。"""
        import re

        # 用下划线替换无效字符
        sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", name)

        # 移除连续的下划线
        sanitized = re.sub(r"_+", "_", sanitized)

        # 移除前导和尾随下划线
        sanitized = sanitized.strip("_")

        # 如果需要，截断为 64 个字符
        if len(sanitized) > 64:
            sanitized = sanitized[:64]

        return sanitized

    async def list_tools(self) -> ListToolsResult:
        """列出所有可用工具。"""
        tools_result = ListToolsResult(tools=[])
        for session in self.sessions.values():
            response = await session.list_tools()
            tools_result.tools += response.tools
        return tools_result

    async def disconnect(self, server_id: str = "") -> None:
        """断开与特定 MCP 服务器的连接，如果未提供 server_id 则断开所有服务器。"""
        if server_id:
            if server_id in self.sessions:
                try:
                    exit_stack = self.exit_stacks.get(server_id)

                    # 关闭将处理会话清理的退出栈
                    if exit_stack:
                        try:
                            await exit_stack.aclose()
                        except RuntimeError as e:
                            if "cancel scope" in str(e).lower():
                                logger.warning(
                                    f"断开 {server_id} 连接时出现取消范围错误，继续清理：{e}"
                                )
                            else:
                                raise

                    # 清理引用
                    self.sessions.pop(server_id, None)
                    self.exit_stacks.pop(server_id, None)

                    # 移除此服务器关联的工具
                    self.tool_map = {
                        k: v
                        for k, v in self.tool_map.items()
                        if v.server_id != server_id
                    }
                    self.tools = tuple(self.tool_map.values())
                    logger.info(f"已断开与 MCP 服务器 {server_id} 的连接")
                except Exception as e:
                    logger.error(f"断开服务器 {server_id} 连接时出错：{e}")
        else:
            # 以确定性顺序断开所有服务器
            for sid in sorted(list(self.sessions.keys())):
                await self.disconnect(sid)
            self.tool_map = {}
            self.tools = tuple()
            logger.info("已断开与所有 MCP 服务器的连接")
