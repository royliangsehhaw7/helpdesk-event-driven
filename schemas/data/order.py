from pydantic import BaseModel

class SalesOrder(BaseModel):
    order_id: str
    customer_id: str
    order_date: str
    status: str
    days_since_purchase: int

class OrderDetail(BaseModel):
    product_id: str
    quantity: int
    unit_price: float
    total_price: float
    product_name: str
    category: str
    warranty_months: int
