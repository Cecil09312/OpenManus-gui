import logging
import sys

logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(sys.stderr)])

import argparse
import asyncio
import atexit
import json
from inspect import Parameter, Signature
from typing import Any, Dict, Optional

from mcp.server.fastmcp import FastMCP

from app.logger import logger
from app.tool.base import BaseTool
from app.tool.bash import Bash
from app.tool.browser_use_tool import BrowserUseTool
from app.tool.str_replace_editor import StrReplaceEditor
from app.tool.terminate import Terminate


class MCPServer:
    """MCP服务器实现，包含工具注册和管理。"""

    def __init__(self, name: str = "openmanus"):
        self.server = FastMCP(name)
        self.tools: Dict[str, BaseTool] = {}

        # 初始化工具
        self.tools["bash"] = Bash()
        self.tools["browser"] = BrowserUseTool()
        self.tools["editor"] = StrReplaceEditor()
        self.tools["terminate"] = Terminate()

    def register_tool(self, tool: BaseTool, method_name: Optional[str] = None) -> None:
        """注册工具并进行参数验证和文档生成。"""
        tool_name = method_name or tool.name
        tool_param = tool.to_param()
        tool_function = tool_param["function"]

        # 定义要注册的异步函数
        async def tool_method(**kwargs):
            logger.info(f"正在执行 {tool_name}：{kwargs}")
            result = await tool.execute(**kwargs)

            logger.info(f"{tool_name} 的执行结果：{result}")

            # 处理不同类型的结果（匹配原始逻辑）
            if hasattr(result, "model_dump"):
                return json.dumps(result.model_dump())
            elif isinstance(result, dict):
                return json.dumps(result)
            return result

        # 设置方法元数据
        tool_method.__name__ = tool_name
        tool_method.__doc__ = self._build_docstring(tool_function)
        tool_method.__signature__ = self._build_signature(tool_function)

        # 存储参数架构（对于以编程方式访问它的工具很重要）
        param_props = tool_function.get("parameters", {}).get("properties", {})
        required_params = tool_function.get("parameters", {}).get("required", [])
        tool_method._parameter_schema = {
            param_name: {
                "description": param_details.get("description", ""),
                "type": param_details.get("type", "any"),
                "required": param_name in required_params,
            }
            for param_name, param_details in param_props.items()
        }

        # 向服务器注册
        self.server.tool()(tool_method)
        logger.info(f"已注册工具：{tool_name}")

    def _build_docstring(self, tool_function: dict) -> str:
        """从工具函数元数据构建格式化的文档字符串。"""
        description = tool_function.get("description", "")
        param_props = tool_function.get("parameters", {}).get("properties", {})
        required_params = tool_function.get("parameters", {}).get("required", [])

        # 构建文档字符串（匹配原始格式）
        docstring = description
        if param_props:
            docstring += "\n\n参数：\n"
            for param_name, param_details in param_props.items():
                required_str = (
                    "（必需）" if param_name in required_params else "（可选）"
                )
                param_type = param_details.get("type", "any")
                param_desc = param_details.get("description", "")
                docstring += (
                    f"    {param_name} ({param_type}) {required_str}: {param_desc}\n"
                )

        return docstring

    def _build_signature(self, tool_function: dict) -> Signature:
        """从工具函数元数据构建函数签名。"""
        param_props = tool_function.get("parameters", {}).get("properties", {})
        required_params = tool_function.get("parameters", {}).get("required", [])

        parameters = []

        # 遵循原始类型映射
        for param_name, param_details in param_props.items():
            param_type = param_details.get("type", "")
            default = Parameter.empty if param_name in required_params else None

            # 将 JSON Schema 类型映射到 Python 类型（与原始相同）
            annotation = Any
            if param_type == "string":
                annotation = str
            elif param_type == "integer":
                annotation = int
            elif param_type == "number":
                annotation = float
            elif param_type == "boolean":
                annotation = bool
            elif param_type == "object":
                annotation = dict
            elif param_type == "array":
                annotation = list

            # 创建与原始架构相同的参数
            param = Parameter(
                name=param_name,
                kind=Parameter.KEYWORD_ONLY,
                default=default,
                annotation=annotation,
            )
            parameters.append(param)

        return Signature(parameters=parameters)

    async def cleanup(self) -> None:
        """清理服务器资源。"""
        logger.info("正在清理资源")
        # 遵循原始清理逻辑 - 仅清理浏览器工具
        if "browser" in self.tools and hasattr(self.tools["browser"], "cleanup"):
            await self.tools["browser"].cleanup()

    def register_all_tools(self) -> None:
        """向服务器注册所有工具。"""
        for tool in self.tools.values():
            self.register_tool(tool)

    def run(self, transport: str = "stdio") -> None:
        """运行MCP服务器。"""
        # 注册所有工具
        self.register_all_tools()

        # 注册清理函数（匹配原始行为）
        atexit.register(lambda: asyncio.run(self.cleanup()))

        # 启动服务器（使用与原始相同的日志记录）
        logger.info(f"正在启动 OpenManus 服务器（{transport} 模式）")
        self.server.run(transport=transport)


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="OpenManus MCP 服务器")
    parser.add_argument(
        "--transport",
        choices=["stdio"],
        default="stdio",
        help="通信方式：stdio 或 http（默认：stdio）",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    # 创建并运行服务器（保持原有流程）
    server = MCPServer()
    server.run(transport=args.transport)
