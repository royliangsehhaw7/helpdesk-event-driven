"""
tests/test_order_tools.py

Unit tests for tools/order_tools.py.

Strategy: all four tools receive a RunContext whose `deps` is a mock Deps.
We mock the repo layer — no database, no pydantic-ai Agent — so each test
is fast, isolated, and deterministic.

Run with:
    pytest tests/test_order_tools.py -v
    
    # class::method
pytest tests/test_order_tools.py::TestGetOrderSummary::test_order_not_found_returns_error_dict -v

    # or just the method name with -k (no need to know the class)
pytest tests/test_order_tools.py -k "test_order_not_found_returns_error_dict" -v    
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from tools.order_tools import (
    get_order_summary,
    get_order_line_items,
    get_order_total,
    get_customer_order_count,
)


# ---------------------------------------------------------------------------
# Helpers — build a minimal fake RunContext
# ---------------------------------------------------------------------------

def make_ctx(
    order_id: str = "ORD-1001",
    customer_id: str = "C001",
    order_repo_overrides: dict | None = None,
):
    """Return a mock RunContext[Deps] wired to a fake OrderRepository.

    All repo methods are AsyncMock so they can be awaited inside the tools.
    Pass `order_repo_overrides` to set specific return values, e.g.:
        {"get_order": None}   →   simulates a missing / mismatched order
    """
    # --- fake OrderRepository ---
    order_repo = MagicMock()
    order_repo.get_order = AsyncMock(return_value={
        "order_id": order_id,
        "customer_id": customer_id,
        "order_date": "2024-11-01",
        "status": "delivered",
        "days_since_purchase": 15,
    })
    order_repo.get_details = AsyncMock(return_value=[
        {
            "product_id": "P001",
            "quantity": 1,
            "unit_price": 149.90,
            "total_price": 149.90,
            "product_name": "Mechanical Keyboard",
            "category": "electronics",
            "warranty_months": 12,
        }
    ])
    order_repo.get_total = AsyncMock(return_value=149.90)
    order_repo.get_customer_order_count = AsyncMock(return_value=5)

    # apply any per-test overrides
    if order_repo_overrides:
        for method, value in order_repo_overrides.items():
            getattr(order_repo, method).return_value = value

    # --- fake RepoFacade ---
    repo = MagicMock()
    repo.order = order_repo

    # --- fake Deps ---
    deps = MagicMock()
    deps.order_id = order_id
    deps.customer_id = customer_id
    deps.repo = repo

    # --- fake RunContext ---
    ctx = MagicMock()
    ctx.deps = deps
    return ctx


# ---------------------------------------------------------------------------
# get_order_summary
# ---------------------------------------------------------------------------

class TestGetOrderSummary:

    @pytest.mark.asyncio
    async def test_returns_json_string(self):
        ctx = make_ctx()
        result = await get_order_summary(ctx)
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    @pytest.mark.asyncio
    async def test_happy_path_contains_expected_fields(self):
        ctx = make_ctx()
        parsed = json.loads(await get_order_summary(ctx))

        assert parsed["order_id"] == "ORD-1001"
        assert parsed["customer_id"] == "C001"
        assert parsed["status"] == "delivered"
        assert "days_since_purchase" in parsed

    @pytest.mark.asyncio
    async def test_passes_correct_ids_to_repo(self):
        ctx = make_ctx(order_id="ORD-9999", customer_id="C007")
        await get_order_summary(ctx)

        ctx.deps.repos.order.get_order.assert_awaited_once_with("ORD-9999", "C007")

    @pytest.mark.asyncio
    async def test_order_not_found_returns_error_dict(self):
        ctx = make_ctx(order_repo_overrides={"get_order": None})
        parsed = json.loads(await get_order_summary(ctx))

        assert "error" in parsed
        assert "ORD-1001" in parsed["error"]

    @pytest.mark.asyncio
    async def test_mismatched_customer_returns_error(self):
        """Repo returns None when customer_id doesn't match the order."""
        ctx = make_ctx(
            order_id="ORD-1001",
            customer_id="C999",
            order_repo_overrides={"get_order": None},
        )
        parsed = json.loads(await get_order_summary(ctx))
        assert "error" in parsed


# ---------------------------------------------------------------------------
# get_order_line_items
# ---------------------------------------------------------------------------

