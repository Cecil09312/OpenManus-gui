import asyncio
import base64
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import ClassVar, Generic, Optional, TypeVar

from browser_use import Browser as BrowserUseBrowser
from browser_use import BrowserConfig
from browser_use.browser.context import BrowserContext, BrowserContextConfig
from browser_use.dom.service import DomService
from openai import AsyncOpenAI, OpenAI
from pydantic import Field, field_validator
from pydantic_core.core_schema import ValidationInfo

from app.config import config
from app.llm import LLM
from app.logger import logger
from app.tool.base import BaseTool, ToolResult
from app.tool.web_search import WebSearch

_BROWSER_DESCRIPTION = """\
一个强大的浏览器自动化工具，允许通过各种操作与网页交互。

## 核心操作（推荐使用）
* click: 点击元素 - 参数 element_description 描述要点击的元素
  示例: action="click", element_description="搜索按钮"
  示例: action="click", element_description="1月30日"
* type: 输入文本 - 参数 element_description 描述输入框，text 为要输入的文本
  示例: action="type", element_description="出发城市", text="上海"
  示例: action="type", element_description="搜索框", text="机票"

## 索引操作（基于 get_current_state 的元素索引）
* click_element: 点击指定索引的元素
* input_text: 在指定索引的输入框中输入文本

## 辅助操作
* go_to_url: 导航到 URL
* scroll_down/scroll_up: 滚动页面
* send_keys: 发送按键（Enter、Escape 等）
* wait: 等待秒数
* go_back: 返回上一页
* extract_content: 提取页面内容
* web_search: 网页搜索并导航到首个结果
* gui_action: 视觉模型直接操作页面（由 GUI-Plus 视觉模型执行点击/输入）

## 工作原理
click 和 type 内部自动选择最佳策略：
1. 优先通过 JavaScript/Playwright 分析页面定位元素
2. 如果失败，自动使用 GUI-Plus 视觉模型识别并操作
3. 自动处理页面变化和复杂交互场景

* 此工具在调用之间保持状态，浏览器会话保持活跃直到显式关闭
* 使用元素索引时，请参考当前浏览器状态中显示的编号元素。
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
                    "click",
                    "type",
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
                    "gui_action",
                ],
                "description": "要执行的浏览器操作。推荐使用 click（点击元素）和 type（输入文本）",
            },
            "url": {
                "type": "string",
                "description": "'go_to_url' 或 'open_tab' 操作的 URL",
            },
            "element_description": {
                "type": "string",
                "description": "用于 'click' 或 'type' 的元素描述（如：'搜索按钮'、'出发城市'、'1月30日'）",
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
            "click": ["element_description"],
            "type": ["element_description", "text"],
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
        调用视觉 GUI 模型获取 GUI 操作指令。

        使用 config.toml 中 [llm.vision] 配置的视觉模型（如 qwen-vl-plus）。

        参数：
            image_path: 截图文件路径
            instruction: 用户指令

        返回：
            视觉 GUI 模型的响应文本
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

        # 从配置读取视觉模型设置（api_key、base_url、model），不再硬编码模型名
        vision_settings = config.llm.get("vision", {})
        vision_api_key = vision_settings.api_key or os.getenv("DASHSCOPE_API_KEY")
        vision_base_url = vision_settings.base_url
        vision_model = vision_settings.model

        # 使用异步方式调用 OpenAI API
        client = OpenAI(api_key=vision_api_key, base_url=vision_base_url)

        # 在线程池中执行同步调用
        loop = asyncio.get_event_loop()
        completion = await loop.run_in_executor(
            None,
            lambda: client.chat.completions.create(
                model=vision_model, messages=messages
            ),
        )

        return completion.choices[0].message.content

    def _parse_gui_action(self, response_text: str) -> Optional[dict]:
        """
        解析视觉 GUI 模型返回的 JSON 响应。

        参数：
            response_text: 视觉 GUI 模型的响应文本

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
        执行视觉 GUI 模型返回的 GUI 操作。

        参数：
            action: 视觉 GUI 模型返回的操作字典
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

    async def _execute_vision_action(
        self, context: BrowserContext, vision_instruction: str, action_hint: str = "click"
    ) -> ToolResult:
        """
        使用 GUI-Plus 视觉模型执行浏览器操作。

        工作流程：
        1. 截取当前页面的截图（仅视口区域，确保坐标对齐）
        2. 将截图发送给 GUI-Plus 模型，附带用户指令
        3. 解析模型返回的 JSON（包含 action 和参数）
        4. 执行相应的操作（点击、输入、滚动等）

        参数：
            context: 浏览器上下文
            vision_instruction: 用户的视觉指令（如"点击搜索按钮"、"在出发地输入框输入上海"）
            action_hint: 操作提示（"click" 或 "type"），帮助模型理解意图

        返回：
            包含操作结果的 ToolResult
        """
        try:
            page = await context.get_current_page()
            await page.bring_to_front()
            await page.wait_for_load_state()

            # 1. 截取当前页面截图（使用 viewport 截图，不是 full_page）
            screenshot_bytes = await page.screenshot(
                type="png",
                full_page=False,  # 只截取可见区域，确保坐标对齐
            )
            screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")
            image_data_url = f"data:image/png;base64,{screenshot_base64}"

            logger.info(f"[GUI-Plus] Taking screenshot for vision_{action_hint}...")

            # 保存截图用于调试
            debug_dir = Path("debug_html")
            debug_dir.mkdir(exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            screenshot_path = debug_dir / f"{timestamp}_vision_{action_hint}.png"
            with open(screenshot_path, "wb") as f:
                f.write(screenshot_bytes)
            logger.info(f"[GUI-Plus] Screenshot saved: {screenshot_path}")

            # 2. 构建 GUI-Plus 的 system prompt
            gui_plus_system_prompt = """## 1. 核心角色 (Core Role)
你是一个顶级的AI视觉操作代理。你的任务是分析电脑屏幕截图，理解用户的指令，然后将任务分解为单一、精确的GUI原子操作。

## 2. [CRITICAL] 坐标精确性要求
- **仔细观察截图**：返回的坐标必须是目标元素的**实际中心位置**
- **不要使用固定坐标**：每次都要根据截图中的实际元素位置来确定坐标
- **输入框识别**：对于输入框，坐标应该是输入框内部的中心位置，通常在文字区域内
- **验证坐标**：确保坐标点落在目标元素的边界内

## 3. [CRITICAL] JSON Schema & 绝对规则
你的输出**必须**是一个严格符合以下规则的JSON对象。**任何偏差都将导致失败**。
- **[R1] 严格的JSON**: 你的回复**必须**是且**只能是**一个JSON对象。禁止在JSON代码块前后添加任何文本、注释或解释。
- **[R2] 精确的Action值**: `action`字段的值**必须**是下列之一：`CLICK`, `TYPE`, `SCROLL`, `KEY_PRESS`, `FINISH`, `FAIL`。
- **[R3] 严格的Parameters结构**: `parameters`对象的结构**必须**与所选Action定义的模板**完全一致**。

## 4. 工具集 (Available Actions)

### CLICK
- **功能**: 单击屏幕上的元素。
- **Parameters模板**:
{"x": <integer>, "y": <integer>, "description": "<string: 描述你点击的是什么>"}

### TYPE
- **功能**: 先点击输入框，然后输入文本。必须提供输入框中心的坐标。
- **重要**: x和y坐标必须是输入框内部文字区域的中心位置
- **Parameters模板**:
{"x": <integer>, "y": <integer>, "text": "<string>", "needs_enter": <boolean>, "description": "<string: 描述输入框>"}

### SCROLL
- **功能**: 滚动窗口。
- **Parameters模板**:
{"direction": "<'up' or 'down'>", "amount": "<'small', 'medium', or 'large'>"}

### KEY_PRESS
- **功能**: 按下功能键。
- **Parameters模板**:
{"key": "<string: e.g., 'enter', 'esc', 'alt+f4'>"}

### FINISH
- **功能**: 任务成功完成。
- **Parameters模板**:
{"message": "<string: 总结任务完成情况>"}

### FAIL
- **功能**: 任务无法完成。
- **Parameters模板**:
{"reason": "<string: 清晰解释失败原因>"}

## 5. 重要提醒
- 坐标必须根据截图中元素的**实际位置**来确定，不要使用固定值
- 输入框的坐标应该是输入框**内部中心**的位置
- 按钮的坐标应该是按钮**中心**的位置
- 仔细观察截图，找到目标元素的边界，然后计算中心坐标
"""

            # 3. 调用 GUI-Plus 模型
            # 使用环境变量或配置中的 API key
            api_key = os.getenv("DASHSCOPE_API_KEY")
            if not api_key:
                api_key = getattr(config.llm.get("vision"), "api_key", "") or getattr(
                    config.llm.get("default"), "api_key", ""
                )
            base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

            client = AsyncOpenAI(api_key=api_key, base_url=base_url)

            messages = [
                {"role": "system", "content": gui_plus_system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                        {"type": "text", "text": vision_instruction},
                    ],
                },
            ]

            logger.info(f"[GUI-Plus] Calling model with instruction: {vision_instruction}")

            completion = await client.chat.completions.create(
                model="gui-plus",
                messages=messages,
            )

            response_content = completion.choices[0].message.content
            logger.info(f"[GUI-Plus] Model response: {response_content}")

            # 4. 解析 JSON 响应
            # 预处理：修复 {"x": 139, 675} 这种格式 -> {"x": 139, "y": 675}
            fixed_response = re.sub(
                r'"x":\s*(\d+),\s*(\d+)\s*[,}]',
                lambda m: f'"x": {m.group(1)}, "y": {m.group(2)}' + (',' if m.group(0).endswith(',') else '}'),
                response_content
            )
            # 修复 {"x": [139, 675]} 这种格式 -> {"x": 139, "y": 675}
            fixed_response = re.sub(
                r'"x":\s*\[(\d+),\s*(\d+)\]',
                r'"x": \1, "y": \2',
                fixed_response
            )
            if fixed_response != response_content:
                logger.warning(f"[GUI-Plus] Fixed JSON format: {fixed_response[:200]}")

            # 尝试提取 JSON（处理 markdown 代码块、多个 JSON 对象和不完整的 JSON）
            # 去除 markdown 代码块标记
            stripped_response = re.sub(r"```(?:json)?", "", fixed_response)

            # 平衡大括号扫描，提取所有完整的 JSON 对象片段
            candidates = []
            depth = 0
            start_pos = None
            for i, ch in enumerate(stripped_response):
                if ch == "{":
                    if depth == 0:
                        start_pos = i
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0 and start_pos is not None:
                        candidates.append(stripped_response[start_pos : i + 1])
                        start_pos = None

            result = None
            # 依次解析，取最后一个包含 action 或坐标的合法 JSON
            # （模型可能输出多个 JSON 对象，后者通常是修正后的最终决策）
            for cand in candidates:
                try:
                    parsed = json.loads(cand)
                    if isinstance(parsed, dict) and (
                        "action" in parsed or "x" in parsed or "parameters" in parsed
                    ):
                        result = parsed
                except json.JSONDecodeError:
                    continue

            if result is None:
                # 尝试修复不完整的 JSON（添加缺失的 }）
                incomplete_match = re.search(r"\{[\s\S]*", stripped_response)
                if incomplete_match:
                    incomplete_json = incomplete_match.group()
                    # 计算缺失的闭合括号数量
                    open_braces = incomplete_json.count("{")
                    close_braces = incomplete_json.count("}")
                    missing_braces = open_braces - close_braces
                    if missing_braces > 0:
                        try:
                            fixed_json = incomplete_json + "}" * missing_braces
                            parsed = json.loads(fixed_json)
                            if isinstance(parsed, dict):
                                result = parsed
                                logger.warning("[GUI-Plus] Fixed incomplete JSON response")
                        except json.JSONDecodeError:
                            pass
            if result is None:
                return ToolResult(error=f"无法从模型响应中解析 JSON: {fixed_response}")

            # 修复 GUI-Plus 不规范的响应格式
            action_type = result.get("action", "").strip().upper()
            params = result.get("parameters", {})
            thought = result.get("thought", "")

            # 容错：FAIL/FINISH 的 message/reason 可能放在顶层而非 parameters 里
            # （模型实际会返回 {"action": "FAIL", "reason": "..."} 这种扁平结构）
            if action_type in ("FAIL", "FINISH", "FAILE") and isinstance(params, dict):
                if "reason" in result and "reason" not in params:
                    params["reason"] = result["reason"]
                if "message" in result and "message" not in params:
                    params["message"] = result["message"]

            # 如果没有 action 但有坐标，推断 action 类型
            if not action_type:
                if result.get("x") is not None or params.get("x") is not None:
                    if result.get("text") or params.get("text"):
                        action_type = "TYPE"
                        if not params:
                            params = result
                    else:
                        action_type = "CLICK"
                        if not params:
                            params = result
                    logger.warning(f"[GUI-Plus] Inferred action type: {action_type}")

            logger.info(f"[GUI-Plus] Decision: action={action_type}, thought={thought}")

            # 如果期望的是 type 操作，但模型只返回了 CLICK，需要先点击再输入
            if action_hint == "type" and action_type == "CLICK":
                # 从 vision_instruction 中提取引号内的文本（如 输入'上海' 中的 上海）
                text_match = re.search(r"(?:输入|填入|写入)['\"]([^'\"]+)['\"]", vision_instruction)
                if text_match:
                    text_to_type = text_match.group(1)
                    x = params.get("x")
                    y = params.get("y")
                    if x is not None and y is not None:
                        # 处理坐标格式
                        if isinstance(x, list) and len(x) >= 2:
                            x, y = x[0], x[1]
                        elif isinstance(x, list):
                            x = x[0]
                        if isinstance(y, list):
                            y = y[0]
                        try:
                            x = int(x)
                            y = int(y)
                        except (ValueError, TypeError):
                            pass
                        # 换算模型坐标到视口像素
                        x, y = await self._scale_gui_plus_coords(page, x, y)
                        logger.info(f"[GUI-Plus] Click+Type: ({x}, {y}) then type '{text_to_type}'")
                        await page.mouse.click(x, y)
                        await asyncio.sleep(0.3)
                        await page.keyboard.press("Control+a")  # 全选
                        await asyncio.sleep(0.1)
                        await page.keyboard.type(text_to_type)
                        await asyncio.sleep(0.3)
                        return ToolResult(
                            output=f"[vision] 成功在 ({x}, {y}) 点击并输入: {text_to_type}\n思考过程: {thought}"
                        )

            # 5. 执行操作
            if action_type == "CLICK":
                x = params.get("x")
                y = params.get("y")
                description = params.get("description", "")

                # 处理坐标格式错误的情况
                # 情况1: {"x": [590, 206]} - x 是一个包含两个值的列表
                if isinstance(x, list) and len(x) >= 2:
                    x, y = x[0], x[1]
                elif isinstance(x, list) and len(x) == 1:
                    x = x[0]

                # 情况2: y 也可能是列表
                if isinstance(y, list) and len(y) >= 1:
                    y = y[0]

                if x is None or y is None:
                    return ToolResult(error=f"CLICK 操作缺少坐标: {params}")

                # 确保坐标是数值
                try:
                    x = int(x)
                    y = int(y)
                except (ValueError, TypeError):
                    return ToolResult(error=f"CLICK 坐标格式错误: x={x}, y={y}")

                # 换算模型坐标到视口像素
                x, y = await self._scale_gui_plus_coords(page, x, y)

                logger.info(f"[GUI-Plus] CLICK at ({x}, {y}): {description}")

                # 调试：在截图上标记点击位置
                try:
                    from PIL import Image, ImageDraw
                    debug_screenshot = Image.open(screenshot_path)
                    draw = ImageDraw.Draw(debug_screenshot)
                    # 画一个红色十字标记点击位置
                    draw.ellipse([x-10, y-10, x+10, y+10], outline="red", width=3)
                    draw.line([x-15, y, x+15, y], fill="red", width=2)
                    draw.line([x, y-15, x, y+15], fill="red", width=2)
                    debug_path = screenshot_path.with_name(f"{screenshot_path.stem}_clicked.png")
                    debug_screenshot.save(debug_path)
                    logger.info(f"[GUI-Plus] Debug screenshot with click marker saved: {debug_path}")
                except Exception as debug_err:
                    logger.debug(f"[GUI-Plus] Failed to save debug screenshot: {debug_err}")

                await page.mouse.click(x, y)
                await asyncio.sleep(0.5)  # 等待点击生效

                return ToolResult(
                    output=f"[vision] 成功点击 ({x}, {y}): {description}\n思考过程: {thought}"
                )

            elif action_type == "TYPE":
                text_to_type = params.get("text", "")
                needs_enter = params.get("needs_enter", False)
                description = params.get("description", "输入框")

                if not text_to_type:
                    return ToolResult(error="TYPE 操作缺少文本")

                # 获取坐标（必须有坐标才能正确点击输入框）
                x = params.get("x")
                y = params.get("y")

                # 处理 x 或 y 是列表的情况
                if isinstance(x, list) and len(x) >= 2:
                    x, y = x[0], x[1]
                elif isinstance(x, list) and len(x) == 1:
                    x = x[0]
                if isinstance(y, list) and len(y) >= 1:
                    y = y[0]

                if x is not None and y is not None:
                    try:
                        x = int(x)
                        y = int(y)
                    except (ValueError, TypeError):
                        return ToolResult(error=f"TYPE 坐标格式错误: x={x}, y={y}")

                    # 换算模型坐标到视口像素
                    x, y = await self._scale_gui_plus_coords(page, x, y)

                    logger.info(f"[GUI-Plus] TYPE: click ({x}, {y}) then type '{text_to_type}'")

                    # 调试：在截图上标记输入位置
                    try:
                        from PIL import Image, ImageDraw
                        debug_screenshot = Image.open(screenshot_path)
                        draw = ImageDraw.Draw(debug_screenshot)
                        # 画一个绿色方框标记输入位置
                        draw.rectangle([x-15, y-10, x+15, y+10], outline="green", width=3)
                        draw.text((x+20, y-5), text_to_type, fill="green")
                        debug_path = screenshot_path.with_name(f"{screenshot_path.stem}_typed.png")
                        debug_screenshot.save(debug_path)
                        logger.info(f"[GUI-Plus] Debug screenshot with type marker saved: {debug_path}")
                    except Exception as debug_err:
                        logger.debug(f"[GUI-Plus] Failed to save debug screenshot: {debug_err}")

                    # 先点击输入框
                    await page.mouse.click(x, y)
                    await asyncio.sleep(0.3)
                    # 全选并删除现有内容
                    await page.keyboard.press("Control+a")
                    await asyncio.sleep(0.1)
                else:
                    logger.warning("[GUI-Plus] TYPE: no coordinates, typing at current focus")

                # 输入文本
                await page.keyboard.type(text_to_type)
                if needs_enter:
                    await page.keyboard.press("Enter")

                await asyncio.sleep(0.3)  # 等待输入生效

                return ToolResult(
                    output=f"[vision] 成功在 ({x}, {y}) {description} 输入: {text_to_type}\n思考过程: {thought}"
                )

            elif action_type == "SCROLL":
                direction = params.get("direction", "down")
                amount = params.get("amount", "medium")

                scroll_amounts = {"small": 100, "medium": 300, "large": 600}
                pixels = scroll_amounts.get(amount, 300)
                if direction == "up":
                    pixels = -pixels

                await page.mouse.wheel(0, pixels)
                return ToolResult(output=f"[vision] 成功滚动 {direction} {amount}")

            elif action_type == "KEY_PRESS":
                key = params.get("key", "")
                if key:
                    await page.keyboard.press(key)
                    return ToolResult(output=f"[vision] 成功按下按键: {key}")
                return ToolResult(error="KEY_PRESS 操作缺少按键")

            elif action_type == "FINISH":
                message = params.get("message", "任务完成")
                return ToolResult(output=f"[vision] 任务完成: {message}")

            elif action_type == "FAIL":
                reason = params.get("reason", "未知原因")
                return ToolResult(error=f"[vision] 任务失败: {reason}")

            else:
                return ToolResult(error=f"未知的操作类型: {action_type}")

        except Exception as e:
            logger.error(f"[GUI-Plus] Execution failed: {e}")
            import traceback
            traceback.print_exc()
            return ToolResult(error=f"[vision] 执行失败: {str(e)}")

    async def _perform_gui_action_on_page(self, page, instruction: str) -> str:
        """
        在当前页面上执行 GUI 操作。
        截图页面，调用视觉 GUI 模型获取操作指令，然后执行。

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
            # 调用视觉 GUI 模型
            response = await self._call_gui_plus_model(tmp_path, instruction)
            logger.info(f"视觉 GUI 模型响应: {response}")

            # 解析响应
            action = self._parse_gui_action(response)
            if not action:
                return f"无法解析视觉 GUI 模型响应: {response}"

            # 执行操作
            result = await self._execute_gui_action(action, page)
            return result
        finally:
            # 清理临时文件
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    # ========== 简化的 click 和 type 接口 ==========

    @staticmethod
    async def _scale_gui_plus_coords(page, x: int, y: int):
        """
        将 GUI-Plus 返回的坐标换算为视口像素坐标。

        GUI-Plus 模型内部将截图按长边 1260 等比缩放后识别，
        返回的坐标基于缩放后的图片空间，需要换算回实际视口像素。
        （视口长边小于 1260 时不缩放，返回原坐标）

        参数：
            page: Playwright 页面对象
            x: 模型返回的 x 坐标
            y: 模型返回的 y 坐标

        返回：
            (x, y) 换算后的视图像素坐标
        """
        try:
            vp_w = await page.evaluate("() => window.innerWidth")
            vp_h = await page.evaluate("() => window.innerHeight")
            long_side = max(vp_w, vp_h)
            scale = long_side / 1260 if long_side > 1260 else 1.0
            if scale != 1.0:
                logger.info(
                    f"[GUI-Plus] 坐标换算: 视口 {vp_w}x{vp_h}, 缩放系数 {scale:.3f}, "
                    f"({x}, {y}) -> ({int(x * scale)}, {int(y * scale)})"
                )
                return int(x * scale), int(y * scale)
        except Exception as e:
            logger.debug(f"[GUI-Plus] 坐标换算失败，使用原始坐标: {e}")
        return x, y

    async def _click(self, context: BrowserContext, element_description: str) -> ToolResult:
        """
        简化的点击操作，自动选择最佳策略：
        1. 尝试通过 Playwright locator 查找包含指定文字的元素
        2. 如果失败，使用 GUI-Plus 视觉模型

        参数：
            context: 浏览器上下文
            element_description: 要点击的元素描述（如"搜索按钮"、"1月30日"）

        返回：
            ToolResult
        """
        try:
            page = await context.get_current_page()
            await page.bring_to_front()
            await page.wait_for_load_state()

            logger.info(f"[click] 尝试点击: '{element_description}'")

            # 策略1: 使用 Playwright locator 精确查找（优先文字精确匹配）
            try:
                # 尝试精确文字匹配
                locator = page.get_by_text(element_description, exact=True)
                if await locator.count() > 0:
                    # 找到精确匹配，点击第一个可见的
                    for i in range(await locator.count()):
                        el = locator.nth(i)
                        if await el.is_visible():
                            box = await el.bounding_box()
                            if box and box["y"] < 600:  # 只点击上半部分页面的元素
                                click_x = box["x"] + box["width"] / 2
                                click_y = box["y"] + box["height"] / 2
                                logger.info(f"[click] 精确匹配: '{element_description}' at ({click_x:.0f}, {click_y:.0f})")
                                await page.mouse.click(click_x, click_y)
                                await asyncio.sleep(0.5)
                                return ToolResult(
                                    output=f"[click] 成功点击: '{element_description}' at ({click_x:.0f}, {click_y:.0f})"
                                )

                # 尝试包含文字匹配（但要求元素文字长度不能太长）
                locator = page.get_by_text(element_description, exact=False)
                if await locator.count() > 0:
                    for i in range(min(await locator.count(), 10)):  # 最多检查10个
                        el = locator.nth(i)
                        if await el.is_visible():
                            text_content = await el.text_content()
                            # 只接受文字长度不超过描述3倍的元素
                            if text_content and len(text_content.strip()) <= len(element_description) * 3:
                                box = await el.bounding_box()
                                if box and box["y"] < 600:
                                    click_x = box["x"] + box["width"] / 2
                                    click_y = box["y"] + box["height"] / 2
                                    logger.info(f"[click] 包含匹配: '{text_content[:30]}' at ({click_x:.0f}, {click_y:.0f})")
                                    await page.mouse.click(click_x, click_y)
                                    await asyncio.sleep(0.5)
                                    return ToolResult(
                                        output=f"[click] 成功点击: '{text_content[:30]}' at ({click_x:.0f}, {click_y:.0f})"
                                    )
            except Exception as e:
                logger.debug(f"[click] Playwright locator 失败: {e}")

            # 策略2: 回退到视觉模型
            logger.info("[click] Playwright 未找到匹配元素，使用视觉模型")
            return await self._execute_vision_action(context, f"点击{element_description}", "click")

        except Exception as e:
            logger.error(f"[click] 点击失败: {e}")
            # 最终回退到视觉模型
            return await self._execute_vision_action(context, f"点击{element_description}", "click")

    async def _type(self, context: BrowserContext, element_description: str, text: str) -> ToolResult:
        """
        简化的输入操作，自动选择最佳策略：
        1. 先尝试通过 JavaScript 找到输入框，并用 LLM 选择匹配的
        2. 如果失败，使用 GUI-Plus 视觉模型识别并输入

        参数：
            context: 浏览器上下文
            element_description: 输入框描述（如"出发城市"、"搜索框"）
            text: 要输入的文本

        返回：
            ToolResult
        """
        try:
            page = await context.get_current_page()
            await page.bring_to_front()
            await page.wait_for_load_state()

            logger.info(f"[type] 尝试在 '{element_description}' 输入: '{text}'")

            # 策略1: 尝试通过 JavaScript 找到输入框
            inputs = await page.evaluate(
                """
                () => {
                    const inputs = document.querySelectorAll('input[type="text"], input[type="search"], input:not([type]), textarea, [contenteditable="true"], [role="textbox"], [role="combobox"]');
                    const results = [];
                    for (const el of inputs) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width <= 0 || rect.height <= 0) continue;
                        if (rect.y < 0 || rect.y > window.innerHeight) continue;

                        const placeholder = el.getAttribute('placeholder') || '';
                        const ariaLabel = el.getAttribute('aria-label') || '';
                        const name = el.getAttribute('name') || '';
                        const value = el.value || '';

                        // 查找关联的 label
                        let labelText = '';
                        if (el.id) {
                            const label = document.querySelector(`label[for="${el.id}"]`);
                            if (label) labelText = label.textContent?.trim() || '';
                        }
                        // 检查父元素中的文字
                        const parent = el.closest('div, label, li');
                        const parentText = parent ? parent.textContent?.trim().substring(0, 30) : '';

                        results.push({
                            placeholder: placeholder,
                            ariaLabel: ariaLabel,
                            name: name,
                            value: value,
                            labelText: labelText,
                            parentText: parentText,
                            rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height }
                        });
                    }
                    return results;
                }
            """
            )

            # 构建输入框描述列表供 LLM 分析
            if inputs and len(inputs) > 0:
                inputs_text = "\n".join(
                    [
                        f"[{i}] placeholder='{inp['placeholder']}' label='{inp['labelText']}' aria='{inp['ariaLabel']}' value='{inp['value'][:20]}' parent='{inp['parentText'][:20]}'"
                        for i, inp in enumerate(inputs)
                    ]
                )

                prompt = f"""找到最匹配的输入框。

