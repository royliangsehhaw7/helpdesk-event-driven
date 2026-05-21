from pydantic import BaseModel

class ProfileOutput(BaseModel):
    customer_id: str
    tier: str
    total_orders: int
    previous_complaints: int
    is_repeat_issue: bool
    sentiment_score: float = 0.0
    sentiment_label: str = "neutral"