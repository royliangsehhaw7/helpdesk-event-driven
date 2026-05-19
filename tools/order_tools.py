import json
from pydantic_ai import RunContext
from core.deps import Deps


async def get_order_summary(ctx: RunContext[Deps]) -> str:
    """Retrieve order header — status, order date, days since purchase.
    Returns error if order not found or does not belong to this customer."""
    row = await ctx.deps.repo.order.get_order(
        ctx.deps.order_id, ctx.deps.customer_id
    )
    if not row:
        return json.dumps({
            "error": f"Order {ctx.deps.order_id} not found for this customer"
        })
    
    return json.dumps(row)


async def get_order_line_items(ctx: RunContext[Deps]) -> str:
    """Retrieve all products in the order with category and warranty info."""
    rows = await ctx.deps.repo.order.get_details(ctx.deps.order_id)

    return json.dumps(rows)


async def get_order_total(ctx: RunContext[Deps]) -> str:
    """Retrieve the total monetary value of the order."""
    total = await ctx.deps.repo.order.get_total(ctx.deps.order_id)

    return json.dumps({"order_total": total})


async def get_customer_order_count(ctx: RunContext[Deps]) -> str:
    """Retrieve the total number of orders this customer has placed."""
    count = await ctx.deps.repo.order.get_customer_order_count(ctx.deps.customer_id)

    return json.dumps({"total_orders": count})