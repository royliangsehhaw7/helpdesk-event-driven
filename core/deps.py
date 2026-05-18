from dataclasses import dataclass
from db.repositories.facade_repo import FacadeRepos
from schemas.data.policy import Policy

@dataclass
class Deps:
    """Shared dependencies injected into every agent run via RunContext.

    db           — async MySQL wrapper. Agents query through tools only.
    bus          — event bus. Agents publish findings through it.
    findings     — accumulates agent outputs for this request.
    policy       — single policy config object, loaded once at startup.
    message_id,
    customer_id,
    order_id     — request identifiers for this activation.
    total_tokens — accumulated across all LLM agent calls for this request.
    """

    bus:          EventBus
    repos:        FacadeRepos
    findings:     Findings
    policy:       Policy
    message_id:   str
    customer_id:  str
    order_id:     str
    total_tokens: int = 0