from pydantic import BaseModel

class CustomerMessageContract(BaseModel):
    message_id: str    # uuid — ties all findings for this request together
    customer_id: str
    order_id: str
    message: str
    timestamp: str