class TestGetOrderLineItems:

    @pytest.mark.asyncio
    async def test_returns_json_string(self):
        ctx = make_ctx()
        result = await get_order_line_items(ctx)
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_returns_list(self):
        ctx = make_ctx()
        parsed = json.loads(await get_order_line_items(ctx))
        assert isinstance(parsed, list)

    @pytest.mark.asyncio
    async def test_line_item_has_expected_fields(self):
        ctx = make_ctx()
        parsed = json.loads(await get_order_line_items(ctx))

        assert len(parsed) == 1
        item = parsed[0]
        for field in ("product_id", "product_name", "category",
                      "quantity", "unit_price", "total_price", "warranty_months"):
            assert field in item, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_passes_order_id_to_repo(self):
        ctx = make_ctx(order_id="ORD-4242")
        await get_order_line_items(ctx)
        ctx.deps.repos.order.get_details.assert_awaited_once_with("ORD-4242")

    @pytest.mark.asyncio
    async def test_empty_order_returns_empty_list(self):
        ctx = make_ctx(order_repo_overrides={"get_details": []})
        parsed = json.loads(await get_order_line_items(ctx))
        assert parsed == []

    @pytest.mark.asyncio
    async def test_multiple_line_items(self):
        two_items = [
            {"product_id": "P001", "product_name": "Keyboard",
             "category": "electronics", "quantity": 1,
             "unit_price": 149.90, "total_price": 149.90, "warranty_months": 12},
            {"product_id": "P002", "product_name": "Mouse",
             "category": "electronics", "quantity": 2,
             "unit_price": 49.90, "total_price": 99.80, "warranty_months": 12},
        ]
        ctx = make_ctx(order_repo_overrides={"get_details": two_items})
        parsed = json.loads(await get_order_line_items(ctx))
        assert len(parsed) == 2


# ---------------------------------------------------------------------------
# get_order_total
# ---------------------------------------------------------------------------

class TestGetOrderTotal:

    @pytest.mark.asyncio
    async def test_returns_json_string(self):
        ctx = make_ctx()
        result = await get_order_total(ctx)
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_contains_order_total_key(self):
        ctx = make_ctx()
        parsed = json.loads(await get_order_total(ctx))
        assert "order_total" in parsed

    @pytest.mark.asyncio
    async def test_total_value_matches_repo(self):
        ctx = make_ctx(order_repo_overrides={"get_total": 299.80})
        parsed = json.loads(await get_order_total(ctx))
        assert parsed["order_total"] == pytest.approx(299.80)

    @pytest.mark.asyncio
    async def test_zero_total(self):
        ctx = make_ctx(order_repo_overrides={"get_total": 0.0})
        parsed = json.loads(await get_order_total(ctx))
        assert parsed["order_total"] == 0.0

    @pytest.mark.asyncio
    async def test_passes_order_id_to_repo(self):
        ctx = make_ctx(order_id="ORD-5555")
        await get_order_total(ctx)
        ctx.deps.repos.order.get_total.assert_awaited_once_with("ORD-5555")


# ---------------------------------------------------------------------------
# get_customer_order_count
# ---------------------------------------------------------------------------

class TestGetCustomerOrderCount:

    @pytest.mark.asyncio
    async def test_returns_json_string(self):
        ctx = make_ctx()
        result = await get_customer_order_count(ctx)
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_contains_total_orders_key(self):
        ctx = make_ctx()
        parsed = json.loads(await get_customer_order_count(ctx))
        assert "total_orders" in parsed

    @pytest.mark.asyncio
    async def test_count_matches_repo(self):
        ctx = make_ctx(order_repo_overrides={"get_customer_order_count": 12})
        parsed = json.loads(await get_customer_order_count(ctx))
        assert parsed["total_orders"] == 12

    @pytest.mark.asyncio
    async def test_first_time_customer_returns_zero(self):
        ctx = make_ctx(order_repo_overrides={"get_customer_order_count": 0})
        parsed = json.loads(await get_customer_order_count(ctx))
        assert parsed["total_orders"] == 0

    @pytest.mark.asyncio
    async def test_passes_customer_id_to_repo(self):
        ctx = make_ctx(customer_id="C042")
        await get_customer_order_count(ctx)
        ctx.deps.repos.order.get_customer_order_count.assert_awaited_once_with("C042")