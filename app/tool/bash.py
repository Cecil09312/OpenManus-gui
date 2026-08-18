import asyncio
import os
from typing import Optional

from app.exceptions import ToolError
from app.tool.base import BaseTool, CLIResult

_BASH_DESCRIPTION = """在终端中执行 bash 命令。
* 长时间运行的命令：对于可能无限期运行的命令，应在后台运行并将输出重定向到文件，例如 command = `python3 app.py > server.log 2>&1 &`。
* 交互式：如果 bash 命令返回退出码 `-1`，表示进程尚未完成。助手必须再次发送一个空 `command` 的终端调用（将获取额外的日志），或者可以向运行中进程的 STDIN 发送额外文本（将 `command` 设置为该文本），或者可以发送 command=`ctrl+c` 来中断进程。
* 超时：如果命令执行结果显示"命令已超时。正在向进程发送 SIGINT"，助手应在后台重试运行该命令。
"""


class _BashSession:
    """bash shell 会话。"""

    _started: bool
    _process: asyncio.subprocess.Process

    command: str = "/bin/bash"
    _output_delay: float = 0.2  # seconds
    _timeout: float = 120.0  # seconds
    _sentinel: str = "<<exit>>"

    def __init__(self):
        self._started = False
        self._timed_out = False

    async def start(self):
        if self._started:
            return

        self._process = await asyncio.create_subprocess_shell(
            self.command,
            preexec_fn=os.setsid,
            shell=True,
            bufsize=0,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        self._started = True

    def stop(self):
        """终止 bash shell。"""
        if not self._started:
            raise ToolError("会话尚未启动。")
        if self._process.returncode is not None:
            return
        self._process.terminate()

    async def run(self, command: str):
        """在 bash shell 中执行命令。"""
        if not self._started:
            raise ToolError("会话尚未启动。")
        if self._process.returncode is not None:
            return CLIResult(
                system="工具必须重启",
                error=f"bash 已退出，返回码为 {self._process.returncode}",
            )
        if self._timed_out:
            raise ToolError(
                f"已超时：bash 在 {self._timeout} 秒内未返回，必须重启",
            )

        # 我们知道这些不是 None，因为我们用 PIPEs 创建了进程
        assert self._process.stdin
        assert self._process.stdout
        assert self._process.stderr

        # send command to the process
        self._process.stdin.write(
            command.encode() + f"; echo '{self._sentinel}'\n".encode()
        )
        await self._process.stdin.drain()

        # read output from the process, until the sentinel is found
        try:
            async with asyncio.timeout(self._timeout):
                while True:
                    await asyncio.sleep(self._output_delay)
                    # if we read directly from stdout/stderr, it will wait forever for
                    # EOF. use the StreamReader buffer directly instead.
                    output = (
                        self._process.stdout._buffer.decode()
                    )  # pyright: ignore[reportAttributeAccessIssue]
                    if self._sentinel in output:
                        # strip the sentinel and break
                        output = output[: output.index(self._sentinel)]
                        break
        except asyncio.TimeoutError:
            self._timed_out = True
            raise ToolError(
                f"已超时：bash 在 {self._timeout} 秒内未返回，必须重启",
            ) from None

        if output.endswith("\n"):
            output = output[:-1]

        error = (
            self._process.stderr._buffer.decode()
        )  # pyright: ignore[reportAttributeAccessIssue]
        if error.endswith("\n"):
            error = error[:-1]

        # clear the buffers so that the next output can be read correctly
        self._process.stdout._buffer.clear()  # pyright: ignore[reportAttributeAccessIssue]
        self._process.stderr._buffer.clear()  # pyright: ignore[reportAttributeAccessIssue]

        return CLIResult(output=output, error=error)


class Bash(BaseTool):
    """用于执行 bash 命令的工具"""

    name: str = "bash"
    description: str = _BASH_DESCRIPTION
    parameters: dict = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的 bash 命令。可以为空以查看之前退出码为 `-1` 时的额外日志。可以是 `ctrl+c` 来中断当前运行的进程。",
            },
        },
        "required": ["command"],
    }

    _session: Optional[_BashSession] = None

    async def execute(
        self, command: str | None = None, restart: bool = False, **kwargs
    ) -> CLIResult:
        if restart:
            if self._session:
                self._session.stop()
            self._session = _BashSession()
            await self._session.start()

            return CLIResult(system="工具已重启。")

        if self._session is None:
            self._session = _BashSession()
            await self._session.start()

        if command is not None:
            return await self._session.run(command)

        raise ToolError("no command provided.")


if __name__ == "__main__":
    bash = Bash()
    rst = asyncio.run(bash.execute("ls -l"))
    print(rst)
