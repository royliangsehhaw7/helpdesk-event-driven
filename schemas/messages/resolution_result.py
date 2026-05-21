from pydantic import BaseModel

class ResolutionResultMessage(BaseModel):
    triggered_by: str  # "resolution_agent"
    timestamp: str