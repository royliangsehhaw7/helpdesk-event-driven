from abc import ABC, abstractmethod
from pydantic_ai import Agent

from core.deps import Deps
from core.message_hub import MessageHub


class BaseAgent(ABC):
    def __init__(self, name: str, agent: Agent | None):
        self._name = name
        self._agent = agent

    @property
    def name(self) -> str:
        return self._name

    @abstractmethod
    def subscribe(self, hub: MessageHub, deps: Deps) -> None: 
        ...

    @abstractmethod
    def get_instruction(self) -> str: 
        ...