"""支持沙箱的文件和目录操作工具。"""

from collections import defaultdict
from pathlib import Path
from typing import Any, DefaultDict, List, Literal, Optional, get_args

from app.config import config
from app.exceptions import ToolError
from app.tool import BaseTool
from app.tool.base import CLIResult, ToolResult
from app.tool.file_operators import (
    FileOperator,
    LocalFileOperator,
    PathLike,
    SandboxFileOperator,
)

Command = Literal[
    "view",
    "create",
    "str_replace",
    "insert",
    "undo_edit",
]

# 常量
SNIPPET_LINES: int = 4
MAX_RESPONSE_LEN: int = 16000
TRUNCATED_MESSAGE: str = (
    "<响应已裁剪><NOTE>为节省上下文，仅向你显示了此文件的一部分。 "
    "你应该在使用 `grep -n` 搜索文件内容后重试此工具，"
    "以找到你所需内容的行号。</NOTE>"
)

# 工具描述
_STR_REPLACE_EDITOR_DESCRIPTION = """用于查看、创建和编辑文件的自定义编辑工具
* 状态在命令调用和与用户的讨论之间保持持久
* 如果 `path` 是文件，`view` 显示 `cat -n` 的结果。如果 `path` 是目录，`view` 列出最多2层深度的非隐藏文件和目录
* 如果指定的 `path` 已作为文件存在，则不能使用 `create` 命令
* 如果 `command` 生成长输出，将被截断并标记为 `<response clipped>`
* `undo_edit` 命令将撤销对 `path` 文件的最后一次编辑

使用 `str_replace` 命令的注意事项：
* `old_str` 参数应与原文件中的一个或多个连续行完全匹配。注意空格！
* 如果 `old_str` 参数在文件中不唯一，则不会执行替换。确保在 `old_str` 中包含足够的上下文以使其唯一
* `new_str` 参数应包含要替换 `old_str` 的编辑后的行
"""


def maybe_truncate(
    content: str, truncate_after: Optional[int] = MAX_RESPONSE_LEN
) -> str:
    """如果内容超过指定长度，则截断内容并附加提示。"""
    if not truncate_after or len(content) <= truncate_after:
        return content
    return content[:truncate_after] + TRUNCATED_MESSAGE


