from pydantic import BaseModel

class PurchaseVerifiedEvent(BaseModel):
    message_id: str
    customer_id: str
    order_id: str
    verified: bool
    order_date: str | None = None
    product_name: str | None = None
    product_category: str | None = None
    days_since_purchase: int | None = None
    order_total: float | None = None
    reason: str | None = None

class CustomerProfileEvent(BaseModel):
    message_id: str
    customer_id: str
    tier: str
    total_orders: int
    previous_complaints: int
    is_repeat_issue: bool
    sentiment_score: float = 0.0      # updated by SentimentAgent
    sentiment_label: str = "neutral"  # updated by SentimentAgent

class ComplaintTypeEvent(BaseModel):
    message_id: str
    complaint_type: str               # "packaging"|"product"|"delivery"|"billing"
    severity: str                     # "low"|"medium"|"high"
    keywords: list[str]
    wants_refund: bool
    wants_replacement: bool
    wants_complaint_filed: bool

class RefundEligibilityEvent(BaseModel):
    message_id: str
    eligible: bool
    reason: str
    refund_amount: float | None = None
    extended_due_to_tier: bool = False

class ResolutionOptionsEvent(BaseModel):
    message_id: str
    options: list[str]
    recommended: str
    escalate_to_human: bool = False
    escalation_reason: str | None = None

class CustomerResponseEvent(BaseModel):
    message_id: str
    response: str
    actions_taken: list[str]
    resolved: bool