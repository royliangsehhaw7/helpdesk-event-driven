from pydantic import BaseModel

class PurchaseOutput(BaseModel):
    verified: bool
    order_date: str | None = None
    product_name: str | None = None
    product_category: str | None = None
    days_since_purchase: int | None = None
    order_total: float | None = None
    reason: str | None = None