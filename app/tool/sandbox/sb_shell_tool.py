import asyncio
import time
from typing import Any, Dict, Optional, TypeVar
from uuid import uuid4

from app.daytona.tool_base import Sandbox, SandboxToolsBase
from app.tool.base import ToolResult
from app.utils.logger import logger


Context = TypeVar("Context")
_SHELL_DESCRIPTION = """\
在工作区目录中执行 shell 命令。
重要提示：命令默认为非阻塞模式，在 tmux 会话中运行。
这非常适合长时间运行的操作，如启动服务器或构建过程。
使用会话来维护命令之间的状态。
此工具对于运行 CLI 工具、安装包和管理系统操作至关重要。
"""


class SandboxShellTool(SandboxToolsBase):
    """在 Daytona 沙箱中执行任务的工具，具有浏览器使用能力。
    使用会话维护命令之间的状态，并提供全面的进程管理。
    """

    name: str = "sandbox_shell"
    description: str = _SHELL_DESCRIPTION
    parameters: dict = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "execute_command",
                    "check_command_output",
                    "terminate_command",
                    "list_commands",
                ],
                "description": "要执行的 shell 操作"
            },
            "command": {
                "type": "string",
                "description": "要执行的 shell 命令。用于运行 CLI 工具、安装包"
                "或系统操作。命令可以使用 &&、|| 和 | 运算符连接。",
            },
            "folder": {
                "type": "string",
                "description": "可选的相对路径，指向 /workspace 的子目录，命令将在该目录中"
                "执行。例如：'data/pdfs'",
            },
            "session_name": {
                "type": "string",
                "description": "可选的 tmux 会话名称。为需要维护状态的相关命令"
                "使用命名会话。默认为随机会话名。",
            },
            "blocking": {
                "type": "boolean",
                "description": "是否等待命令完成。默认为 false，即非阻塞"
                "执行。",
                "default": False,
            },
            "timeout": {
                "type": "integer",
                "description": "可选的阻塞命令超时时间（秒）。默认为 60。非阻塞"
                "命令忽略此参数。",
                "default": 60,
            },
            "kill_session": {
                "type": "boolean",
                "description": "检查后是否终止 tmux 会话。完成命令后设置为 true。",
                "default": False,
            },
        },
        "required": ["action"],
        "dependencies": {
            "execute_command": ["command"],
            "check_command_output": ["session_name"],
            "terminate_command": ["session_name"],
            "list_commands": [],
        },
    }

    def __init__(
        self, sandbox: Optional[Sandbox] = None, thread_id: Optional[str] = None, **data
    ):
        """使用可选的沙箱和 thread_id 初始化。"""
        super().__init__(**data)
        if sandbox is not None:
            self._sandbox = sandbox

    async def _ensure_session(self, session_name: str = "default") -> str:
        """确保会话存在并返回其 ID。"""
        if session_name not in self._sessions:
            session_id = str(uuid4())
            try:
                await self._ensure_sandbox()  # Ensure sandbox is initialized
                self.sandbox.process.create_session(session_id)
                self._sessions[session_name] = session_id
            except Exception as e:
                raise RuntimeError(f"创建会话失败：{str(e)}")
        return self._sessions[session_name]

    async def _cleanup_session(self, session_name: str):
        """如果会话存在则清理它。"""
        if session_name in self._sessions:
            try:
                await self._ensure_sandbox()  # Ensure sandbox is initialized
                self.sandbox.process.delete_session(self._sessions[session_name])
                del self._sessions[session_name]
            except Exception as e:
                print(f"警告：清理会话 {session_name} 失败：{str(e)}")

    async def _execute_raw_command(self, command: str) -> Dict[str, Any]:
        """直接在沙箱中执行原始命令。"""
        # Ensure session exists for raw commands
        session_id = await self._ensure_session("raw_commands")

        # Execute command in session
        from app.daytona.sandbox import SessionExecuteRequest

        req = SessionExecuteRequest(
            command=command, run_async=False, cwd=self.workspace_path
        )

        response = self.sandbox.process.execute_session_command(
            session_id=session_id,
            req=req,
            timeout=30,  # Short timeout for utility commands
        )

        logs = self.sandbox.process.get_session_command_logs(
            session_id=session_id, command_id=response.cmd_id
        )

        return {"output": logs, "exit_code": response.exit_code}

    async def _execute_command(
        self,
        command: str,
        folder: Optional[str] = None,
        session_name: Optional[str] = None,
        blocking: bool = False,
        timeout: int = 60,
    ) -> ToolResult:
        try:
            # Ensure sandbox is initialized
            await self._ensure_sandbox()

            # Set up working directory
            cwd = self.workspace_path
            if folder:
                folder = folder.strip("/")
                cwd = f"{self.workspace_path}/{folder}"

            # Generate a session name if not provided
            if not session_name:
                session_name = f"session_{str(uuid4())[:8]}"

            # Check if tmux session already exists
            check_session = await self._execute_raw_command(
                f"tmux has-session -t {session_name} 2>/dev/null || echo 'not_exists'"
            )
            session_exists = "not_exists" not in check_session.get("output", "")

            if not session_exists:
                # Create a new tmux session
                await self._execute_raw_command(
                    f"tmux new-session -d -s {session_name}"
                )

            # Ensure we're in the correct directory and send command to tmux
            full_command = f"cd {cwd} && {command}"
            wrapped_command = full_command.replace('"', '\\"')  # Escape double quotes

            # Send command to tmux session
            await self._execute_raw_command(
                f'tmux send-keys -t {session_name} "{wrapped_command}" Enter'
            )

            if blocking:
                # For blocking execution, wait and capture output
                start_time = time.time()
                while (time.time() - start_time) < timeout:
                    # Wait a bit before checking
                    time.sleep(2)

                    # Check if session still exists (command might have exited)
                    check_result = await self._execute_raw_command(
                        f"tmux has-session -t {session_name} 2>/dev/null || echo 'ended'"
                    )
                    if "ended" in check_result.get("output", ""):
                        break

                    # Get current output and check for common completion indicators
                    output_result = await self._execute_raw_command(
                        f"tmux capture-pane -t {session_name} -p -S - -E -"
                    )
                    current_output = output_result.get("output", "")

                    # Check for prompt indicators that suggest command completion
                    last_lines = current_output.split("\n")[-3:]
                    completion_indicators = [
                        "$",
                        "#",
                        ">",
                        "Done",
                        "Completed",
                        "Finished",
                        "✓",
                    ]
                    if any(
                        indicator in line
                        for indicator in completion_indicators
                        for line in last_lines
                    ):
                        break

                # Capture final output
                output_result = await self._execute_raw_command(
                    f"tmux capture-pane -t {session_name} -p -S - -E -"
                )
                final_output = output_result.get("output", "")

                # Kill the session after capture
                await self._execute_raw_command(f"tmux kill-session -t {session_name}")

                return self.success_response(
                    {
                        "output": final_output,
                        "session_name": session_name,
                        "cwd": cwd,
                        "completed": True,
                    }
                )
            else:
                # For non-blocking, just return immediately
                return self.success_response(
                    {
                        "session_name": session_name,
                        "cwd": cwd,
                        "message": f"命令已发送到 tmux 会话 '{session_name}'。使用 check_command_output 查看结果。",
                        "completed": False,
                    }
                )

        except Exception as e:
            # Attempt to clean up session in case of error
            if session_name:
                try:
                    await self._execute_raw_command(
                        f"tmux kill-session -t {session_name}"
                    )
                except:
                    pass
            return self.fail_response(f"执行命令时出错：{str(e)}")

    async def _check_command_output(
        self, session_name: str, kill_session: bool = False
    ) -> ToolResult:
        try:
            # Ensure sandbox is initialized
            await self._ensure_sandbox()

            # Check if session exists
            check_result = await self._execute_raw_command(
                f"tmux has-session -t {session_name} 2>/dev/null || echo 'not_exists'"
            )
            if "not_exists" in check_result.get("output", ""):
                return self.fail_response(
                    f"Tmux 会话 '{session_name}' 不存在。"
                )

            # Get output from tmux pane
            output_result = await self._execute_raw_command(
                f"tmux capture-pane -t {session_name} -p -S - -E -"
            )
            output = output_result.get("output", "")

            # Kill session if requested
            if kill_session:
                await self._execute_raw_command(f"tmux kill-session -t {session_name}")
                termination_status = "会话已终止。"
            else:
                termination_status = "会话仍在运行。"

            return self.success_response(
                {
                    "output": output,
                    "session_name": session_name,
                    "status": termination_status,
                }
            )

        except Exception as e:
            return self.fail_response(f"检查命令输出时出错：{str(e)}")

    async def _terminate_command(self, session_name: str) -> ToolResult:
        try:
            # Ensure sandbox is initialized
            await self._ensure_sandbox()

            # Check if session exists
            check_result = await self._execute_raw_command(
                f"tmux has-session -t {session_name} 2>/dev/null || echo 'not_exists'"
            )
            if "not_exists" in check_result.get("output", ""):
                return self.fail_response(
                    f"Tmux 会话 '{session_name}' 不存在。"
                )

            # Kill the session
            await self._execute_raw_command(f"tmux kill-session -t {session_name}")

            return self.success_response(
                {"message": f"Tmux 会话 '{session_name}' 已成功终止。"}
            )

        except Exception as e:
            return self.fail_response(f"终止命令时出错：{str(e)}")

    async def _list_commands(self) -> ToolResult:
        try:
            # Ensure sandbox is initialized
            await self._ensure_sandbox()

            # List all tmux sessions
            result = await self._execute_raw_command(
                "tmux list-sessions 2>/dev/null || echo 'No sessions'"
            )
            output = result.get("output", "")

            if "No sessions" in output or not output.strip():
                return self.success_response(
                    {"message": "未找到活动的 tmux 会话。", "sessions": []}
                )

            # Parse session list
            sessions = []
            for line in output.split("\n"):
                if line.strip():
                    parts = line.split(":")
                    if parts:
                        session_name = parts[0].strip()
                        sessions.append(session_name)

            return self.success_response(
                {
                    "message": f"找到 {len(sessions)} 个活动会话。",
                    "sessions": sessions,
                }
            )

        except Exception as e:
            return self.fail_response(f"列出命令时出错：{str(e)}")

    async def execute(
        self,
        action: str,
        command: str,
        folder: Optional[str] = None,
        session_name: Optional[str] = None,
        blocking: bool = False,
        timeout: int = 60,
        kill_session: bool = False,
    ) -> ToolResult:
        """
        在沙箱环境中执行浏览器操作。
        参数：
            timeout:
            blocking:
            session_name:
            folder:
            command:
            kill_session:
            action: 要执行的浏览器操作
        返回：
            包含操作输出或错误的 ToolResult
        """
        async with asyncio.Lock():
            try:
                # Navigation actions
                if action == "execute_command":
                    if not command:
                        return self.fail_response("navigation 操作需要 command")
                    return await self._execute_command(
                        command, folder, session_name, blocking, timeout
                    )
                elif action == "check_command_output":
                    if session_name is None:
                        return self.fail_response(
                            "navigation 操作需要 session_name"
                        )
                    return await self._check_command_output(session_name, kill_session)
                elif action == "terminate_command":
                    if session_name is None:
                        return self.fail_response(
                            "click_element 操作需要 session_name"
                        )
                    return await self._terminate_command(session_name)
                elif action == "list_commands":
                    return await self._list_commands()
                else:
                    return self.fail_response(f"未知操作：{action}")
            except Exception as e:
                logger.error(f"执行 shell 操作时出错：{e}")
                return self.fail_response(f"执行 shell 操作时出错：{e}")

    async def cleanup(self):
        """清理所有会话。"""
        for session_name in list(self._sessions.keys()):
            await self._cleanup_session(session_name)

        # Also clean up any tmux sessions
        try:
            await self._ensure_sandbox()
            await self._execute_raw_command("tmux kill-server 2>/dev/null || true")
        except Exception as e:
            logger.error(f"沙箱清理操作出错：{e}")
