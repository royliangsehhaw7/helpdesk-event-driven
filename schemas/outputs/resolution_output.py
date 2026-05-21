from pydantic import BaseModel

class ResolutionOutput(BaseModel):
    options: list[str]
    recommended: str
    escalate_to_human: bool = False
    escalation_reason: str | None = None