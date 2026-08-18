import asyncio
import base64
import json
import os
import re
import tempfile
from typing import ClassVar, Generic, Optional, TypeVar

from browser_use import Browser as BrowserUseBrowser
from browser_use import BrowserConfig
from browser_use.browser.context import BrowserContext, BrowserContextConfig
from browser_use.dom.service import DomService
from openai import OpenAI
from pydantic import Field, field_validator
from pydantic_core.core_schema import ValidationInfo

from app.config import config
from app.llm import LLM
from app.logger import logger
from app.tool.base import BaseTool, ToolResult
from app.tool.web_search import WebSearch

_BROWSER_DESCRIPTION = """\
一个强大的浏览器自动化工具，允许通过各种操作与网页交互。
* 此工具提供控制浏览器会话、浏览网页和提取信息的命令
* 它在调用之间保持状态，使浏览器会话保持活跃直到显式关闭
* 当你需要浏览网站、填写表单、点击按钮、提取内容或执行网络搜索时使用此工具
* 每个操作需要工具依赖中定义的特定参数

主要功能包括：
* 导航：前往特定 URL、后退、搜索网络或刷新页面
* 交互：点击元素、输入文本、从下拉菜单中选择、发送键盘命令
* 滚动：按像素量上下滚动或滚动到特定文本
* 内容提取：根据特定目标从网页提取和分析内容
* 标签页管理：在标签页之间切换、打开新标签页或关闭标签页

注意：使用元素索引时，请参考当前浏览器状态中显示的编号元素。
"""

Context = TypeVar("Context")