用户想在这里输入: {element_description}

输入框列表:
{inputs_text}

返回最匹配的输入框索引（只返回数字，如: 0）。如果没有匹配项，返回 -1。"""

                response = await self.llm.ask(
                    messages=[{"role": "user", "content": prompt}],
                    system_msgs=[
                        {
                            "role": "system",
                            "content": "你是一个精确的页面元素匹配器。只返回元素索引数字。",
                        }
                    ],
                )

                # 解析索引
                try:
                    match = re.search(r"-?\d+", response)
                    if match:
                        idx = int(match.group())
                        if 0 <= idx < len(inputs):
                            target = inputs[idx]
                            click_x = target["rect"]["x"] + target["rect"]["width"] / 2
                            click_y = target["rect"]["y"] + target["rect"]["height"] / 2

                            logger.info(f"[type] 找到输入框: [{idx}] placeholder='{target['placeholder']}' at ({click_x:.0f}, {click_y:.0f})")

                            # 点击激活输入框
                            await page.mouse.click(click_x, click_y)
                            await asyncio.sleep(0.3)

                            # 全选并输入
                            await page.keyboard.press("Control+a")
                            await asyncio.sleep(0.1)
                            await page.keyboard.type(text)
                            await asyncio.sleep(0.3)

                            return ToolResult(
                                output=f"[type] 成功在 '{target['placeholder'] or target['labelText'] or element_description}' 中输入: {text}"
                            )
                except (ValueError, IndexError):
                    pass

            # 策略2: 回退到视觉模型
            logger.info("[type] JavaScript 未找到匹配输入框，使用视觉模型")
            return await self._execute_vision_action(context, f"在{element_description}输入'{text}'", "type")

        except Exception as e:
            logger.error(f"[type] 输入失败: {e}")
            # 最终回退到视觉模型
            return await self._execute_vision_action(context, f"在{element_description}输入'{text}'", "type")

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
        element_description: Optional[str] = None,
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
            instruction: gui_action 操作的视觉指令
            element_description: click/type 操作的元素描述
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
                    # 选择器映射可能过期（页面已变化），KeyError 需捕获并提示刷新状态
                    try:
                        element = await context.get_dom_element_by_index(index)
                    except KeyError:
                        return ToolResult(
                            error=(
                                f"索引 {index} 在当前页面状态中不存在（页面可能已发生变化）。"
                                f"请先执行 action='get_current_state'（或 navigate）获取最新的元素索引后再重试。"
                            )
                        )
                    if not element:
                        return ToolResult(error=f"未找到索引为 {index} 的元素")
                    # 输入前检查元素类型：非输入型元素（如链接/按钮）不可输入，
                    # 且库方法会先 click 该元素导致页面跳转，必须提前拦截
                    tag_name = getattr(element, "tag_name", "").lower()
                    attrs = getattr(element, "attributes", {}) or {}
                    is_editable = tag_name in ("input", "textarea") or (
                        "contenteditable" in attrs
                    )
                    if not is_editable:
                        return ToolResult(
                            error=(
                                f"索引 {index} 是 <{tag_name}> 元素，不可输入文本。"
                                f"input_text 只适用于 <input>、<textarea> 或 contenteditable 元素。"
                                f"请先执行 action='get_current_state' 查看可交互元素列表，"
                                f"选择正确的输入框索引后重试；如目标是搜索，可改用 go_to_url 直接访问搜索 URL。"
                            )
                        )
                    try:
                        await context._input_text_element_node(element, text)
                    except Exception:
                        # 库方法失败（元素被遮挡、句柄失效或站点反自动化检测拦截 click）
                        # 改用 JavaScript 直接设置元素值并触发事件，绕过交互检测
                        try:
                            element_handle = await context.get_locate_element(element)
                            if element_handle is None:
                                raise RuntimeError("元素句柄获取失败")
                            await element_handle.evaluate(
                                """(el, text) => {
                                    el.focus();
                                    if (el.isContentEditable) {
                                        el.textContent = text;
                                    } else {
                                        el.value = text;
                                    }
                                    el.dispatchEvent(new Event('input', {bubbles: true}));
                                    el.dispatchEvent(new Event('change', {bubbles: true}));
                                }""",
                                text,
                            )
                        except Exception as js_error:
                            return ToolResult(
                                error=(
                                    f"在索引为 {index} 的元素中输入文本失败：{str(js_error)}。"
                                    f"建议：1) 重新执行 get_current_state 获取最新页面状态后重试；"
                                    f"2) 改用 web_search 操作搜索；"
                                    f"3) 或使用 go_to_url 直接访问带查询参数的 URL"
                                )
                            )
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

                # 简化的 click 操作：智能路由（Playwright -> GUI-Plus 视觉模型）
                elif action == "click":
                    if not element_description:
                        return ToolResult(
                            error="'click' 操作需要提供 element_description（元素描述）"
                        )
                    return await self._click(context, element_description)

                # 简化的 type 操作：智能路由（JavaScript+LLM -> GUI-Plus 视觉模型）
                elif action == "type":
                    if not element_description:
                        return ToolResult(
                            error="'type' 操作需要提供 element_description（输入框描述）"
                        )
                    if not text:
                        return ToolResult(error="'type' 操作需要提供 text（要输入的文本）")
                    return await self._type(context, element_description, text)

                # GUI-Plus 视觉交互操作
                elif action == "gui_action":
                    if not instruction:
                        return ToolResult(error="'gui_action' 操作需要提供指令")
                    return await self._execute_vision_action(context, instruction, "click")

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
