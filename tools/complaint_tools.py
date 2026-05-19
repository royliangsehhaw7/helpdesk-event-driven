import json
from pydantic_ai import RunContext
from core.deps import Deps

async def get_recent_complaints(ctx: RunContext[Deps]) -> str:
    """Retrieve the 10 most recent complaints for this customer."""
    rows = await ctx.deps.repo.complaint.get_history(ctx.deps.customer_id, limit=10)

    return json.dumps(rows)

async def get_complaint_count(ctx: RunContext[Deps]) -> str:
    """Retrieve the total complaint count for this customer."""
    count = await ctx.deps.repo.complaint.get_count(ctx.deps.customer_id)

    return json.dumps({"previous_complaints": count})