class BrowserUseTool(BaseTool, Generic[Context]):
    name: str = "browser_use"
    description: str = _BROWSER_DESCRIPTION
    parameters: dict = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "go_to_url",
                    "click_element",
                    "input_text",
                    "scroll_down",
                    "scroll_up",
                    "scroll_to_text",
                    "send_keys",
                    "get_dropdown_options",
                    "select_dropdown_option",
                    "go_back",
                    "web_search",
                    "wait",
                    "extract_content",
                    "switch_tab",
                    "open_tab",
                    "close_tab",
                ],
                "description": "要执行的浏览器操作",
            },
            "url": {
                "type": "string",
                "description": "'go_to_url' 或 'open_tab' 操作的 URL",
            },
            "index": {
                "type": "integer",
                "description": "'click_element'、'input_text'、'get_dropdown_options' 或 'select_dropdown_option' 操作的元素索引",
            },
            "text": {
                "type": "string",
                "description": "'input_text'、'scroll_to_text' 或 'select_dropdown_option' 操作的文本",
            },
            "scroll_amount": {
                "type": "integer",
                "description": "'scroll_down' 或 'scroll_up' 操作要滚动的像素数（正数向下，负数向上）",
            },
            "tab_id": {
                "type": "integer",
                "description": "'switch_tab' 操作的标签页 ID",
            },
            "query": {
                "type": "string",
                "description": "'web_search' 操作的搜索查询",
            },
            "goal": {
                "type": "string",
                "description": "'extract_content' 操作的提取目标",
            },
            "keys": {
                "type": "string",
                "description": "'send_keys' 操作要发送的按键",
            },
            "seconds": {
                "type": "integer",
                "description": "'wait' 操作要等待的秒数",
            },
            "instruction": {
                "type": "string",
                "description": "'gui_action' 操作的指令，用于 gui-plus 模型理解并执行视觉交互操作",
            },
        },
        "required": ["action"],
        "dependencies": {
            "go_to_url": ["url"],
            "click_element": ["index"],
            "input_text": ["index", "text"],
            "switch_tab": ["tab_id"],
            "open_tab": ["url"],
            "scroll_down": ["scroll_amount"],
            "scroll_up": ["scroll_amount"],
            "scroll_to_text": ["text"],
            "send_keys": ["keys"],
            "get_dropdown_options": ["index"],
            "select_dropdown_option": ["index", "text"],
            "go_back": [],
            "web_search": ["query"],
            "wait": ["seconds"],
            "extract_content": ["goal"],
            "gui_action": ["instruction"],
        },
    }

    # gui-plus 模型系统提示词
    GUI_PLUS_SYSTEM_PROMPT: ClassVar[str] = (
        """## 1. 核心角色 (Core Role)你是一个顶级的AI视觉操作代理。你的任务是分析电脑屏幕截图，理解用户的指令，然后将任务分解为单一、精确的GUI原子操作。## 2. [CRITICAL] JSON Schema & 绝对规则你的输出**必须**是一个严格符合以下规则的JSON对象。**任何偏差都将导致失败**。- **[R1] 严格的JSON**: 你的回复**必须**是且**只能是**一个JSON对象。禁止在JSON代码块前后添加任何文本、注释或解释。- **[R2] 严格的Parameters结构**:`thought`对象的结构: "在这里用一句话简要描述你的思考过程。例如：用户想打开浏览器，我看到了桌面上的Chrome浏览器图标，所以下一步是点击它。"- **[R3] 精确的Action值**: `action`字段的值**必须**是`## 3. 工具集`中定义的一个大写字符串（例如 `"CLICK"`, `"TYPE"`），不允许有任何前导/后置空格或大小写变化。- **[R4] 严格的Parameters结构**: `parameters`对象的结构**必须**与所选Action在`## 3. 工具集`中定义的模板**完全一致**。键名、值类型都必须精确匹配。## 3. 工具集 (Available Actions)### CLICK- **功能**: 单击屏幕。- **Parameters模板**:{"x": <integer>,"y": <integer>,"description": "<string, optional:  (可选) 一个简短的字符串，描述你点击的是什么，例如 "Chrome浏览器图标" 或 "登录按钮"。>"}### TYPE- **功能**: 输入文本。- **Parameters模板**:{"text": "<string>","needs_enter": <boolean>}### SCROLL- **功能**: 滚动窗口。- **Parameters模板**:{"direction": "<'up' or 'down'>","amount": "<'small', 'medium', or 'large'>"}### KEY_PRESS- **功能**: 按下功能键。- **Parameters模板**:{"key": "<string: e.g., 'enter', 'esc', 'alt+f4'>"}### FINISH- **功能**: 任务成功完成。- **Parameters模板**:{"message": "<string: 总结任务完成情况>"}### FAILE- **功能**: 任务无法完成。- **Parameters模板**:{"reason": "<string: 清晰解释失败原因>"}## 4. 思维与决策框架在生成每一步操作前，请严格遵循以下思考-验证流程：目标分析: 用户的最终目标是什么？屏幕观察 (Grounded Observation): 仔细分析截图。你的决策必须基于截图中存在的视觉证据。 如果你看不见某个元素，你就不能与它交互。行动决策: 基于目标和可见的元素，选择最合适的工具。构建输出:a. 在thought字段中记录你的思考。b. 选择一个action。c. 精确复制该action的parameters模板，并填充值。最终验证 (Self-Correction): 在输出前，最后检查一遍：我的回复是纯粹的JSON吗？action的值是否正确无误（大写、无空格）？parameters的结构是否与模板100%一致？例如，对于CLICK，是否有独立的x和y键，并且它们的值都是整数？"""
    )

    lock: asyncio.Lock = Field(default_factory=asyncio.Lock)
    browser: Optional[BrowserUseBrowser] = Field(default=None, exclude=True)
    context: Optional[BrowserContext] = Field(default=None, exclude=True)
    dom_service: Optional[DomService] = Field(default=None, exclude=True)
    web_search_tool: WebSearch = Field(default_factory=WebSearch, exclude=True)

    # 用于通用功能的上下文
    tool_context: Optional[Context] = Field(default=None, exclude=True)

    llm: Optional[LLM] = Field(default_factory=LLM)

    @field_validator("parameters", mode="before")
    def validate_parameters(cls, v: dict, info: ValidationInfo) -> dict:
        if not v:
            raise ValueError("参数不能为空")
        return v

    async def _ensure_browser_initialized(self) -> BrowserContext:
        """确保浏览器和上下文已初始化。"""
        if self.browser is None:
            browser_config_kwargs = {"headless": False, "disable_security": True}

            if config.browser_config:
                from browser_use.browser.browser import ProxySettings

                # 处理代理设置。
                if config.browser_config.proxy and config.browser_config.proxy.server:
                    browser_config_kwargs["proxy"] = ProxySettings(
                        server=config.browser_config.proxy.server,
                        username=config.browser_config.proxy.username,
                        password=config.browser_config.proxy.password,
                    )

                browser_attrs = [
                    "headless",
                    "disable_security",
                    "extra_chromium_args",
                    "chrome_instance_path",
                    "wss_url",
                    "cdp_url",
                ]

                for attr in browser_attrs:
                    value = getattr(config.browser_config, attr, None)
                    if value is not None:
                        if not isinstance(value, list) or value:
                            browser_config_kwargs[attr] = value

            self.browser = BrowserUseBrowser(BrowserConfig(**browser_config_kwargs))

        if self.context is None:
            context_config = BrowserContextConfig()

            # 如果配置中有上下文配置，则使用它。
            if (
                config.browser_config
                and hasattr(config.browser_config, "new_context_config")
                and config.browser_config.new_context_config
            ):
                context_config = config.browser_config.new_context_config

            self.context = await self.browser.new_context(context_config)
            self.dom_service = DomService(await self.context.get_current_page())

            # 注入反检测脚本，隐藏自动化浏览器特征
            page = await self.context.get_current_page()
            await page.add_init_script(
                """
                // 隐藏 webdriver 标志
                Object.defineProperty(navigator, 'webdriver', { get: () => false });
                // 伪装 plugins
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });
                // 伪装 languages
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['zh-CN', 'zh', 'en']
                });
                // 隐藏自动化相关属性
                delete navigator.__proto__.webdriver;
            """
            )

        return self.context

    async def _call_gui_plus_model(self, image_path: str, instruction: str) -> str:
        """
        调用 gui-plus 模型获取 GUI 操作指令。

        参数：
            image_path: 截图文件路径
            instruction: 用户指令

        返回：
            gui-plus 模型的响应文本
        """
        # 读取图片并转换为 base64
        with open(image_path, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode("utf-8")

        image_data_url = f"data:image/png;base64,{base64_image}"

        messages = [
            {"role": "system", "content": self.GUI_PLUS_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                    {"type": "text", "text": instruction},
                ],
            },
        ]

        # 使用异步方式调用 OpenAI API
        client = OpenAI(
            api_key=os.getenv("DASHSCOPE_API_KEY"),
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )

        # 在线程池中执行同步调用
        loop = asyncio.get_event_loop()
        completion = await loop.run_in_executor(
            None,
            lambda: client.chat.completions.create(model="gui-plus", messages=messages),
        )

        return completion.choices[0].message.content

    def _parse_gui_action(self, response_text: str) -> Optional[dict]:
        """
        解析 gui-plus 模型返回的 JSON 响应。

        参数：
            response_text: gui-plus 模型的响应文本

        返回：
            解析后的操作字典，如果解析失败则返回 None
        """
        try:
            # 尝试提取 JSON 部分
            match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if match:
                return json.loads(match.group())
        except json.JSONDecodeError:
            pass
        return None

    async def _execute_gui_action(self, action: dict, page) -> str:
        """
        执行 gui-plus 模型返回的 GUI 操作。

        参数：
            action: gui-plus 模型返回的操作字典
            page: Playwright 页面对象

        返回：
            操作执行结果描述
        """
        action_type = action.get("action", "")
        params = action.get("parameters", {})

        if action_type == "CLICK":
            x = params.get("x", 0)
            y = params.get("y", 0)
            description = params.get("description", "")
            await page.mouse.click(x, y)
            return f"已点击坐标 ({x}, {y}) - {description}"

        elif action_type == "TYPE":
            text = params.get("text", "")
            needs_enter = params.get("needs_enter", False)
            await page.keyboard.type(text)
            if needs_enter:
                await page.keyboard.press("Enter")
            return f"已输入文本: {text}"

        elif action_type == "SCROLL":
            direction = params.get("direction", "down")
            amount = params.get("amount", "medium")
            scroll_amount = {"small": 100, "medium": 300, "large": 500}.get(amount, 300)
            delta = -scroll_amount if direction == "up" else scroll_amount
            await page.mouse.wheel(0, delta)
            return f"已滚动 {direction} {amount}"

        elif action_type == "KEY_PRESS":
            key = params.get("key", "enter")
            await page.keyboard.press(key)
            return f"已按键: {key}"

        elif action_type == "FINISH":
            message = params.get("message", "任务完成")
            return f"任务完成: {message}"

        elif action_type == "FAILE":
            reason = params.get("reason", "未知原因")
            return f"任务失败: {reason}"

        return f"未知操作类型: {action_type}"

    async def _perform_gui_action_on_page(self, page, instruction: str) -> str:
        """
        在当前页面上执行 GUI 操作。
        截图页面，调用 gui-plus 模型获取操作指令，然后执行。

        参数：
            page: Playwright 页面对象
            instruction: 用户指令

        返回：
            操作执行结果描述
        """
        # 截图当前页面
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name

        await page.screenshot(path=tmp_path)

        try:
            # 调用 gui-plus 模型
            response = await self._call_gui_plus_model(tmp_path, instruction)
            logger.info(f"gui-plus 模型响应: {response}")

            # 解析响应
            action = self._parse_gui_action(response)
            if not action:
                return f"无法解析 gui-plus 模型响应: {response}"

            # 执行操作
            result = await self._execute_gui_action(action, page)
            return result
        finally:
            # 清理临时文件
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    async def _call_gui_plus_model(self, image_path: str, instruction: str) -> str:
        """
        调用 gui-plus 模型获取 GUI 操作指令。

        参数：
            image_path: 截图文件路径
            instruction: 用户指令

        返回：
            gui-plus 模型的响应文本
        """
        # 读取图片并转换为 base64
        with open(image_path, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode("utf-8")

        image_data_url = f"data:image/png;base64,{base64_image}"

        messages = [
            {"role": "system", "content": self.GUI_PLUS_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                    {"type": "text", "text": instruction},
                ],
            },
        ]

        # 使用异步方式调用 OpenAI API
        client = OpenAI(
            api_key=os.getenv("DASHSCOPE_API_KEY"),
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )

        # 在线程池中执行同步调用
        loop = asyncio.get_event_loop()
        completion = await loop.run_in_executor(
            None,
            lambda: client.chat.completions.create(model="gui-plus", messages=messages),
        )

        return completion.choices[0].message.content

    def _parse_gui_action(self, response_text: str) -> Optional[dict]:
        """
        解析 gui-plus 模型返回的 JSON 响应。

        参数：
            response_text: gui-plus 模型的响应文本

        返回：
            解析后的操作字典，如果解析失败则返回 None
        """
        try:
            # 尝试提取 JSON 部分
            match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if match:
                return json.loads(match.group())
        except json.JSONDecodeError:
            pass
        return None

    async def _execute_gui_action(self, action: dict, page) -> str:
        """
        执行 gui-plus 模型返回的 GUI 操作。

        参数：
            action: gui-plus 模型返回的操作字典
            page: Playwright 页面对象

        返回：
            操作执行结果描述
        """
        action_type = action.get("action", "")
        params = action.get("parameters", {})

        if action_type == "CLICK":
            x = params.get("x", 0)
            y = params.get("y", 0)
            description = params.get("description", "")
            await page.mouse.click(x, y)
            return f"已点击坐标 ({x}, {y}) - {description}"

        elif action_type == "TYPE":
            text = params.get("text", "")
            needs_enter = params.get("needs_enter", False)
            await page.keyboard.type(text)
            if needs_enter:
                await page.keyboard.press("Enter")
            return f"已输入文本: {text}"

        elif action_type == "SCROLL":
            direction = params.get("direction", "down")
            amount = params.get("amount", "medium")
            scroll_amount = {"small": 100, "medium": 300, "large": 500}.get(amount, 300)
            delta = -scroll_amount if direction == "up" else scroll_amount
            await page.mouse.wheel(0, delta)
            return f"已滚动 {direction} {amount}"

        elif action_type == "KEY_PRESS":
            key = params.get("key", "enter")
            await page.keyboard.press(key)
            return f"已按键: {key}"

        elif action_type == "FINISH":
            message = params.get("message", "任务完成")
            return f"任务完成: {message}"

        elif action_type == "FAILE":
            reason = params.get("reason", "未知原因")
            return f"任务失败: {reason}"

        return f"未知操作类型: {action_type}"

    async def _perform_gui_action_on_page(self, page, instruction: str) -> str:
        """
        在当前页面上执行 GUI 操作。
        截图页面，调用 gui-plus 模型获取操作指令，然后执行。

        参数：
            page: Playwright 页面对象
            instruction: 用户指令

        返回：
            操作执行结果描述
        """
        # 截图当前页面
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name

        await page.screenshot(path=tmp_path)

        try:
            # 调用 gui-plus 模型
            response = await self._call_gui_plus_model(tmp_path, instruction)
            logger.info(f"gui-plus 模型响应: {response}")

            # 解析响应
            action = self._parse_gui_action(response)
            if not action:
                return f"无法解析 gui-plus 模型响应: {response}"

            # 执行操作
            result = await self._execute_gui_action(action, page)
            return result
        finally:
            # 清理临时文件
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    async def execute(
        self,
        action: str,
        url: Optional[str] = None,
        index: Optional[int] = None,
        text: Optional[str] = None,
        scroll_amount: Optional[int] = None,
        tab_id: Optional[int] = None,
        query: Optional[str] = None,
        goal: Optional[str] = None,
        keys: Optional[str] = None,
        seconds: Optional[int] = None,
        instruction: Optional[str] = None,
        **kwargs,
    ) -> ToolResult:
        """
        执行指定的浏览器操作。

        参数：
            action: 要执行的浏览器操作
            url: 导航或新标签页的 URL
            index: 点击或输入操作的元素索引
            text: 输入操作的文本或搜索查询
            scroll_amount: 滚动操作要滚动的像素数
            tab_id: switch_tab 操作的标签页 ID
            query: Google 搜索的查询
            goal: 内容提取的提取目标
            keys: 键盘操作要发送的按键
            seconds: 等待的秒数
            **kwargs: 额外参数

        返回：
            包含操作输出或错误的 ToolResult
        """
        async with self.lock:
            try:
                context = await self._ensure_browser_initialized()

                # 从配置中获取最大内容长度
                max_content_length = getattr(
                    config.browser_config, "max_content_length", 2000
                )

                # 导航操作
                if action == "go_to_url":
                    if not url:
                        return ToolResult(error="'go_to_url' 操作需要提供 URL")
                    page = await context.get_current_page()
                    await page.goto(url)
                    await page.wait_for_load_state()
                    return ToolResult(output=f"已导航到 {url}")

                elif action == "go_back":
                    await context.go_back()
                    return ToolResult(output="已返回上一页")

                elif action == "refresh":
                    await context.refresh_page()
                    return ToolResult(output="已刷新当前页面")

                elif action == "web_search":
                    if not query:
                        return ToolResult(error="'web_search' 操作需要提供搜索查询")
                    # 执行网页搜索，获取更多结果以提供更全面的信息
                    search_response = await self.web_search_tool.execute(
                        query=query, fetch_content=True, num_results=10
                    )
                    # 检查搜索结果是否为空
                    if not search_response.results:
                        return ToolResult(
                            error=f"搜索 '{query}' 未返回任何结果，请尝试更换关键词或使用其他搜索方式"
                        )
                    # 导航到第一个搜索结果
                    first_search_result = search_response.results[0]
                    url_to_navigate = first_search_result.url
                    if not url_to_navigate:
                        return ToolResult(error="搜索结果中未包含有效URL")

                    page = await context.get_current_page()
                    await page.goto(url_to_navigate)
                    await page.wait_for_load_state()

                    return search_response

                # 元素交互操作
                elif action == "click_element":
                    if index is None:
                        return ToolResult(error="'click_element' 操作需要提供元素索引")
                    element = await context.get_dom_element_by_index(index)
                    if not element:
                        return ToolResult(error=f"未找到索引为 {index} 的元素")
                    download_path = await context._click_element_node(element)
                    output = f"已点击索引为 {index} 的元素"
                    if download_path:
                        output += f" - 文件已下载到 {download_path}"
                    return ToolResult(output=output)

                elif action == "input_text":
                    if index is None or not text:
                        return ToolResult(error="'input_text' 操作需要提供索引和文本")
                    element = await context.get_dom_element_by_index(index)
                    if not element:
                        return ToolResult(error=f"未找到索引为 {index} 的元素")
                    await context._input_text_element_node(element, text)
                    return ToolResult(
                        output=f"已在索引为 {index} 的元素中输入 '{text}'"
                    )

                elif action == "scroll_down" or action == "scroll_up":
                    direction = 1 if action == "scroll_down" else -1
                    amount = (
                        scroll_amount
                        if scroll_amount is not None
                        else context.config.browser_window_size["height"]
                    )
                    await context.execute_javascript(
                        f"window.scrollBy(0, {direction * amount});"
                    )
                    return ToolResult(
                        output=f"已{'向下' if direction > 0 else '向上'}滚动 {amount} 像素"
                    )

                elif action == "scroll_to_text":
                    if not text:
                        return ToolResult(error="'scroll_to_text' 操作需要提供文本")
                    page = await context.get_current_page()
                    try:
                        locator = page.get_by_text(text, exact=False)
                        await locator.scroll_into_view_if_needed()
                        return ToolResult(output=f"已滚动到文本：'{text}'")
                    except Exception as e:
                        return ToolResult(error=f"滚动到文本失败：{str(e)}")

                elif action == "send_keys":
                    if not keys:
                        return ToolResult(error="'send_keys' 操作需要提供按键")
                    page = await context.get_current_page()
                    await page.keyboard.press(keys)
                    return ToolResult(output=f"已发送按键：{keys}")

                elif action == "get_dropdown_options":
                    if index is None:
                        return ToolResult(
                            error="'get_dropdown_options' 操作需要提供元素索引"
                        )
                    element = await context.get_dom_element_by_index(index)
                    if not element:
                        return ToolResult(error=f"未找到索引为 {index} 的元素")
                    page = await context.get_current_page()
                    options = await page.evaluate(
                        """
                        (xpath) => {
                            const select = document.evaluate(xpath, document, null,
                                XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                            if (!select) return null;
                            return Array.from(select.options).map(opt => ({
                                text: opt.text,
                                value: opt.value,
                                index: opt.index
                            }));
                        }
                    """,
                        element.xpath,
                    )
                    return ToolResult(output=f"下拉菜单选项：{options}")

                elif action == "select_dropdown_option":
                    if index is None or not text:
                        return ToolResult(
                            error="'select_dropdown_option' 操作需要提供索引和文本"
                        )
                    element = await context.get_dom_element_by_index(index)
                    if not element:
                        return ToolResult(error=f"未找到索引为 {index} 的元素")
                    page = await context.get_current_page()
                    await page.select_option(element.xpath, label=text)
                    return ToolResult(
                        output=f"已从索引为 {index} 的下拉菜单中选择选项 '{text}'"
                    )

                # Content extraction actions
                elif action == "extract_content":
                    if not goal:
                        return ToolResult(
                            error="'extract_content' 操作需要提供提取目标"
                        )

                    page = await context.get_current_page()
                    import markdownify

                    content = markdownify.markdownify(await page.content())

                    prompt = f"""\
你的任务是提取页面内容。你将获得一个页面和一个目标，你应该从页面中提取与此目标相关的所有信息。如果目标不明确，请总结页面。以 JSON 格式响应。
提取目标：{goal}

页面内容：
{content[:max_content_length]}
"""
                    messages = [{"role": "system", "content": prompt}]

                    # 定义提取函数架构
                    extraction_function = {
                        "type": "function",
                        "function": {
                            "name": "extract_content",
                            "description": "根据目标从网页中提取特定信息",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "extracted_content": {
                                        "type": "object",
                                        "description": "根据目标从页面中提取的内容",
                                        "properties": {
                                            "text": {
                                                "type": "string",
                                                "description": "从页面中提取的文本内容",
                                            },
                                            "metadata": {
                                                "type": "object",
                                                "description": "关于提取内容的附加元数据",
                                                "properties": {
                                                    "source": {
                                                        "type": "string",
                                                        "description": "提取内容的来源",
                                                    }
                                                },
                                            },
                                        },
                                    }
                                },
                                "required": ["extracted_content"],
                            },
                        },
                    }

                    # 使用 LLM 通过必需的函数调用来提取内容
                    response = await self.llm.ask_tool(
                        messages,
                        tools=[extraction_function],
                        tool_choice="auto",
                    )

                    if response and response.tool_calls:
                        raw_args = response.tool_calls[0].function.arguments
                        try:
                            args = json.loads(raw_args)
                        except json.JSONDecodeError:
                            # LLM 可能返回多个 JSON 对象拼接，尝试提取第一个完整对象
                            import re

                            match = re.match(r"\{.*\}", raw_args, re.DOTALL)
                            if match:
                                try:
                                    args = json.loads(match.group())
                                except json.JSONDecodeError:
                                    return ToolResult(
                                        output=f"内容提取失败：LLM返回了无效的JSON格式。原始内容：\n{content[:500]}\n"
                                    )
                            else:
                                return ToolResult(
                                    output=f"内容提取失败：LLM返回了无效的JSON格式。原始内容：\n{content[:500]}\n"
                                )
                        extracted_content = args.get("extracted_content", {})
                        return ToolResult(
                            output=f"从页面中提取的内容：\n{extracted_content}\n"
                        )

                    return ToolResult(output="未从页面中提取到任何内容。")

                # 标签页管理操作
                elif action == "switch_tab":
                    if tab_id is None:
                        return ToolResult(error="'switch_tab' 操作需要提供标签页 ID")
                    await context.switch_to_tab(tab_id)
                    page = await context.get_current_page()
                    await page.wait_for_load_state()
                    return ToolResult(output=f"已切换到标签页 {tab_id}")

                elif action == "open_tab":
                    if not url:
                        return ToolResult(error="'open_tab' 操作需要提供 URL")
                    await context.create_new_tab(url)
                    return ToolResult(output=f"已打开新标签页，URL：{url}")

                elif action == "close_tab":
                    await context.close_current_tab()
                    return ToolResult(output="已关闭当前标签页")

                # 实用工具操作
                elif action == "wait":
                    seconds_to_wait = seconds if seconds is not None else 3
                    await asyncio.sleep(seconds_to_wait)
                    return ToolResult(output=f"已等待 {seconds_to_wait} 秒")

                # gui-plus 视觉交互操作
                elif action == "gui_action":
                    if not instruction:
                        return ToolResult(error="'gui_action' 操作需要提供指令")
                    page = await context.get_current_page()
                    result = await self._perform_gui_action_on_page(page, instruction)
                    return ToolResult(output=result)

                else:
                    return ToolResult(error=f"未知的操作：{action}")

            except Exception as e:
                return ToolResult(error=f"浏览器操作 '{action}' 失败：{str(e)}")

    async def get_current_state(
        self, context: Optional[BrowserContext] = None
    ) -> ToolResult:
        """
        获取当前浏览器状态作为 ToolResult。
        如果未提供 context，则使用 self.context。
        """
        try:
            # 使用提供的 context 或回退到 self.context
            ctx = context or self.context
            if not ctx:
                return ToolResult(error="浏览器上下文未初始化")

            state = await ctx.get_state(cache_clickable_elements_hashes=False)

            # 如果不存在则创建 viewport_info 字典
            viewport_height = 0
            if hasattr(state, "viewport_info") and state.viewport_info:
                viewport_height = state.viewport_info.height
            elif hasattr(ctx, "config") and hasattr(ctx.config, "browser_window_size"):
                viewport_height = ctx.config.browser_window_size.get("height", 0)

            # 为状态截图
            page = await ctx.get_current_page()

            await page.bring_to_front()
            await page.wait_for_load_state()

            screenshot = await page.screenshot(
                full_page=True, animations="disabled", type="jpeg", quality=100
            )

            screenshot = base64.b64encode(screenshot).decode("utf-8")

            # 构建包含所有必需字段的状态信息
            state_info = {
                "url": state.url,
                "title": state.title,
                "tabs": [tab.model_dump() for tab in state.tabs],
                "help": "[0], [1], [2], etc., represent clickable indices corresponding to the elements listed. Clicking on these indices will navigate to or interact with the respective content behind them.",
                "interactive_elements": (
                    state.element_tree.clickable_elements_to_string()
                    if state.element_tree
                    else ""
                ),
                "scroll_info": {
                    "pixels_above": getattr(state, "pixels_above", 0),
                    "pixels_below": getattr(state, "pixels_below", 0),
                    "total_height": getattr(state, "pixels_above", 0)
                    + getattr(state, "pixels_below", 0)
                    + viewport_height,
                },
                "viewport_height": viewport_height,
            }

            return ToolResult(
                output=json.dumps(state_info, indent=4, ensure_ascii=False),
                base64_image=screenshot,
            )
        except Exception as e:
            return ToolResult(error=f"获取浏览器状态失败：{str(e)}")

    async def cleanup(self):
        """清理浏览器资源。"""
        async with self.lock:
            if self.context is not None:
                await self.context.close()
                self.context = None
                self.dom_service = None
            if self.browser is not None:
                await self.browser.close()
                self.browser = None

    def __del__(self):
        """确保对象销毁时进行清理。"""
        if self.browser is not None or self.context is not None:
            try:
                asyncio.run(self.cleanup())
            except RuntimeError:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(self.cleanup())
                loop.close()

    @classmethod
    def create_with_context(cls, context: Context) -> "BrowserUseTool[Context]":
        """工厂方法，使用特定上下文创建 BrowserUseTool。"""
        tool = cls()
        tool.tool_context = context
        return tool
