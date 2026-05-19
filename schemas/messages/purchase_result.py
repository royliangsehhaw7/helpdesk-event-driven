from pydantic import BaseModel

class PurchaseResultContract(BaseModel):
    message_id: str
    customer_id: str
    order_id: str
    verified: bool
    order_date: str | None = None
    product_name: str | None = None
    product_category: str | None = None
    days_since_purchase: int | None = None
    order_total: float | None = None
    reason: str | None = None
