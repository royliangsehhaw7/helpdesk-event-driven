from pydantic import BaseModel

class RefundResultContract(BaseModel):
    message_id: str
    eligible: bool
    reason: str
    refund_amount: float | None = None
    extended_due_to_tier: bool = False