"""
tests/db/test_integration.py

Integration tests for tools/order_tools.py against a live MySQL database.
Seed data must be loaded (schema.sql) before running.
Tests are read-only — no INSERT, UPDATE, or DELETE.

Run all:
    pytest tests/db/test_integration.py -v

Run one:
    pytest tests/db/test_integration.py::TestGetOrderSummary::test_returns_correct_order_id -v
"""

import json
import pytest
import pytest_asyncio
from unittest.mock import MagicMock
from dotenv import load_dotenv

load_dotenv()

from db.connection import Database
from db.repositories.order_repo import OrderRepository
from tools.order_tools import (
    get_order_summary,
    get_order_line_items,
    get_order_total,
    get_customer_order_count,
)

# ---------------------------------------------------------------------------
# Known seed values — adjust if your seed data differs
# ---------------------------------------------------------------------------

CUSTOMER_ID       = "C001"
ORDER_ID          = "ORD-1001"
ORDER_STATUS      = "delivered"
EXPECTED_TOTAL    = 149.90
WRONG_ORDER_ID    = "ORD-9999"
WRONG_CUSTOMER_ID = "C999"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="function")
async def init_db():
    """Initialise the singleton pool once. Repositories call Database.get_pool()
    internally — no pool is passed around."""
    await Database.get_pool()
    yield
    await Database.close()

@pytest.fixture(scope="function")
def repo():
    return OrderRepository()


def make_ctx(repo, order_id=ORDER_ID, customer_id=CUSTOMER_ID):
    repo_facade = MagicMock()
    repo_facade.order = repo

    deps = MagicMock()
    deps.order_id    = order_id
    deps.customer_id = customer_id
    deps.repo        = repo_facade

    ctx = MagicMock()
    ctx.deps = deps
    return ctx


# ============================ A S S E R T I O N S ============================ #
# ---------------------------------------------------------------------------
# get_order_summary
# ---------------------------------------------------------------------------
class TestGetOrderSummary:

    @pytest.mark.asyncio
    async def test_returns_correct_order_id(self, init_db, repo):
        ctx = make_ctx(repo)
        parsed = json.loads(await get_order_summary(ctx))
        assert parsed["order_id"] == ORDER_ID

    @pytest.mark.asyncio
    async def test_returns_correct_customer_id(self, init_db, repo):
        ctx = make_ctx(repo)
        parsed = json.loads(await get_order_summary(ctx))
        assert parsed["customer_id"] == CUSTOMER_ID

    @pytest.mark.asyncio
    async def test_status_is_delivered(self, init_db, repo):
        ctx = make_ctx(repo)
        parsed = json.loads(await get_order_summary(ctx))
        assert parsed["status"] == ORDER_STATUS

    @pytest.mark.asyncio
    async def test_days_since_purchase_is_non_negative_int(self, init_db, repo):
        ctx = make_ctx(repo)
        parsed = json.loads(await get_order_summary(ctx))
        assert isinstance(parsed["days_since_purchase"], int)
        assert parsed["days_since_purchase"] >= 0

    @pytest.mark.asyncio
    async def test_order_date_format_is_yyyy_mm_dd(self, init_db, repo):
        import re
        ctx = make_ctx(repo)
        parsed = json.loads(await get_order_summary(ctx))
        assert re.match(r"\d{4}-\d{2}-\d{2}", parsed["order_date"])

    @pytest.mark.asyncio
    async def test_non_existent_order_returns_error(self, init_db, repo):
        ctx = make_ctx(repo, order_id=WRONG_ORDER_ID)
        parsed = json.loads(await get_order_summary(ctx))
        assert "error" in parsed
        assert WRONG_ORDER_ID in parsed["error"]

    @pytest.mark.asyncio
    async def test_wrong_customer_returns_error(self, init_db, repo):
        ctx = make_ctx(repo, customer_id=WRONG_CUSTOMER_ID)
        parsed = json.loads(await get_order_summary(ctx))
        assert "error" in parsed


