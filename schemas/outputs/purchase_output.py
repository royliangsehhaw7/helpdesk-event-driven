from pydantic import BaseModel

class PurchaseOutput(BaseModel):
    """What the LLM must produce. Shaped for the model's reasoning."""
    order_id: str
    verified: bool
    total_amount: float
    purchase_date: str
    within_return_window: bool