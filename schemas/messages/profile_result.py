from pydantic import BaseModel

class ProfileResultMessage(BaseModel):
    triggered_by: str  # "resolution_agent"
    timestamp: str