# ---------------------------------------------------------------------------
# get_order_line_items
# ---------------------------------------------------------------------------
class TestGetOrderLineItems:

    @pytest.mark.asyncio
    async def test_returns_a_list(self, init_db, repo):
        ctx = make_ctx(repo)
        parsed = json.loads(await get_order_line_items(ctx))
        assert isinstance(parsed, list)

    @pytest.mark.asyncio
    async def test_order_has_at_least_one_line_item(self, init_db, repo):
        ctx = make_ctx(repo)
        parsed = json.loads(await get_order_line_items(ctx))
        assert len(parsed) >= 1

    @pytest.mark.asyncio
    async def test_line_item_has_required_fields(self, init_db, repo):
        ctx = make_ctx(repo)
        parsed = json.loads(await get_order_line_items(ctx))
        item = parsed[0]
        for field in (
            "product_id", "product_name", "category",
            "quantity", "unit_price", "total_price", "warranty_months",
        ):
            assert field in item, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_unit_price_is_positive(self, init_db, repo):
        ctx = make_ctx(repo)
        parsed = json.loads(await get_order_line_items(ctx))
        for item in parsed:
            assert float(item["unit_price"]) > 0

    @pytest.mark.asyncio
    async def test_quantity_is_positive_int(self, init_db, repo):
        ctx = make_ctx(repo)
        parsed = json.loads(await get_order_line_items(ctx))
        for item in parsed:
            assert isinstance(item["quantity"], int)
            assert item["quantity"] > 0

    @pytest.mark.asyncio
    async def test_non_existent_order_returns_empty_list(self, init_db, repo):
        ctx = make_ctx(repo, order_id=WRONG_ORDER_ID)
        parsed = json.loads(await get_order_line_items(ctx))
        assert parsed == []


# ---------------------------------------------------------------------------
# get_order_total
# ---------------------------------------------------------------------------
class TestGetOrderTotal:

    @pytest.mark.asyncio
    async def test_returns_order_total_key(self, init_db, repo):
        ctx = make_ctx(repo)
        parsed = json.loads(await get_order_total(ctx))
        assert "order_total" in parsed

    @pytest.mark.asyncio
    async def test_total_matches_expected_seed_value(self, init_db, repo):
        ctx = make_ctx(repo)
        parsed = json.loads(await get_order_total(ctx))
        assert parsed["order_total"] == pytest.approx(EXPECTED_TOTAL, abs=0.01)

    @pytest.mark.asyncio
    async def test_total_is_numeric(self, init_db, repo):
        ctx = make_ctx(repo)
        parsed = json.loads(await get_order_total(ctx))
        assert isinstance(parsed["order_total"], (int, float))

    @pytest.mark.asyncio
    async def test_non_existent_order_returns_zero(self, init_db, repo):
        ctx = make_ctx(repo, order_id=WRONG_ORDER_ID)
        parsed = json.loads(await get_order_total(ctx))
        assert parsed["order_total"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# get_customer_order_count
# ---------------------------------------------------------------------------
class TestGetCustomerOrderCount:

    @pytest.mark.asyncio
    async def test_returns_total_orders_key(self, init_db, repo):
        ctx = make_ctx(repo)
        parsed = json.loads(await get_customer_order_count(ctx))
        assert "total_orders" in parsed

    @pytest.mark.asyncio
    async def test_count_is_positive_int(self, init_db, repo):
        ctx = make_ctx(repo)
        parsed = json.loads(await get_customer_order_count(ctx))
        assert isinstance(parsed["total_orders"], int)
        assert parsed["total_orders"] > 0

    @pytest.mark.asyncio
    async def test_unknown_customer_returns_zero(self, init_db, repo):
        ctx = make_ctx(repo, customer_id=WRONG_CUSTOMER_ID)
        parsed = json.loads(await get_customer_order_count(ctx))
        assert parsed["total_orders"] == 0



# ========================== S I M U L A T I O N S ============================ #

# ---------------------------------------------------------------------------
# get_customer_order_count
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_order_summary(init_db, repo):
    ctx = make_ctx(repo)
    result = json.loads(await get_order_summary(ctx))
    print(json.dumps(result, indent=2))


# ---------------------------------------------------------------------------
# get_customer_order_count
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_order_line_items(init_db, repo):
    ctx = make_ctx(repo)
    result = json.loads(await get_order_line_items(ctx))
    print(json.dumps(result, indent=2))


# ---------------------------------------------------------------------------
# get_customer_order_count
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_order_total(init_db, repo):
    ctx = make_ctx(repo)
    result = json.loads(await get_order_total(ctx))
    print(json.dumps(result, indent=2))


@pytest.mark.asyncio
async def test_get_customer_order_count(init_db, repo):
    ctx = make_ctx(repo)
    result = json.loads(await get_customer_order_count(ctx))
    print(json.dumps(result, indent=2))