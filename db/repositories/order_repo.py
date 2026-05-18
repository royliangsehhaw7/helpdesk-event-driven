from dataclasses import dataclass
from db.connection import Database

@dataclass
class OrderRepository:

    async def get(self, id: str) -> dict | None:
        pool = await Database.get_pool()
        
        # 2. Explicitly acquire a connection
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """
                    select 
                        order_id, customer_id, status, 
                        DATE_FORMAT(order_date, '%%Y-%%m-%%d') as order_date, 
                        DATEDIFF(CURDATE(), order_date) as days_since_purchase 
                    from orders 
                    where order_id = %s
                """
                await cur.execute(sql, (id))

                return await cur.fetchone()
            

    async def get_order_details(self, order_id: str) -> list[dict]:
        pool = await Database.get_pool()

        """Fetch all line items for an order including product info."""
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """
                    select 
                        od.product_id, od.quantity, od.unit_price, od.total_price, 
                        p.name AS product_name, p.category, p.warranty_months 
                    from order_details od 
                    join products p ON p.id = od.product_id 
                    where od.order_id = %s    
                """
                await cur.execute(sql, (order_id,))

                return await cur.fetchall()
            

    async def get_order_total(self, order_id: str) -> float:
        pool = await Database.get_pool()

        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """
                    select 
                        COALESCE(SUM(total_price), 0.00)
                    from order_details 
                    where order_id = %s
                """
                await cur.execute(sql, (order_id,))
                row = await cur.fetchone()

                return float(row[0]) if row else 0.00
            
            
    async def get_customer_order_count(self, customer_id: str) -> int:
        pool = await Database.get_pool()

        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """
                    select
                        COUNT(*) FROM orders 
                    where customer_id = %s"
                """
                await cur.execute(sql, (customer_id,))
                row = await cur.fetchone()

                return int(row[0]) if row else 0
