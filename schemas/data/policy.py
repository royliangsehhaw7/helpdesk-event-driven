from pydantic import BaseModel

class Policy(BaseModel):
    refund_window_days: int
    vip_extended_refund_days: int
    premium_extended_refund_days: int
    complaint_escalation_threshold: int
    replacement_eligible_categories: list[str]
    auto_refund_complaint_types: list[str]