from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar, Dict, Optional

from daytona import Daytona, DaytonaConfig, Sandbox, SandboxState
from pydantic import Field

from app.config import config
from app.daytona.sandbox import create_sandbox, start_supervisord_session
from app.tool.base import BaseTool
from app.utils.files_utils import clean_path
from app.utils.logger import logger

# load_dotenv()
daytona_settings = config.daytona
daytona_config = DaytonaConfig(
    api_key=daytona_settings.daytona_api_key,
    server_url=daytona_settings.daytona_server_url,
    target=daytona_settings.daytona_target,
)
daytona = Daytona(daytona_config)


@dataclass
class ThreadMessage:
    """
    表示要添加到线程的消息。
    """

    type: str
    content: Dict[str, Any]
    is_llm_message: bool = False
    metadata: Optional[Dict[str, Any]] = None
    timestamp: Optional[float] = field(
        default_factory=lambda: datetime.now().timestamp()
    )

    def to_dict(self) -> Dict[str, Any]:
        """将消息转换为字典用于 API 调用"""
        return {
            "type": self.type,
            "content": self.content,
            "is_llm_message": self.is_llm_message,
            "metadata": self.metadata or {},
            "timestamp": self.timestamp,
        }


class SandboxToolsBase(BaseTool):
    """所有沙箱工具的基类，提供基于项目的沙箱访问。"""

    # 类变量用于跟踪沙箱 URL 是否已打印
    _urls_printed: ClassVar[bool] = False

    # 必需字段
    project_id: Optional[str] = None
    # thread_manager: Optional[ThreadManager] = None

    # 私有字段（不属于模型架构的一部分）
    _sandbox: Optional[Sandbox] = None
    _sandbox_id: Optional[str] = None
    _sandbox_pass: Optional[str] = None
    workspace_path: str = Field(default="/workspace", exclude=True)
    _sessions: dict[str, str] = {}

    class Config:
        arbitrary_types_allowed = True  # 允许非 pydantic 类型，如 ThreadManager
        underscore_attrs_are_private = True

    async def _ensure_sandbox(self) -> Sandbox:
        """确保我们有一个有效的沙箱实例，如果需要则从项目中获取。"""
        if self._sandbox is None:
            # 获取或启动沙箱
            try:
                self._sandbox = create_sandbox(password=config.daytona.VNC_password)
                # 如果尚未打印则记录 URL
                if not SandboxToolsBase._urls_printed:
                    vnc_link = self._sandbox.get_preview_link(6080)
                    website_link = self._sandbox.get_preview_link(8080)

                    vnc_url = (
                        vnc_link.url if hasattr(vnc_link, "url") else str(vnc_link)
                    )
                    website_url = (
                        website_link.url
                        if hasattr(website_link, "url")
                        else str(website_link)
                    )

                    print("\033[95m***")
                    print(f"VNC 地址：{vnc_url}")
                    print(f"网站地址：{website_url}")
                    print("***\033[0m")
                    SandboxToolsBase._urls_printed = True
            except Exception as e:
                logger.error(f"获取或启动沙箱时出错：{str(e)}")
                raise e
        else:
            if (
                self._sandbox.state == SandboxState.ARCHIVED
                or self._sandbox.state == SandboxState.STOPPED
            ):
                logger.info(f"沙箱处于 {self._sandbox.state} 状态。正在启动...")
                try:
                    daytona.start(self._sandbox)
                    # 等待沙箱初始化
                    # sleep(5)
                    # 启动后刷新沙箱状态

                    # 重启时在会话中启动 supervisord
                    start_supervisord_session(self._sandbox)
                except Exception as e:
                    logger.error(f"启动沙箱时出错：{e}")
                    raise e
        return self._sandbox

    @property
    def sandbox(self) -> Sandbox:
        """获取沙箱实例，确保其存在。"""
        if self._sandbox is None:
            raise RuntimeError("沙箱未初始化。请先调用 _ensure_sandbox()。")
        return self._sandbox

    @property
    def sandbox_id(self) -> str:
        """获取沙箱 ID，确保其存在。"""
        if self._sandbox_id is None:
            raise RuntimeError("沙箱 ID 未初始化。请先调用 _ensure_sandbox()。")
        return self._sandbox_id

    def clean_path(self, path: str) -> str:
        """清理并规范化路径，使其相对于 /workspace。"""
        cleaned_path = clean_path(path, self.workspace_path)
        logger.debug(f"已清理路径：{path} -> {cleaned_path}")
        return cleaned_path
