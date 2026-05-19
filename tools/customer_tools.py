
import json
from pydantic_ai import RunContext
from core.deps import Deps

async def get_customer_profile(ctx: RunContext[Deps]) -> str:
    """Retrieve customer name, tier, and join date."""
    row = await ctx.deps.repo.customer.get_by_id(ctx.deps.customer_id)
    
    return json.dumps(row if row else {"error": "Customer not found"})