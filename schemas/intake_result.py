from pydantic import BaseModel, Field

class IntakeResult(BaseModel):
    ready: bool = Field(default=False, description="Flag to proceed once the order reference has been obtained")    

    order_id: str = Field(max_length=12, description="The customer's order reference")
    message: str = Field(description="Summary of customers request or complaint")
