from pydantic import BaseModel

class CustomerResponseContract(BaseModel):
    message_id: str
    response: str
    actions_taken: list[str]
    resolved: bool