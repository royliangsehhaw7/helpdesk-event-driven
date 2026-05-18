from dataclasses import dataclass
from db.connection import Database

@dataclass
class PolicyRepository:
        
    async def get_policy(self) -> dict | None:
        pool = await Database.get_pool()
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """
                    select * 
                    from policy 
                    where id = 1
                """
                await cur.execute(sql)

                return await cur.fetchone()
