from typing import Any, AsyncIterable, ClassVar, Dict, List, Literal

from pydantic import BaseModel

from app.agent.manus import Manus


class ResponseFormat(BaseModel):
    """以此格式回复用户。"""

    status: Literal["input_required", "completed", "error"] = "input_required"
    message: str


class A2AManus(Manus):
    async def invoke(self, query, sessionId) -> str:
        config = {"configurable": {"thread_id": sessionId}}
        response = await self.run(query)
        return self.get_agent_response(config, response)

    async def stream(self, query: str) -> AsyncIterable[Dict[str, Any]]:
        """Manus 不支持流式传输。"""
        raise NotImplementedError("Manus 尚不支持流式传输。")

    def get_agent_response(self, config, agent_response):
        return {
            "is_task_complete": True,
            "require_user_input": False,
            "content": agent_response,
        }

    SUPPORTED_CONTENT_TYPES: ClassVar[List[str]] = ["text", "text/plain"]
