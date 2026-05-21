from dataclasses import dataclass
from db.connection import Database

@dataclass
class OrderRepository:

    async def get_order(self, order_id: str, customer_id: str) -> dict | None:
        """Fetch order header. Verifies ownership — requires matching customer_id."""
        pool = await Database.get_pool()

        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(f"""
                    select 
                        order_id, customer_id, status, DATE_FORMAT(order_date, '%%Y-%%m-%%d') AS order_date,
                    DATEDIFF(CURDATE(), order_date) AS days_since_purchase
                    from orders
                    where order_id = %s AND customer_id = %s
                    """, (order_id, customer_id)
                )
                return await cur.fetchone()

    async def get_details(self, order_id: str) -> list[dict]:
        """All line items for an order including product name and category."""
        pool = await Database.get_pool()

        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(f"""
                    select 
                        od.product_id, od.quantity, od.unit_price, od.total_price,
                        p.name AS product_name, p.category, p.warranty_months
                    from order_details od
                    join products p ON p.id = od.product_id
                    where od.order_id = %s
                    """, (order_id,)
                )
                return await cur.fetchall()

    async def get_total(self, order_id: str) -> float:
        """Sum of all line item totals for an order."""
        pool = await Database.get_pool()

        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(f"""
                    select 
                        COALESCE(SUM(total_price), 0.00)
                    from order_details 
                    where order_id = %s
                    """, (order_id,)
                )
                row = await cur.fetchone()
                return float(list(row.values())[0]) if row else 0.00

    async def get_customer_order_count(self, customer_id: str) -> int:
        """Total number of orders placed by this customer."""
        pool = await Database.get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT COUNT(*) AS total FROM orders WHERE customer_id = %s",
                    (customer_id,)
                )
                row = await cur.fetchone()
                return int(row['total']) if row else 0