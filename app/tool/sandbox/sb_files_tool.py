import asyncio
from typing import Optional, TypeVar

from pydantic import Field

from app.daytona.tool_base import Sandbox, SandboxToolsBase
from app.tool.base import ToolResult
from app.utils.files_utils import clean_path, should_exclude_file
from app.utils.logger import logger


Context = TypeVar("Context")

_FILES_DESCRIPTION = """\
基于沙箱的文件系统工具，允许在安全的沙箱环境中进行文件操作。
* 此工具提供在工作区中创建、读取、更新和删除文件的命令
* 所有操作都相对于 /workspace 目录执行以确保安全
* 当你需要在沙箱中管理文件、编辑代码或操作文件内容时使用此工具
* 每个操作需要工具依赖中定义的特定参数
主要功能包括：
* 文件创建：使用指定内容和权限创建新文件
* 文件修改：替换特定字符串或完全重写文件
* 文件删除：从工作区中删除文件
* 文件读取：读取文件内容，支持指定行范围
"""


class SandboxFilesTool(SandboxToolsBase):
    name: str = "sandbox_files"
    description: str = _FILES_DESCRIPTION
    parameters: dict = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "create_file",
                    "str_replace",
                    "full_file_rewrite",
                    "delete_file",
                ],
                "description": "要执行的文件操作"
            },
            "file_path": {
                "type": "string",
                "description": "文件路径，相对于 /workspace（例如：'src/main.py'）"
            },
            "file_contents": {
                "type": "string",
                "description": "要写入文件的内容"
            },
            "old_str": {
                "type": "string",
                "description": "要被替换的文本（必须仅出现一次）"
            },
            "new_str": {
                "type": "string",
                "description": "替换后的文本"
            },
            "permissions": {
                "type": "string",
                "description": "文件权限，八进制格式（例如：'644'）"
                "default": "644",
            },
        },
        "required": ["action"],
        "dependencies": {
            "create_file": ["file_path", "file_contents"],
            "str_replace": ["file_path", "old_str", "new_str"],
            "full_file_rewrite": ["file_path", "file_contents"],
            "delete_file": ["file_path"],
        },
    }
    SNIPPET_LINES: int = Field(default=4, exclude=True)
    # workspace_path: str = Field(default="/workspace", exclude=True)
    # sandbox: Optional[Sandbox] = Field(default=None, exclude=True)

    def __init__(
        self, sandbox: Optional[Sandbox] = None, thread_id: Optional[str] = None, **data
    ):
        """使用可选的沙箱和 thread_id 初始化。"""
        super().__init__(**data)
        if sandbox is not None:
            self._sandbox = sandbox

    def clean_path(self, path: str) -> str:
        """清理并规范化路径，使其相对于 /workspace"""
        return clean_path(path, self.workspace_path)

    def _should_exclude_file(self, rel_path: str) -> bool:
        """检查文件是否应基于路径、名称或扩展名被排除"""
        return should_exclude_file(rel_path)

    def _file_exists(self, path: str) -> bool:
        """检查文件是否在沙箱中存在"""
        try:
            self.sandbox.fs.get_file_info(path)
            return True
        except Exception:
            return False

    async def get_workspace_state(self) -> dict:
        """通过读取所有文件获取当前工作区状态"""
        files_state = {}
        try:
            # Ensure sandbox is initialized
            await self._ensure_sandbox()

            files = self.sandbox.fs.list_files(self.workspace_path)
            for file_info in files:
                rel_path = file_info.name

                # Skip excluded files and directories
                if self._should_exclude_file(rel_path) or file_info.is_dir:
                    continue

                try:
                    full_path = f"{self.workspace_path}/{rel_path}"
                    content = self.sandbox.fs.download_file(full_path).decode()
                    files_state[rel_path] = {
                        "content": content,
                        "is_dir": file_info.is_dir,
                        "size": file_info.size,
                        "modified": file_info.mod_time,
                    }
                except Exception as e:
                    print(f"读取文件 {rel_path} 时出错：{e}")
                except UnicodeDecodeError:
                    print(f"跳过二进制文件：{rel_path}")

            return files_state

        except Exception as e:
            print(f"获取工作区状态时出错：{str(e)}")
            return {}

    async def execute(
        self,
        action: str,
        file_path: Optional[str] = None,
        file_contents: Optional[str] = None,
        old_str: Optional[str] = None,
        new_str: Optional[str] = None,
        permissions: Optional[str] = "644",
        **kwargs,
    ) -> ToolResult:
        """
        在沙箱环境中执行文件操作。
        参数：
            action: 要执行的文件操作
            file_path: 文件路径，相对于 /workspace
            file_contents: 要写入文件的内容
            old_str: 要被替换的文本（用于 str_replace）
            new_str: 替换后的文本（用于 str_replace）
            permissions: 文件权限，八进制格式
        返回：
            包含操作输出或错误的 ToolResult
        """
        async with asyncio.Lock():
            try:
                # File creation
                if action == "create_file":
                    if not file_path or not file_contents:
                        return self.fail_response(
                            "create_file 操作需要 file_path 和 file_contents"
                        )
                    return await self._create_file(
                        file_path, file_contents, permissions
                    )

                # String replacement
                elif action == "str_replace":
                    if not file_path or not old_str or not new_str:
                        return self.fail_response(
                            "str_replace 操作需要 file_path、old_str 和 new_str"
                        )
                    return await self._str_replace(file_path, old_str, new_str)

                # Full file rewrite
                elif action == "full_file_rewrite":
                    if not file_path or not file_contents:
                        return self.fail_response(
                            "full_file_rewrite 操作需要 file_path 和 file_contents"
                        )
                    return await self._full_file_rewrite(
                        file_path, file_contents, permissions
                    )

                # File deletion
                elif action == "delete_file":
                    if not file_path:
                        return self.fail_response(
                            "delete_file 操作需要 file_path"
                        )
                    return await self._delete_file(file_path)

                else:
                    return self.fail_response(f"未知操作：{action}")

            except Exception as e:
                logger.error(f"执行文件操作时出错：{e}")
                return self.fail_response(f"执行文件操作时出错：{e}")

    async def _create_file(
        self, file_path: str, file_contents: str, permissions: str = "644"
    ) -> ToolResult:
        """使用提供的内容创建新文件"""
        try:
            # Ensure sandbox is initialized
            await self._ensure_sandbox()

            file_path = self.clean_path(file_path)
            full_path = f"{self.workspace_path}/{file_path}"
            if self._file_exists(full_path):
                return self.fail_response(
                    f"文件 '{file_path}' 已存在。使用 full_file_rewrite 来修改现有文件。"
                )

            # Create parent directories if needed
            parent_dir = "/".join(full_path.split("/")[:-1])
            if parent_dir:
                self.sandbox.fs.create_folder(parent_dir, "755")

            # Write the file content
            self.sandbox.fs.upload_file(file_contents.encode(), full_path)
            self.sandbox.fs.set_file_permissions(full_path, permissions)

            message = f"文件 '{file_path}' 创建成功。"

            # Check if index.html was created and add 8080 server info (only in root workspace)
            if file_path.lower() == "index.html":
                try:
                    website_link = self.sandbox.get_preview_link(8080)
                    website_url = (
                        website_link.url
                        if hasattr(website_link, "url")
                        else str(website_link).split("url='")[1].split("'")[0]
                    )
                    message += f"\n\n[自动检测到 index.html - HTTP 服务器地址：{website_url}]"
                    message += "\n[注意：请使用上面提供的 HTTP 服务器地址，不要启动新的服务器]"
                except Exception as e:
                    logger.warning(
                        f"获取 index.html 的网站 URL 失败：{str(e)}"
                    )

            return self.success_response(message)
        except Exception as e:
            return self.fail_response(f"创建文件时出错：{str(e)}")

    async def _str_replace(
        self, file_path: str, old_str: str, new_str: str
    ) -> ToolResult:
        """替换文件中的特定文本"""
        try:
            # Ensure sandbox is initialized
            await self._ensure_sandbox()

            file_path = self.clean_path(file_path)
            full_path = f"{self.workspace_path}/{file_path}"
            if not self._file_exists(full_path):
                return self.fail_response(f"文件 '{file_path}' 不存在")

            content = self.sandbox.fs.download_file(full_path).decode()
            old_str = old_str.expandtabs()
            new_str = new_str.expandtabs()

            occurrences = content.count(old_str)
            if occurrences == 0:
                return self.fail_response(f"未在文件中找到字符串 '{old_str}'")
            if occurrences > 1:
                lines = [
                    i + 1
                    for i, line in enumerate(content.split("\n"))
                    if old_str in line
                ]
                return self.fail_response(
                    f"在第 {lines} 行找到多个匹配项。请确保字符串唯一"
                )

            # Perform replacement
            new_content = content.replace(old_str, new_str)
            self.sandbox.fs.upload_file(new_content.encode(), full_path)

            # Show snippet around the edit
            replacement_line = content.split(old_str)[0].count("\n")
            start_line = max(0, replacement_line - self.SNIPPET_LINES)
            end_line = replacement_line + self.SNIPPET_LINES + new_str.count("\n")
            snippet = "\n".join(new_content.split("\n")[start_line : end_line + 1])

            message = f"替换成功。"

            return self.success_response(message)

        except Exception as e:
            return self.fail_response(f"替换字符串时出错：{str(e)}")

    async def _full_file_rewrite(
        self, file_path: str, file_contents: str, permissions: str = "644"
    ) -> ToolResult:
        """使用新内容完全重写现有文件"""
        try:
            # Ensure sandbox is initialized
            await self._ensure_sandbox()

            file_path = self.clean_path(file_path)
            full_path = f"{self.workspace_path}/{file_path}"
            if not self._file_exists(full_path):
                return self.fail_response(
                    f"文件 '{file_path}' 不存在。使用 create_file 创建新文件。"
                )

            self.sandbox.fs.upload_file(file_contents.encode(), full_path)
            self.sandbox.fs.set_file_permissions(full_path, permissions)

            message = f"文件 '{file_path}' 已完全重写成功。"

            # Check if index.html was rewritten and add 8080 server info (only in root workspace)
            if file_path.lower() == "index.html":
                try:
                    website_link = self.sandbox.get_preview_link(8080)
                    website_url = (
                        website_link.url
                        if hasattr(website_link, "url")
                        else str(website_link).split("url='")[1].split("'")[0]
                    )
                    message += f"\n\n[自动检测到 index.html - HTTP 服务器地址：{website_url}]"
                    message += "\n[注意：请使用上面提供的 HTTP 服务器地址，不要启动新的服务器]"
                except Exception as e:
                    logger.warning(
                        f"获取 index.html 的网站 URL 失败：{str(e)}"
                    )

            return self.success_response(message)
        except Exception as e:
            return self.fail_response(f"重写文件时出错：{str(e)}")

    async def _delete_file(self, file_path: str) -> ToolResult:
        """删除指定路径的文件"""
        try:
            # Ensure sandbox is initialized
            await self._ensure_sandbox()

            file_path = self.clean_path(file_path)
            full_path = f"{self.workspace_path}/{file_path}"
            if not self._file_exists(full_path):
                return self.fail_response(f"文件 '{file_path}' 不存在")

            self.sandbox.fs.delete_file(full_path)
            return self.success_response(f"文件 '{file_path}' 删除成功。")
        except Exception as e:
            return self.fail_response(f"删除文件时出错：{str(e)}")

    async def cleanup(self):
        """清理沙箱资源。"""

    @classmethod
    def create_with_context(cls, context: Context) -> "SandboxFilesTool[Context]":
        """使用特定上下文创建 SandboxFilesTool 的工厂方法。"""
        raise NotImplementedError(
            "SandboxFilesTool 未实现 create_with_context"
        )
