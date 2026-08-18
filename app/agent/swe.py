from typing import List

from pydantic import Field

from app.agent.toolcall import ToolCallAgent
from app.prompt.swe import SYSTEM_PROMPT
from app.tool import Bash, StrReplaceEditor, Terminate, ToolCollection


class SWEAgent(ToolCallAgent):
    """一个实现 SWEAgent 范式的智能体，用于执行代码和自然语言对话。"""

    name: str = "swe"
    description: str = "一个直接与计算机交互以解决任务的自主 AI 编程员。"

    system_prompt: str = SYSTEM_PROMPT
    next_step_prompt: str = ""

    available_tools: ToolCollection = ToolCollection(
        Bash(), StrReplaceEditor(), Terminate()
    )
    special_tool_names: List[str] = Field(default_factory=lambda: [Terminate().name])

    max_steps: int = 20