class StrReplaceEditor(BaseTool):
    """一个支持沙箱的文件查看、创建和编辑工具。"""

    name: str = "str_replace_editor"
    description: str = _STR_REPLACE_EDITOR_DESCRIPTION
    parameters: dict = {
        "type": "object",
        "properties": {
            "command": {
                "description": "要运行的命令。允许的选项：`view`、`create`、`str_replace`、`insert`、`undo_edit`。",
                "enum": ["view", "create", "str_replace", "insert", "undo_edit"],
                "type": "string",
            },
            "path": {
                "description": "文件或目录的绝对路径。",
                "type": "string",
            },
            "file_text": {
                "description": "`create` 命令的必需参数，包含要创建的文件内容。",
                "type": "string",
            },
            "old_str": {
                "description": "`str_replace` 命令的必需参数，包含 `path` 中要替换的字符串。",
                "type": "string",
            },
            "new_str": {
                "description": "`str_replace` 命令的可选参数，包含新字符串（如未提供，则不添加任何字符串）。`insert` 命令的必需参数，包含要插入的字符串。",
                "type": "string",
            },
            "insert_line": {
                "description": "`insert` 命令的必需参数。`new_str` 将插入到 `path` 文件中 `insert_line` 行之后。",
                "type": "integer",
            },
            "view_range": {
                "description": "当 `path` 指向文件时，`view` 命令的可选参数。如未提供，则显示完整文件。如已提供，则按指定行号范围显示文件内容，例如 [11, 12] 将显示第11和12行。行号从1开始。设置 `[start_line, -1]` 显示从 `start_line` 到文件末尾的所有行。",
                "items": {"type": "integer"},
                "type": "array",
            },
        },
        "required": ["command", "path"],
    }
    _file_history: DefaultDict[PathLike, List[str]] = defaultdict(list)
    _local_operator: LocalFileOperator = LocalFileOperator()
    _sandbox_operator: SandboxFileOperator = SandboxFileOperator()

    # def _get_operator(self, use_sandbox: bool) -> FileOperator:
    def _get_operator(self) -> FileOperator:
        """根据执行模式获取相应的文件操作器。"""
        return (
            self._sandbox_operator
            if config.sandbox.use_sandbox
            else self._local_operator
        )

    async def execute(
        self,
        *,
        command: Command,
        path: str,
        file_text: str | None = None,
        view_range: list[int] | None = None,
        old_str: str | None = None,
        new_str: str | None = None,
        insert_line: int | None = None,
        **kwargs: Any,
    ) -> str:
        """执行文件操作命令。"""
        # 获取相应的文件操作器
        operator = self._get_operator()

        # 验证路径和命令组合
        await self.validate_path(command, Path(path), operator)

        # 执行相应命令
        if command == "view":
            result = await self.view(path, view_range, operator)
        elif command == "create":
            if file_text is None:
                raise ToolError("命令 create 需要参数 `file_text`")
            await operator.write_file(path, file_text)
            self._file_history[path].append(file_text)
            result = ToolResult(output=f"文件已在以下位置成功创建：{path}")
        elif command == "str_replace":
            if old_str is None:
                raise ToolError("命令 str_replace 需要参数 `old_str`")
            result = await self.str_replace(path, old_str, new_str, operator)
        elif command == "insert":
            if insert_line is None:
                raise ToolError("命令 insert 需要参数 `insert_line`")
            if new_str is None:
                raise ToolError("命令 insert 需要参数 `new_str`")
            result = await self.insert(path, insert_line, new_str, operator)
        elif command == "undo_edit":
            result = await self.undo_edit(path, operator)
        else:
            # 这应该被类型检查捕获，但为了安全起见我们保留它
            raise ToolError(
                f'未识别的命令 {command}。{self.name} 工具允许的命令为：{", ".join(get_args(Command))}'
            )

        return str(result)

    async def validate_path(
        self, command: str, path: Path, operator: FileOperator
    ) -> None:
        """根据执行环境验证路径和命令组合。"""
        # 检查路径是否为绝对路径
        if not path.is_absolute():
            raise ToolError(f"路径 {path} 不是绝对路径")

        # 仅对非 create 命令检查路径是否存在
        if command != "create":
            if not await operator.exists(path):
                raise ToolError(f"路径 {path} 不存在。请提供有效的路径。")

            # 检查路径是否为目录
            is_dir = await operator.is_directory(path)
            if is_dir and command != "view":
                raise ToolError(f"路径 {path} 是一个目录，只有 `view` 命令可用于目录")

        # 对 create 命令检查文件是否已存在
        elif command == "create":
            exists = await operator.exists(path)
            if exists:
                raise ToolError(
                    f"文件已存在于：{path}。无法使用命令 `create` 覆盖文件。"
                )

    async def view(
        self,
        path: PathLike,
        view_range: Optional[List[int]] = None,
        operator: FileOperator = None,
    ) -> CLIResult:
        """显示文件或目录内容。"""
        # 判断路径是否为目录
        is_dir = await operator.is_directory(path)

        if is_dir:
            # 目录处理
            if view_range:
                raise ToolError("当 `path` 指向目录时不允许使用 `view_range` 参数。")

            return await self._view_directory(path, operator)
        else:
            # 文件处理
            return await self._view_file(path, operator, view_range)

    @staticmethod
    async def _view_directory(path: PathLike, operator: FileOperator) -> CLIResult:
        """显示目录内容。"""
        find_cmd = f"find {path} -maxdepth 2 -not -path '*/\\.*'"

        # 使用操作器执行命令
        returncode, stdout, stderr = await operator.run_command(find_cmd)

        if not stderr:
            stdout = (
                f"以下是 {path} 中最多2层深度的文件和目录，"
                f"已排除隐藏项：\n{stdout}\n"
            )

        return CLIResult(output=stdout, error=stderr)

    async def _view_file(
        self,
        path: PathLike,
        operator: FileOperator,
        view_range: Optional[List[int]] = None,
    ) -> CLIResult:
        """显示文件内容，可选择在指定行范围内显示。"""
        # 读取文件内容
        file_content = await operator.read_file(path)
        init_line = 1

        # 如果指定了查看范围则应用
        if view_range:
            if len(view_range) != 2 or not all(isinstance(i, int) for i in view_range):
                raise ToolError("无效的 `view_range`。它应是一个包含两个整数的列表。")

            file_lines = file_content.split("\n")
            n_lines_file = len(file_lines)
            init_line, final_line = view_range

            # 验证查看范围
            if init_line < 1 or init_line > n_lines_file:
                raise ToolError(
                    f"无效的 `view_range`：{view_range}。其第一个元素 `{init_line}` 应在"
                    f"文件行数范围内：{[1, n_lines_file]}"
                )
            if final_line > n_lines_file:
                raise ToolError(
                    f"无效的 `view_range`：{view_range}。其第二个元素 `{final_line}` 应"
                    f"小于文件行数：`{n_lines_file}`"
                )
            if final_line != -1 and final_line < init_line:
                raise ToolError(
                    f"无效的 `view_range`：{view_range}。其第二个元素 `{final_line}` 应"
                    f"大于或等于第一个元素 `{init_line}`"
                )

            # 应用范围
            if final_line == -1:
                file_content = "\n".join(file_lines[init_line - 1 :])
            else:
                file_content = "\n".join(file_lines[init_line - 1 : final_line])

        # 格式化并返回结果
        return CLIResult(
            output=self._make_output(file_content, str(path), init_line=init_line)
        )

    async def str_replace(
        self,
        path: PathLike,
        old_str: str,
        new_str: Optional[str] = None,
        operator: FileOperator = None,
    ) -> CLIResult:
        """用新字符串替换文件中的唯一字符串。"""
        # 读取文件内容并展开制表符
        file_content = (await operator.read_file(path)).expandtabs()
        old_str = old_str.expandtabs()
        new_str = new_str.expandtabs() if new_str is not None else ""

        # 检查 old_str 在文件中是否唯一
        occurrences = file_content.count(old_str)
        if occurrences == 0:
            raise ToolError(f"未执行替换，old_str `{old_str}` 未在 {path} 中完整出现。")
        elif occurrences > 1:
            # 查找出现位置的行号
            file_content_lines = file_content.split("\n")
            lines = [
                idx + 1
                for idx, line in enumerate(file_content_lines)
                if old_str in line
            ]
            raise ToolError(
                f"未执行替换。old_str `{old_str}` 在行 {lines} 中多次出现。"
                f"请确保其唯一性"
            )

        # 用 new_str 替换 old_str
        new_file_content = file_content.replace(old_str, new_str)

        # 将新内容写入文件
        await operator.write_file(path, new_file_content)

        # 将原始内容保存到历史记录
        self._file_history[path].append(file_content)

        # 创建编辑部分的代码片段
        replacement_line = file_content.split(old_str)[0].count("\n")
        start_line = max(0, replacement_line - SNIPPET_LINES)
        end_line = replacement_line + SNIPPET_LINES + new_str.count("\n")
        snippet = "\n".join(new_file_content.split("\n")[start_line : end_line + 1])

        # 准备成功消息
        success_msg = f"文件 {path} 已被编辑。"
        success_msg += self._make_output(snippet, f"{path} 的代码片段", start_line + 1)
        success_msg += "请检查更改并确保符合预期。如有必要请再次编辑文件。"

        return CLIResult(output=success_msg)

    async def insert(
        self,
        path: PathLike,
        insert_line: int,
        new_str: str,
        operator: FileOperator = None,
    ) -> CLIResult:
        """在文件的指定行处插入文本。"""
        # 读取并准备内容
        file_text = (await operator.read_file(path)).expandtabs()
        new_str = new_str.expandtabs()
        file_text_lines = file_text.split("\n")
        n_lines_file = len(file_text_lines)

        # 验证 insert_line
        if insert_line < 0 or insert_line > n_lines_file:
            raise ToolError(
                f"无效的 `insert_line` 参数：{insert_line}。它应在"
                f"文件行数范围内：{[0, n_lines_file]}"
            )

        # 执行插入
        new_str_lines = new_str.split("\n")
        new_file_text_lines = (
            file_text_lines[:insert_line]
            + new_str_lines
            + file_text_lines[insert_line:]
        )

        # 创建预览代码片段
        snippet_lines = (
            file_text_lines[max(0, insert_line - SNIPPET_LINES) : insert_line]
            + new_str_lines
            + file_text_lines[insert_line : insert_line + SNIPPET_LINES]
        )

        # 合并行并写入文件
        new_file_text = "\n".join(new_file_text_lines)
        snippet = "\n".join(snippet_lines)

        await operator.write_file(path, new_file_text)
        self._file_history[path].append(file_text)

        # 准备成功消息
        success_msg = f"文件 {path} 已被编辑。"
        success_msg += self._make_output(
            snippet,
            "已编辑文件的代码片段",
            max(1, insert_line - SNIPPET_LINES + 1),
        )
        success_msg += "请检查更改并确保符合预期（正确的缩进、无重复行等）。如有必要请再次编辑文件。"

        return CLIResult(output=success_msg)

    async def undo_edit(
        self, path: PathLike, operator: FileOperator = None
    ) -> CLIResult:
        """撤销对文件的最后一次编辑。"""
        if not self._file_history[path]:
            raise ToolError(f"未找到 {path} 的编辑历史。")

        old_text = self._file_history[path].pop()
        await operator.write_file(path, old_text)

        return CLIResult(
            output=f"已成功撤销对 {path} 的最后一次编辑。{self._make_output(old_text, str(path))}"
        )

    def _make_output(
        self,
        file_content: str,
        file_descriptor: str,
        init_line: int = 1,
        expand_tabs: bool = True,
    ) -> str:
        """格式化文件内容并带行号显示。"""
        file_content = maybe_truncate(file_content)
        if expand_tabs:
            file_content = file_content.expandtabs()

        # 为每行添加行号
        file_content = "\n".join(
            [
                f"{i + init_line:6}\t{line}"
                for i, line in enumerate(file_content.split("\n"))
            ]
        )

        return (
            f"以下是在 {file_descriptor} 上运行 `cat -n` 的结果：\n"
            + file_content
            + "\n"
        )
