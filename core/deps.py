from dataclasses import dataclass

from schemas.policy import Policy
from .blackboard import Blackboard
from .message_hub import MessageHub
from db.repositories.facade_repo import FacadeRepos

@dataclass
class Deps:
    """Shared dependencies injected into every agent run via RunContext.

    db           — async MySQL wrapper. Agents query through tools only.
    hub          — event (message) hub. Agents publish findings through it.
    board        — accumulates agent outputs for this request.
    policy       — single policy config object, loaded once at startup.
    message_id,
    customer_id,
    order_id     — request identifiers for this activation.
    total_tokens — accumulated across all LLM agent calls for this request.
    """

    hub:          MessageHub
    repos:        FacadeRepos
    board:        Blackboard
    policy:       Policy
    message_id:   str
    customer_id:  str
    order_id:     str
    total_tokens: int = 0