from pydantic import BaseModel

class RefundOutput(BaseModel):
    eligible: bool
    reason: str
    refund_amount: float | None = None
    extended_due_to_tier: bool = False