from pydantic import BaseModel

class ResolutionResultContract(BaseModel):
    message_id: str
    options: list[str]
    recommended: str
    escalate_to_human: bool = False
    escalation_reason: str | None = None