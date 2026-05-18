from dataclasses import dataclass
from db.connection import Database

@dataclass
class CustomerRepository:
    async def get(self, id: str) -> dict | None:
        pool = await Database.get_pool()
        
        # 2. Explicitly acquire a connection
        async with pool.acquire() as conn:
            # 3. Explicitly create a cursor
            async with conn.cursor() as cur:
                sql = """
                    select 
                        * 
                    from customers 
                    whwew id=%s
                """
                await cur.execute(sql, (id,))                

                # 5. Fetch and return
                return await cur.fetchone()
