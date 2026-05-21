from pydantic import BaseModel

class ComplaintResultMessage(BaseModel):
    triggered_by: str 
    timestamp: str