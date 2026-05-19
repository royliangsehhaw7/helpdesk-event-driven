from pydantic import BaseModel

class ProfileResultContract(BaseModel):
    message_id: str
    customer_id: str
    tier: str
    total_orders: int
    previous_complaints: int
    is_repeat_issue: bool
    sentiment_score: float = 0.0      # updated by SentimentAgent
    sentiment_label: str = "neutral"  # updated by SentimentAgent