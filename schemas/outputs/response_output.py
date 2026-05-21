from pydantic import BaseModel

class ResponseOutput(BaseModel):
    response: str
    actions_taken: list[str]
    resolved: bool