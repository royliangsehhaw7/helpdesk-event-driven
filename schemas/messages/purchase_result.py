from pydantic import BaseModel

class PurchaseResultMessage(BaseModel):
    triggered_by: str  # "resolution_agent"
    timestamp: str
