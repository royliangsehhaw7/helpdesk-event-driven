from pydantic_ai import RunContext
from core.deps import Deps

async def get_recent_complaints(ctx: RunContext[Deps]) -> list[dict]:
    """Retrieve the 10 most recent complaints for this customer."""
    rows = await ctx.deps.repos.complaint.get_history(ctx.deps.customer_id, limit=10)
    return rows

async def get_complaint_count(ctx: RunContext[Deps]) -> dict:
    """Retrieve the total complaint count for this customer."""
    count = await ctx.deps.repos.complaint.get_count(ctx.deps.customer_id)
    return {"previous_complaints": count}