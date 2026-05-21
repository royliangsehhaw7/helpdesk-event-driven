from dataclasses import dataclass
from db.connection import Database

@dataclass
class ComplaintRepository:

    async def get_history(self, customer_id: str, limit: int = 10) -> dict | None:
        pool = await Database.get_pool()
        
        async with pool.acquire() as conn:
           async with conn.cursor() as cur:
                sql = """
                    select 
                        complaint_id, order_id, DATE_FORMAT(date, '%%Y-%%m-%%d') AS date,
                        type, resolution
                    from complaint_history
                    where customer_id = %s
                    order by date DESC LIMIT %s
                """
                await cur.execute(sql, (customer_id, limit))

                return await cur.fetchall()
           

    async def get_count(self, customer_id: str) -> int:
        pool = await Database.get_pool()

        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """
                    select 
                        COUNT(*) as total_count FROM complaint_history 
                    where customer_id = %s
                """
                await cur.execute(sql, (customer_id,))
                row = await cur.fetchone()

                if not row:
                    return 0
                
                # 2. Extract the string key natively from the dictionary
                return int(row["total_count"])