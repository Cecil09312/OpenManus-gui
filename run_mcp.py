#!/usr/bin/env python
import argparse
import asyncio
import sys

from app.agent.mcp import MCPAgent
from app.config import config
from app.logger import logger


class MCPRunner:
    """MCP 智能体运行器，包含路径处理和配置功能。"""

    def __init__(self):
        self.root_path = config.root_path
        self.server_reference = config.mcp_config.server_reference
        self.agent = MCPAgent()

    async def initialize(
        self,
        connection_type: str,
        server_url: str | None = None,
    ) -> None:
        """使用适当的连接方式初始化 MCP 智能体。"""
        logger.info(f"正在使用 {connection_type} 连接方式初始化 MCPAgent...")

        if connection_type == "stdio":
            await self.agent.initialize(
                connection_type="stdio",
                command=sys.executable,
                args=["-m", self.server_reference],
            )
        else:  # sse
            await self.agent.initialize(connection_type="sse", server_url=server_url)

        logger.info(f"已通过 {connection_type} 连接到 MCP 服务器")

    async def run_interactive(self) -> None:
        """以交互模式运行智能体。"""
        print("\nMCP 智能体交互模式（输入 'exit' 退出）\n")
        while True:
            user_input = input("\n请输入你的请求：")
            if user_input.lower() in ["exit", "quit", "q"]:
                break
            response = await self.agent.run(user_input)
            print(f"\n智能体：{response}")

    async def run_single_prompt(self, prompt: str) -> None:
        """使用单个提示词运行智能体。"""
        await self.agent.run(prompt)

    async def run_default(self) -> None:
        """以默认模式运行智能体。"""
        prompt = input("请输入你的提示词：")
        if not prompt.strip():
            logger.warning("提供了空的提示词。")
            return

        logger.warning("正在处理你的请求...")
        await self.agent.run(prompt)
        logger.info("请求处理完成。")

    async def cleanup(self) -> None:
        """清理智能体资源。"""
        await self.agent.cleanup()
        logger.info("会话已结束")


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="运行 MCP 智能体")
    parser.add_argument(
        "--connection",
        "-c",
        choices=["stdio", "sse"],
        default="stdio",
        help="连接类型：stdio 或 sse",
    )
    parser.add_argument(
        "--server-url",
        default="http://127.0.0.1:8000/sse",
        help="SSE 连接的 URL",
    )
    parser.add_argument(
        "--interactive", "-i", action="store_true", help="以交互模式运行"
    )
    parser.add_argument("--prompt", "-p", help="执行单个提示词后退出")
    return parser.parse_args()


async def run_mcp() -> None:
    """MCP 运行器的主入口点。"""
    args = parse_args()
    runner = MCPRunner()

    try:
        await runner.initialize(args.connection, args.server_url)

        if args.prompt:
            await runner.run_single_prompt(args.prompt)
        elif args.interactive:
            await runner.run_interactive()
        else:
            await runner.run_default()

    except KeyboardInterrupt:
        logger.info("程序已被用户中断")
    except Exception as e:
        logger.error(f"运行 MCPAgent 时出错：{str(e)}", exc_info=True)
        sys.exit(1)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(run_mcp())
