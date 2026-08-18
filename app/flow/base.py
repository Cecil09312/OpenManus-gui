from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Union

from pydantic import BaseModel

from app.agent.base import BaseAgent


class BaseFlow(BaseModel, ABC):
    """支持多智能体的执行流程基类"""

    agents: Dict[str, BaseAgent]
    tools: Optional[List] = None
    primary_agent_key: Optional[str] = None

    class Config:
        arbitrary_types_allowed = True

    def __init__(
        self, agents: Union[BaseAgent, List[BaseAgent], Dict[str, BaseAgent]], **data
    ):
        # 处理提供智能体的不同方式
        if isinstance(agents, BaseAgent):
            agents_dict = {"default": agents}
        elif isinstance(agents, list):
            agents_dict = {f"agent_{i}": agent for i, agent in enumerate(agents)}
        else:
            agents_dict = agents

        # 如果未指定主智能体，则使用第一个智能体
        primary_key = data.get("primary_agent_key")
        if not primary_key and agents_dict:
            primary_key = next(iter(agents_dict))
            data["primary_agent_key"] = primary_key

        # 设置智能体字典
        data["agents"] = agents_dict

        # 使用 BaseModel 的初始化方法
        super().__init__(**data)

    @property
    def primary_agent(self) -> Optional[BaseAgent]:
        """获取流程的主智能体"""
        return self.agents.get(self.primary_agent_key)

    def get_agent(self, key: str) -> Optional[BaseAgent]:
        """通过键获取特定智能体"""
        return self.agents.get(key)

    def add_agent(self, key: str, agent: BaseAgent) -> None:
        """向流程中添加新智能体"""
        self.agents[key] = agent

    @abstractmethod
    async def execute(self, input_text: str) -> str:
        """使用给定输入执行流程"""
