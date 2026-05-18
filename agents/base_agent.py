from abc import ABC, abstractmethod
from pydantic_ai import Agent
from bus.event_bus import EventBus
from core.deps import Deps


class BaseAgent(ABC):
    def __init__(self, name: str, agent: Agent | None):
        self._name = name
        self._agent = agent

    @property
    def name(self) -> str:
        return self._name

    @abstractmethod
    def subscribe(self, bus: EventBus, deps: Deps) -> None: ...

    @abstractmethod
    def get_instruction(self) -> str: ...