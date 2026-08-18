import base64
import io
import json
import traceback
from typing import Optional  # 添加 Optional 的导入

from PIL import Image
from pydantic import Field

from app.daytona.tool_base import SandboxToolsBase  # 确保正确导入 Sandbox
from app.daytona.tool_base import Sandbox, ThreadMessage
from app.tool.base import ToolResult
from app.utils.logger import logger

# Context = TypeVar("Context")
_BROWSER_DESCRIPTION = """\
基于沙箱的浏览器自动化工具，允许通过各种操作与网页交互。
* 此工具提供在沙箱环境中控制浏览器会话的命令
* 它跨调用维护状态，保持浏览器会话活跃直到显式关闭
* 当你需要在安全沙箱中浏览网站、填写表单、点击按钮或提取内容时使用此工具
* 每个操作需要工具依赖中定义的特定参数
主要功能包括：
* 导航：转到指定 URL，在历史记录中后退
* 交互：按索引点击元素、输入文本、发送键盘命令
* 滚动：按像素量上下滚动或滚动到特定文本
* 标签页管理：切换标签页或关闭标签页
* 内容提取：获取下拉选项或选择下拉选项
"""


# noinspection PyArgumentList
class SandboxBrowserTool(SandboxToolsBase):
    """在 Daytona 沙箱中执行任务的工具，具有浏览器使用能力。"""

    name: str = "sandbox_browser"
    description: str = _BROWSER_DESCRIPTION
    parameters: dict = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "navigate_to",
                    "go_back",
                    "wait",
                    "click_element",
                    "input_text",
                    "send_keys",
                    "switch_tab",
                    "close_tab",
                    "scroll_down",
                    "scroll_up",
                    "scroll_to_text",
                    "get_dropdown_options",
                    "select_dropdown_option",
                    "click_coordinates",
                    "drag_drop",
                ],
                "description": "要执行的浏览器操作",
            },
            "url": {"type": "string", "description": "'navigate_to' 操作的 URL"},
            "index": {"type": "integer", "description": "交互操作的元素索引"},
            "text": {"type": "string", "description": "输入或滚动操作的文本"},
            "amount": {"type": "integer", "description": "滚动的像素量"},
            "page_id": {"type": "integer", "description": "标签页管理操作的标签页 ID"},
            "keys": {"type": "string", "description": "键盘操作要发送的按键"},
            "seconds": {"type": "integer", "description": "等待的秒数"},
            "x": {"type": "integer", "description": "点击或拖拽操作的 X 坐标"},
            "y": {"type": "integer", "description": "点击或拖拽操作的 Y 坐标"},
            "element_source": {"type": "string", "description": "拖拽操作的源元素"},
            "element_target": {"type": "string", "description": "拖拽操作的目标元素"},
        },
        "required": ["action"],
        "dependencies": {
            "navigate_to": ["url"],
            "click_element": ["index"],
            "input_text": ["index", "text"],
            "send_keys": ["keys"],
            "switch_tab": ["page_id"],
            "close_tab": ["page_id"],
            "scroll_down": ["amount"],
            "scroll_up": ["amount"],
            "scroll_to_text": ["text"],
            "get_dropdown_options": ["index"],
            "select_dropdown_option": ["index", "text"],
            "click_coordinates": ["x", "y"],
            "drag_drop": ["element_source", "element_target"],
            "wait": ["seconds"],
        },
    }
    browser_message: Optional[ThreadMessage] = Field(default=None, exclude=True)

    def __init__(
        self, sandbox: Optional[Sandbox] = None, thread_id: Optional[str] = None, **data
    ):
        """使用可选的沙箱和 thread_id 初始化。"""
        super().__init__(**data)
        if sandbox is not None:
            self._sandbox = sandbox  # 直接设置基类的私有属性

    def _validate_base64_image(
        self, base64_string: str, max_size_mb: int = 10
    ) -> tuple[bool, str]:
        """
        验证 base64 图片数据。
        参数：
            base64_string: base64 编码的图片数据
            max_size_mb: 允许的最大图片大小（兆字节）
        返回：
            (是否有效, 错误消息) 元组
        """
        try:
            if not base64_string or len(base64_string) < 10:
                return False, "Base64 字符串为空或太短"
            if base64_string.startswith("data:"):
                try:
                    base64_string = base64_string.split(",", 1)[1]
                except (IndexError, ValueError):
                    return False, "无效的 data URL 格式"
            import re

            if not re.match(r"^[A-Za-z0-9+/]*={0,2}$", base64_string):
                return False, "检测到无效的 base64 字符"
            if len(base64_string) % 4 != 0:
                return False, "无效的 base64 字符串长度"
            try:
                image_data = base64.b64decode(base64_string, validate=True)
            except Exception as e:
                return False, f"Base64 解码失败：{str(e)}"
            max_size_bytes = max_size_mb * 1024 * 1024
            if len(image_data) > max_size_bytes:
                return False, f"图片大小超过限制（{max_size_bytes} 字节）"
            try:
                image_stream = io.BytesIO(image_data)
                with Image.open(image_stream) as img:
                    img.verify()
                    supported_formats = {"JPEG", "PNG", "GIF", "BMP", "WEBP", "TIFF"}
                    if img.format not in supported_formats:
                        return False, f"不支持的图片格式：{img.format}"
                    image_stream.seek(0)
                    with Image.open(image_stream) as img_check:
                        width, height = img_check.size
                        max_dimension = 8192
                        if width > max_dimension or height > max_dimension:
                            return (
                                False,
                                f"图片尺寸超过限制（{max_dimension}x{max_dimension}）",
                            )
                        if width < 1 or height < 1:
                            return False, f"无效的图片尺寸：{width}x{height}"
            except Exception as e:
                return False, f"无效的图片数据：{str(e)}"
            return True, "有效图片"
        except Exception as e:
            logger.error(f"base64 图片验证时出现意外错误：{e}")
            return False, f"验证错误：{str(e)}"

    async def _execute_browser_action(
        self, endpoint: str, params: dict = None, method: str = "POST"
    ) -> ToolResult:
        """通过沙箱 API 执行浏览器自动化操作。"""
        try:
            await self._ensure_sandbox()
            url = f"http://localhost:8003/api/automation/{endpoint}"
            if method == "GET" and params:
                query_params = "&".join([f"{k}={v}" for k, v in params.items()])
                url = f"{url}?{query_params}"
                curl_cmd = (
                    f"curl -s -X {method} '{url}' -H 'Content-Type: application/json'"
                )
            else:
                curl_cmd = (
                    f"curl -s -X {method} '{url}' -H 'Content-Type: application/json'"
                )
                if params:
                    json_data = json.dumps(params)
                    curl_cmd += f" -d '{json_data}'"
            logger.debug(f"正在执行 curl 命令：{curl_cmd}")
            response = self.sandbox.process.exec(curl_cmd, timeout=30)
            if response.exit_code == 0:
                try:
                    result = json.loads(response.result)
                    result.setdefault("content", "")
                    result.setdefault("role", "assistant")
                    if "screenshot_base64" in result:
                        screenshot_data = result["screenshot_base64"]
                        is_valid, validation_message = self._validate_base64_image(
                            screenshot_data
                        )
                        if not is_valid:
                            logger.warning(f"截图验证失败：{validation_message}")
                            result["image_validation_error"] = validation_message
                            del result["screenshot_base64"]

                    # added_message = await self.thread_manager.add_message(
                    #     thread_id=self.thread_id,
                    #     type="browser_state",
                    #     content=result,
                    #     is_llm_message=False
                    # )
                    message = ThreadMessage(
                        type="browser_state", content=result, is_llm_message=False
                    )
                    self.browser_message = message
                    success_response = {
                        "success": result.get("success", False),
                        "message": result.get("message", "浏览器操作已完成"),
                    }
                    #         if added_message and 'message_id' in added_message:
                    #             success_response['message_id'] = added_message['message_id']
                    for field in [
                        "url",
                        "title",
                        "element_count",
                        "pixels_below",
                        "ocr_text",
                        "image_url",
                    ]:
                        if field in result:
                            success_response[field] = result[field]
                    return (
                        self.success_response(success_response)
                        if success_response["success"]
                        else self.fail_response(success_response)
                    )
                except json.JSONDecodeError as e:
                    logger.error(f"解析响应 JSON 失败：{e}")
                    return self.fail_response(f"解析响应 JSON 失败：{e}")
            else:
                logger.error(f"浏览器自动化请求失败：{response}")
                return self.fail_response(f"浏览器自动化请求失败：{response}")
        except Exception as e:
            logger.error(f"执行浏览器操作时出错：{e}")
            logger.debug(traceback.format_exc())
            return self.fail_response(f"执行浏览器操作时出错：{e}")

    async def execute(
        self,
        action: str,
        url: Optional[str] = None,
        index: Optional[int] = None,
        text: Optional[str] = None,
        amount: Optional[int] = None,
        page_id: Optional[int] = None,
        keys: Optional[str] = None,
        seconds: Optional[int] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
        element_source: Optional[str] = None,
        element_target: Optional[str] = None,
        **kwargs,
    ) -> ToolResult:
        """
        在沙箱环境中执行浏览器操作。
        参数：
            action: 要执行的浏览器操作
            url: 导航的 URL
            index: 交互的元素索引
            text: 输入或滚动操作的文本
            amount: 滚动的像素量
            page_id: 标签页管理的标签页 ID
            keys: 键盘操作要发送的按键
            seconds: 等待的秒数
            x: 点击/拖拽的 X 坐标
            y: 点击/拖拽的 Y 坐标
            element_source: 拖拽操作的源元素
            element_target: 拖拽操作的目标元素
        返回：
            包含操作输出或错误的 ToolResult
        """
        # async with self.lock:
        try:
            # 导航操作
            if action == "navigate_to":
                if not url:
                    return self.fail_response("导航操作需要 URL")
                return await self._execute_browser_action("navigate_to", {"url": url})
            elif action == "go_back":
                return await self._execute_browser_action("go_back", {})
                # 交互操作
            elif action == "click_element":
                if index is None:
                    return self.fail_response("click_element 操作需要 Index")
                return await self._execute_browser_action(
                    "click_element", {"index": index}
                )
            elif action == "input_text":
                if index is None or not text:
                    return self.fail_response("input_text 操作需要 Index 和 text")
                return await self._execute_browser_action(
                    "input_text", {"index": index, "text": text}
                )
            elif action == "send_keys":
                if not keys:
                    return self.fail_response("send_keys 操作需要 Keys")
                return await self._execute_browser_action("send_keys", {"keys": keys})
                # 标签页管理
            elif action == "switch_tab":
                if page_id is None:
                    return self.fail_response("switch_tab 操作需要 Page ID")
                return await self._execute_browser_action(
                    "switch_tab", {"page_id": page_id}
                )
            elif action == "close_tab":
                if page_id is None:
                    return self.fail_response("close_tab 操作需要 Page ID")
                return await self._execute_browser_action(
                    "close_tab", {"page_id": page_id}
                )
                # 滚动操作
            elif action == "scroll_down":
                params = {"amount": amount} if amount is not None else {}
                return await self._execute_browser_action("scroll_down", params)
            elif action == "scroll_up":
                params = {"amount": amount} if amount is not None else {}
                return await self._execute_browser_action("scroll_up", params)
            elif action == "scroll_to_text":
                if not text:
                    return self.fail_response("scroll_to_text 操作需要 Text")
                return await self._execute_browser_action(
                    "scroll_to_text", {"text": text}
                )
            # 下拉菜单操作
            elif action == "get_dropdown_options":
                if index is None:
                    return self.fail_response("get_dropdown_options 操作需要 Index")
                return await self._execute_browser_action(
                    "get_dropdown_options", {"index": index}
                )
            elif action == "select_dropdown_option":
                if index is None or not text:
                    return self.fail_response(
                        "select_dropdown_option 操作需要 Index 和 text"
                    )
                return await self._execute_browser_action(
                    "select_dropdown_option", {"index": index, "text": text}
                )
                # 坐标相关操作
            elif action == "click_coordinates":
                if x is None or y is None:
                    return self.fail_response("click_coordinates 操作需要 X 和 Y 坐标")
                return await self._execute_browser_action(
                    "click_coordinates", {"x": x, "y": y}
                )
            elif action == "drag_drop":
                if not element_source or not element_target:
                    return self.fail_response("drag_drop 操作需要源元素和目标元素")
                return await self._execute_browser_action(
                    "drag_drop",
                    {
                        "element_source": element_source,
                        "element_target": element_target,
                    },
                )
            # 实用工具操作
            elif action == "wait":
                seconds_to_wait = seconds if seconds is not None else 3
                return await self._execute_browser_action(
                    "wait", {"seconds": seconds_to_wait}
                )
            else:
                return self.fail_response(f"未知操作：{action}")
        except Exception as e:
            logger.error(f"执行浏览器操作时出错：{e}")
            return self.fail_response(f"执行浏览器操作时出错：{e}")

    async def get_current_state(
        self, message: Optional[ThreadMessage] = None
    ) -> ToolResult:
        """
        获取当前浏览器状态作为 ToolResult。
        如果未提供 context，则使用 self.context。
        """
        try:
            # 使用提供的上下文或回退到 self.context
            message = message or self.browser_message
            if not message:
                return ToolResult(error="浏览器上下文未初始化")
            state = message.content
            screenshot = state.get("screenshot_base64")
            # 构建包含所有必需字段的状态信息
            state_info = {
                "url": state.get("url", ""),
                "title": state.get("title", ""),
                "tabs": [tab.model_dump() for tab in state.get("tabs", [])],
                "pixels_above": getattr(state, "pixels_above", 0),
                "pixels_below": getattr(state, "pixels_below", 0),
                "help": "[0]、[1]、[2] 等表示可点击的索引，对应列出的元素。点击这些索引将导航到或交互其背后的相应内容。",
            }

            return ToolResult(
                output=json.dumps(state_info, indent=4, ensure_ascii=False),
                base64_image=screenshot,
            )
        except Exception as e:
            return ToolResult(error=f"获取浏览器状态失败：{str(e)}")

    @classmethod
    def create_with_sandbox(cls, sandbox: Sandbox) -> "SandboxBrowserTool":
        """使用沙箱创建工具的工厂方法。"""
        return cls(sandbox=sandbox)
