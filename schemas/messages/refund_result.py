from pydantic import BaseModel

class RefundResultMessage(BaseModel):
    triggered_by: str  # "resolution_agent"
    timestamp: str