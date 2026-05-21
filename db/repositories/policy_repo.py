from dataclasses import dataclass

from db.connection import Database

class PolicyRepository:
        
    async def get_policy(self) -> dict | None:
        pool = await Database.get_pool()
        
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sql = """
                    SELECT 
                        refund_window_days,
                        vip_extended_refund_days,
                        premium_extended_refund_days,
                        complaint_escalation_threshold
                    FROM policy 
                    WHERE id = 1
                """
                await cur.execute(sql)
                row = await cur.fetchone()

                if not row:
                    return None

                # Inject the static configurations directly into the raw dictionary
                row['replacement_eligible_categories'] = ["electronics", "kitchenware"]
                row['auto_refund_complaint_types'] = ["packaging", "billing"]

                return row