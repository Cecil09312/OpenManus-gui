import json
from typing import TYPE_CHECKING, Optional

from pydantic import Field, model_validator

from app.agent.toolcall import ToolCallAgent
from app.logger import logger
from app.prompt.browser import NEXT_STEP_PROMPT, SYSTEM_PROMPT
from app.schema import Message, ToolChoice
from app.tool import BrowserUseTool, Terminate, ToolCollection
from app.tool.sandbox.sb_browser_tool import SandboxBrowserTool


# 避免循环导入，如果 BrowserAgent 需要 BrowserContextHelper
if TYPE_CHECKING:
    from app.agent.base import BaseAgent  # 或者 memory 定义所在的位置


class BrowserContextHelper:
    def __init__(self, agent: "BaseAgent"):
        self.agent = agent
        self._current_base64_image: Optional[str] = None

    async def get_browser_state(self) -> Optional[dict]:
        browser_tool = self.agent.available_tools.get_tool(BrowserUseTool().name)
        if not browser_tool:
            browser_tool = self.agent.available_tools.get_tool(
                SandboxBrowserTool().name
            )
        if not browser_tool or not hasattr(browser_tool, "get_current_state"):
            logger.warning("未找到 BrowserUseTool 或该工具没有 get_current_state 方法")
            return None
        try:
            result = await browser_tool.get_current_state()
            if result.error:
                logger.debug(f"浏览器状态错误：{result.error}")
                return None
            if hasattr(result, "base64_image") and result.base64_image:
                self._current_base64_image = result.base64_image
            else:
                self._current_base64_image = None
            return json.loads(result.output)
        except Exception as e:
            logger.debug(f"获取浏览器状态失败：{str(e)}")
            return None

    async def format_next_step_prompt(self) -> str:
        """获取浏览器状态并格式化浏览器提示。"""
        browser_state = await self.get_browser_state()
        url_info, tabs_info, content_above_info, content_below_info = "", "", "", ""
        results_info = ""  # 或者从智能体获取（如果其他地方需要）

        if browser_state and not browser_state.get("error"):
            url_info = f"\n   URL: {browser_state.get('url', 'N/A')}\n   Title: {browser_state.get('title', 'N/A')}"
            tabs = browser_state.get("tabs", [])
            if tabs:
                tabs_info = f"\n   {len(tabs)} tab(s) available"
            pixels_above = browser_state.get("pixels_above", 0)
            pixels_below = browser_state.get("pixels_below", 0)
            if pixels_above > 0:
                content_above_info = f" ({pixels_above} pixels)"
            if pixels_below > 0:
                content_below_info = f" ({pixels_below} pixels)"

            if self._current_base64_image:
                image_message = Message.user_message(
                    content="当前浏览器截图：",
                    base64_image=self._current_base64_image,
                )
                self.agent.memory.add_message(image_message)
                self._current_base64_image = None  # 添加后消费图片

        return NEXT_STEP_PROMPT.format(
            url_placeholder=url_info,
            tabs_placeholder=tabs_info,
            content_above_placeholder=content_above_info,
            content_below_placeholder=content_below_info,
            results_placeholder=results_info,
        )

    async def cleanup_browser(self):
        browser_tool = self.agent.available_tools.get_tool(BrowserUseTool().name)
        if browser_tool and hasattr(browser_tool, "cleanup"):
            await browser_tool.cleanup()


class BrowserAgent(ToolCallAgent):
    """
    一个使用 browser_use 库来控制浏览器的浏览器智能体。

    此智能体可以浏览网页、与元素交互、填写表单、
    提取内容，并执行其他基于浏览器的操作来完成任务。
    """

    name: str = "browser"
    description: str = "一个能够控制浏览器来完成任务的浏览器智能体"

    system_prompt: str = SYSTEM_PROMPT
    next_step_prompt: str = NEXT_STEP_PROMPT

    max_observe: int = 10000
    max_steps: int = 30

    # 配置可用工具
    available_tools: ToolCollection = Field(
        default_factory=lambda: ToolCollection(BrowserUseTool(), Terminate())
    )

    # 使用 Auto 作为工具选择，允许工具使用和自由格式响应
    tool_choices: ToolChoice = ToolChoice.AUTO
    special_tool_names: list[str] = Field(default_factory=lambda: [Terminate().name])

    browser_context_helper: Optional[BrowserContextHelper] = None

    @model_validator(mode="after")
    def initialize_helper(self) -> "BrowserAgent":
        self.browser_context_helper = BrowserContextHelper(self)
        return self

    async def think(self) -> bool:
        """处理当前状态并使用工具决定下一步动作，同时添加浏览器状态信息。"""
        self.next_step_prompt = (
            await self.browser_context_helper.format_next_step_prompt()
        )
        return await super().think()

    async def cleanup(self):
        """通过调用父类清理方法来清理浏览器智能体资源。"""
        await self.browser_context_helper.cleanup_browser()
