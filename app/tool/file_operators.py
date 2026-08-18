"""本地和沙箱环境的文件操作接口和实现。"""

import asyncio
from pathlib import Path
from typing import Optional, Protocol, Tuple, Union, runtime_checkable

from app.config import SandboxSettings
from app.exceptions import ToolError
from app.sandbox.client import SANDBOX_CLIENT


PathLike = Union[str, Path]


@runtime_checkable
class FileOperator(Protocol):
    """不同环境下文件操作的接口。"""

    async def read_file(self, path: PathLike) -> str:
        """从文件读取内容。"""
        ...

    async def write_file(self, path: PathLike, content: str) -> None:
        """向文件写入内容。"""
        ...

    async def is_directory(self, path: PathLike) -> bool:
        """检查路径是否指向目录。"""
        ...

    async def exists(self, path: PathLike) -> bool:
        """检查路径是否存在。"""
        ...

    async def run_command(
        self, cmd: str, timeout: Optional[float] = 120.0
    ) -> Tuple[int, str, str]:
        """运行 shell 命令并返回 (返回码, 标准输出, 标准错误)。"""
        ...


class LocalFileOperator(FileOperator):
    """本地文件系统的文件操作实现。"""

    encoding: str = "utf-8"

    async def read_file(self, path: PathLike) -> str:
        """从本地文件读取内容。"""
        try:
            return Path(path).read_text(encoding=self.encoding)
        except Exception as e:
            raise ToolError(f"读取 {path} 失败：{str(e)}") from None

    async def write_file(self, path: PathLike, content: str) -> None:
        """向本地文件写入内容。"""
        try:
            Path(path).write_text(content, encoding=self.encoding)
        except Exception as e:
            raise ToolError(f"写入 {path} 失败：{str(e)}") from None

    async def is_directory(self, path: PathLike) -> bool:
        """检查路径是否指向目录。"""
        return Path(path).is_dir()

    async def exists(self, path: PathLike) -> bool:
        """检查路径是否存在。"""
        return Path(path).exists()

    async def run_command(
        self, cmd: str, timeout: Optional[float] = 120.0
    ) -> Tuple[int, str, str]:
        """在本地运行 shell 命令。"""
        process = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
            return (
                process.returncode or 0,
                stdout.decode(),
                stderr.decode(),
            )
        except asyncio.TimeoutError as exc:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            raise TimeoutError(
                f"命令 '{cmd}' 在 {timeout} 秒后超时"
            ) from exc


class SandboxFileOperator(FileOperator):
    """沙箱环境的文件操作实现。"""

    def __init__(self):
        self.sandbox_client = SANDBOX_CLIENT

    async def _ensure_sandbox_initialized(self):
        """确保沙箱已初始化。"""
        if not self.sandbox_client.sandbox:
            await self.sandbox_client.create(config=SandboxSettings())

    async def read_file(self, path: PathLike) -> str:
        """从沙箱中的文件读取内容。"""
        await self._ensure_sandbox_initialized()
        try:
            return await self.sandbox_client.read_file(str(path))
        except Exception as e:
            raise ToolError(f"在沙箱中读取 {path} 失败：{str(e)}") from None

    async def write_file(self, path: PathLike, content: str) -> None:
        """向沙箱中的文件写入内容。"""
        await self._ensure_sandbox_initialized()
        try:
            await self.sandbox_client.write_file(str(path), content)
        except Exception as e:
            raise ToolError(f"在沙箱中写入 {path} 失败：{str(e)}") from None

    async def is_directory(self, path: PathLike) -> bool:
        """检查沙箱中的路径是否指向目录。"""
        await self._ensure_sandbox_initialized()
        result = await self.sandbox_client.run_command(
            f"test -d {path} && echo 'true' || echo 'false'"
        )
        return result.strip() == "true"

    async def exists(self, path: PathLike) -> bool:
        """检查沙箱中的路径是否存在。"""
        await self._ensure_sandbox_initialized()
        result = await self.sandbox_client.run_command(
            f"test -e {path} && echo 'true' || echo 'false'"
        )
        return result.strip() == "true"

    async def run_command(
        self, cmd: str, timeout: Optional[float] = 120.0
    ) -> Tuple[int, str, str]:
        """在沙箱环境中运行命令。"""
        await self._ensure_sandbox_initialized()
        try:
            stdout = await self.sandbox_client.run_command(
                cmd, timeout=int(timeout) if timeout else None
            )
            return (
                0,  # 始终返回 0，因为沙箱没有明确的返回码
                stdout,
                "",  # 当前沙箱实现不捕获标准错误
            )
        except TimeoutError as exc:
            raise TimeoutError(
                f"命令 '{cmd}' 在沙箱中 {timeout} 秒后超时"
            ) from exc
        except Exception as exc:
            return 1, "", f"在沙箱中执行命令时出错：{str(exc)}"
