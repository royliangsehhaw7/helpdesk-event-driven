from dataclasses import dataclass
from typing import Any

@dataclass
class AgentParam:
    message: Any
    deps: Any