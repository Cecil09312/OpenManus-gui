from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from app.llm import LLM
from app.logger import logger
from app.sandbox.client import SANDBOX_CLIENT
from app.schema import ROLE_TYPE, AgentState, Memory, Message


class BaseAgent(BaseModel, ABC):
    """管理智能体状态和执行的抽象基类。

    提供状态转换、内存管理和基于步骤的执行循环的基础功能。
    子类必须实现 `step` 方法。
    """

    # 核心属性
    name: str = Field(..., description="智能体的唯一名称")
    description: Optional[str] = Field(None, description="可选的智能体描述")

    # 提示词
    system_prompt: Optional[str] = Field(None, description="系统级指令提示词")
    next_step_prompt: Optional[str] = Field(None, description="确定下一步操作的提示词")

    # 依赖项
    llm: LLM = Field(default_factory=LLM, description="语言模型实例")
    memory: Memory = Field(default_factory=Memory, description="智能体的记忆存储")
    state: AgentState = Field(default=AgentState.IDLE, description="当前智能体状态")

    # 执行控制
    max_steps: int = Field(default=10, description="终止前的最大步骤数")
    current_step: int = Field(default=0, description="当前执行步骤")

    duplicate_threshold: int = 2

    class Config:
        arbitrary_types_allowed = True
        extra = "allow"  # 允许额外字段以提高子类的灵活性

    @model_validator(mode="after")
    def initialize_agent(self) -> "BaseAgent":
        """如果未提供，使用默认设置初始化智能体。"""
        if self.llm is None or not isinstance(self.llm, LLM):
            self.llm = LLM(config_name=self.name.lower())
        if not isinstance(self.memory, Memory):
            self.memory = Memory()
        return self

    @asynccontextmanager
    async def state_context(self, new_state: AgentState):
        """安全智能体状态转换的上下文管理器。

        参数：
            new_state: 在上下文期间要转换到的状态。

        生成：
            None: 允许在新状态下执行。

        异常：
            ValueError: 如果 new_state 无效。
        """
        if not isinstance(new_state, AgentState):
            raise ValueError(f"无效状态：{new_state}")

        previous_state = self.state
        self.state = new_state
        try:
            yield
        except Exception as e:
            self.state = AgentState.ERROR  # 发生错误时转换为 ERROR 状态
            raise e
        finally:
            self.state = previous_state  # 恢复到之前的状态

    def update_memory(
        self,
        role: ROLE_TYPE,  # type: ignore
        content: str,
        base64_image: Optional[str] = None,
        **kwargs,
    ) -> None:
        """向智能体的记忆中添加消息。

        参数：
            role: 消息发送者的角色（user、system、assistant、tool）。
            content: 消息内容。
            base64_image: 可选的 base64 编码图片。
            **kwargs: 额外参数（例如，tool 消息的 tool_call_id）。

        异常：
            ValueError: 如果角色不受支持。
        """
        message_map = {
            "user": Message.user_message,
            "system": Message.system_message,
            "assistant": Message.assistant_message,
            "tool": lambda content, **kw: Message.tool_message(content, **kw),
        }

        if role not in message_map:
            raise ValueError(f"不支持的消息角色：{role}")

        # 根据角色创建带有适当参数的消息
        kwargs = {"base64_image": base64_image, **(kwargs if role == "tool" else {})}
        self.memory.add_message(message_map[role](content, **kwargs))

    async def run(self, request: Optional[str] = None) -> str:
        """异步执行智能体的主循环。

        参数：
            request: 可选的初始用户请求。

        返回：
            总结执行结果的字符串。

        异常：
            RuntimeError: 如果智能体在启动时不处于 IDLE 状态。
        """
        if self.state != AgentState.IDLE:
            raise RuntimeError(f"无法从状态 {self.state} 运行智能体")

        if request:
            self.update_memory("user", request)

        results: List[str] = []
        async with self.state_context(AgentState.RUNNING):
            while (
                self.current_step < self.max_steps and self.state != AgentState.FINISHED
            ):
                self.current_step += 1
                logger.info(f"执行步骤 {self.current_step}/{self.max_steps}")
                step_result = await self.step()

                # 检查是否陷入循环状态
                if self.is_stuck():
                    self.handle_stuck_state()

                results.append(f"步骤 {self.current_step}：{step_result}")

            if self.current_step >= self.max_steps:
                self.current_step = 0
                self.state = AgentState.IDLE
                results.append(f"已终止：达到最大步数 ({self.max_steps})")
        await SANDBOX_CLIENT.cleanup()
        return "\n".join(results) if results else "未执行任何步骤"

    @abstractmethod
    async def step(self) -> str:
        """执行智能体工作流中的单个步骤。

        必须由子类实现以定义具体行为。
        """

    def handle_stuck_state(self):
        """通过添加提示来改变策略以处理卡住状态"""
        stuck_prompt = "\
        观察到重复响应。请考虑新的策略，避免重复已经尝试过的无效路径。"
        self.next_step_prompt = f"{stuck_prompt}\n{self.next_step_prompt}"
        logger.warning(f"智能体检测到卡住状态。已添加提示：{stuck_prompt}")

    def is_stuck(self) -> bool:
        """通过检测重复内容检查智能体是否陷入循环"""
        if len(self.memory.messages) < 2:
            return False

        last_message = self.memory.messages[-1]
        if not last_message.content:
            return False

        # 统计相同内容的出现次数
        duplicate_count = sum(
            1
            for msg in reversed(self.memory.messages[:-1])
            if msg.role == "assistant" and msg.content == last_message.content
        )

        return duplicate_count >= self.duplicate_threshold

    @property
    def messages(self) -> List[Message]:
        """从智能体的记忆中获取消息列表。"""
        return self.memory.messages

    @messages.setter
    def messages(self, value: List[Message]):
        """设置智能体记忆中的消息列表。"""
        self.memory.messages = value
