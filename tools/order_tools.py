from pydantic_ai import RunContext
from core.deps import Deps


async def get_order_summary(ctx: RunContext[Deps]) -> dict:
    """Retrieve order header — status, order date, days since purchase.
    Returns an error dict if the order is not found or does not belong to this customer."""
    row = await ctx.deps.repos.order.get_order(
        ctx.deps.order_id, ctx.deps.customer_id
    )
    if not row:
        return {"error": f"Order {ctx.deps.order_id} not found for this customer"}
    
    return row


async def get_order_line_items(ctx: RunContext[Deps]) -> list[dict]:
    """Retrieve all products in the order with category and warranty info."""
    rows = await ctx.deps.repos.order.get_details(ctx.deps.order_id)
    return rows


async def get_order_total(ctx: RunContext[Deps]) -> dict:
    """Retrieve the total monetary value of the order."""
    total = await ctx.deps.repos.order.get_total(ctx.deps.order_id)
    return {"order_total": total}


async def get_customer_order_count(ctx: RunContext[Deps]) -> dict:
    """Retrieve the total number of orders this customer has placed."""
    count = await ctx.deps.repos.order.get_customer_order_count(ctx.deps.customer_id)
    return {"total_orders": count}