import logging
from typing import Awaitable, Callable

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    InvalidParamsError,
    Part,
    Task,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils import completed_task, new_artifact
from a2a.utils.errors import ServerError

from .agent import A2AManus


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ManusExecutor(AgentExecutor):
    """Manus 智能体执行器示例。"""

    def __init__(self, agent_factory: Callable[[], Awaitable[A2AManus]]):
        self.agent_factory = agent_factory

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        error = self._validate_request(context)
        if error:
            raise ServerError(error=InvalidParamsError())

        query = context.get_user_input()
        try:
            self.agent = await self.agent_factory()
            result = await self.agent.invoke(query, context.context_id)
            print(f"最终结果 ===> {result}")
        except Exception as e:
            print("调用智能体出错：%s", e)
            raise ServerError(error=ValueError(f"调用智能体出错：{e}")) from e
        parts = [
            Part(
                root=TextPart(
                    text=(
                        result["content"]
                        if result["content"]
                        else "生成回复失败"
                    )
                ),
            )
        ]
        event_queue.enqueue_event(
            completed_task(
                context.task_id,
                context.context_id,
                [new_artifact(parts, f"task_{context.task_id}")],
                [context.message],
            )
        )

    def _validate_request(self, context: RequestContext) -> bool:
        return False

    async def cancel(
        self, request: RequestContext, event_queue: EventQueue
    ) -> Task | None:
        raise ServerError(error=UnsupportedOperationError())
