from pydantic import BaseModel

class CustomerResponseMessage(BaseModel):
    triggered_by: str  # "resolution_agent"
    timestamp: str