# Multi-Agent Customer Service System
## Specification v3.0 — Observer / Event-Driven Pattern with Tool-Based Data Access

---

## 1. Purpose
A learning-oriented multi-agent system that handles customer service requests end-to-end.
The system runs Phase 1 analysis agents concurrently via `asyncio.gather()`, then runs
Phase 2 synthesis agents sequentially in `main.py` in explicit order. All agent results
are shared through a single `Blackboard` object passed to every agent.

---

## 2. Architecture
### 2.1 Pattern

This system uses explicit imperative orchestration. `handle_customer_message()` in `main.py`
is the single orchestrator. It runs Phase 1 agents concurrently, waits for all of them to
finish, then runs Phase 2 agents one at a time in dependency order.

There is no event bus. There are no subscriptions. There are no closures. The flow is
readable top to bottom in `main.py`.

### 2.2 Blackboard — the shared notepad

All agents share one `Blackboard` object created per request in `main.py`.

- Phase 1 agents write their results into it
- `asyncio.gather()` returns only after all Phase 1 agents have written their results
- Phase 2 agents read from it and write their own results back into it

No results are passed as function parameters between agents. Every agent reads and writes
directly from `board`.

### 2.3 Agents

There are seven agents. `main.py` is the orchestrator.

| Agent | Phase | Reads from findings | Writes to findings |
|---|---|---|---|
| `PurchaseVerificationAgent` | 1 | — | `board.purchase` |
| `CustomerHistoryAgent` | 1 | — | `board.profile` |
| `ComplaintClassificationAgent` | 1 | — | `board.complaint_type` |
| `SentimentAgent` | 1 | `board.profile` | `board.profile.sentiment_*` |
| `RefundEligibilityAgent` | 2 | `board.purchase` + `board.complaint_type` | `board.refund_eligibility` |
| `ResolutionAgent` | 2 | `board.purchase` + `board.profile` + `board.complaint_type` + `board.refund_eligibility` | `board.resolution` |
| `ResponseComposerAgent` | 2 | `board.resolution` + `board.profile` + `board.complaint_type` | `board.response` |


---

## 3. Execution Model

This is the most important section to understand before writing a single line of agent code.
Python's `asyncio` is **single-threaded**. There are no real threads. Parallelism is achieved
by interleaving coroutines at `await` points. Understanding exactly when agents run, when they
yield, and when race conditions are possible determines the entire correctness of the system.

### 3.1 Phase 1 — Concurrent fan-out

`main.py` runs all 4 Phase 1 agents inside one `asyncio.gather()` call:

````python
await asyncio.gather(
    purchase_agent.run(message, findings, deps),
    history_agent.run(message, findings, deps),
    classification_agent.run(message, findings, deps),
    sentiment_agent.run(message, findings, deps),
)
````

Each agent immediately hits `await self._agent.run(...)` which is an HTTP call to the LLM.
All four HTTP requests go out simultaneously. The event loop waits for all four to respond.
`asyncio.gather()` does not return until all four agents have finished and written their
results to `findings`.

### 3.2 Phase 2 — Sequential

After `gather()` returns, `findings` is fully populated by Phase 1. Phase 2 agents run
one at a time in dependency order:

**Key insight**: `await bus.publish(message)` in `main.py` does not return until the entire
cascade — including all Phase 2 agents — has completed. The cascade resolves depth-first,
driven by whichever Phase 1 agent finishes last.

```text
refund_agent     
    └── reads board.purchase + board.complaint_type
    └── writes board.refund_eligibility

resolution_agent 
    └── reads board.refund_eligibility + board.profile + board.complaint_type + board.purchase
    └── writes board.resolution

composer_agent   
    └── reads board.resolution + board.profile + board.complaint_type
    └── writes board.response
```

### 3.3 Complete execution flow
```text
main.py: 
    |
    └──board = Blackboard()
    └── asyncio.gather() ← all 4 start, all 4 HTTP requests in-flight
        └── purchase_agent  → await LLM → board.purchase = result
        └── history_agent   → await LLM → board.profile = result
        └── classif_agent   → await LLM → board.complaint_type = result
        └── sentiment_agent → await LLM → board.profile.sentiment_* = result
    └── gather() returns — board fully written
    |
    └── await refund_agent.run(board, deps)
            → pure python, no LLM
            → board.refund_eligibility = result
    |
    └── await resolution_agent.run(board, deps)
        → await LLM
        → board.resolution = result
    |
    └── await composer_agent.run(board, deps)
        → await LLM
        → board.response = result
    |
    └── return board.response
```

---

## 4. Project Structure
```
customer_service/
├── agents/
│   ├── base_agent.py
│   ├── purchase_verification_agent.py
│   ├── customer_history_agent.py
│   ├── complaint_classification_agent.py
│   ├── sentiment_agent.py
│   ├── refund_eligibility_agent.py
│   ├── resolution_agent.py
│   └── response_composer_agent.py
├── core/
│   ├── deps.py
│   ├── findings.py
│   ├── logger.py
│   └── llm_factory.py
├── db/
│   ├── connection.py
│   ├── schema.sql
│   └── repositories/
│       ├── facade.py
│       ├── customer_repo.py
│       ├── order_repo.py
│       ├── complaint_repo.py
│       └── policy_repo.py
├── schemas/
│   ├── data/
│   │   ├── customer.py
│   │   ├── order.py
│   │   ├── complaint.py
│   │   └── policy.py
│   └── events/
│       ├── inbound.py
│       └── findings.py
├── tools/
│   ├── agent_logger.py
│   ├── customer_tools.py
│   ├── order_tools.py
│   └── complaint_tools.py
└── main.py
```

---

## 5. Database Layer

### 5.1 MySQL Schema

**Location**: `db/schema.sql`

**products**
- id (PK) — varchar(20)
- name — varchar(150)
- category — varchar(50)
- warranty_months — int, default 0

**orders**
- order_id (PK) — varchar(20)
- customer_id (FK → customers.id) — varchar(20)
- order_date — date
- status — pending | delivered | cancelled

**order_details**
- id (PK) — bigint
- order_id (FK → orders.order_id) — varchar(20)
- product_id (FK → products.id) — varchar(20)
- quantity — int, default 1
- unit_price — decimal(10,2)
- total_price — decimal(10,2)

**complaint_history**
- complaint_id (PK) — varchar(20)
- customer_id (FK → customers.id) — varchar(20)
- order_id (FK → orders.order_id) — varchar(20)
- date — date
- type - packaging|product|delivery|billing|resolution|refunded|replaced|rejected|pending

**policy**
Single-row configuration table (id = 1)

- refund_window_days — int
- vip_extended_refund_days — int
- premium_extended_refund_days — int
- complaint_escalation_threshold — int
- replacement_eligible_categories — JSON
- auto_refund_complaint_types — JSON


### 5.2 Database Singleton

**Location**: `db/connection.py`

One pool for the entire application. Lazily initialised on first `get_pool()` call.
Repositories call `Database.get_pool()` directly — no pool is passed around.

```python
import os
import aiomysql


class Database:
    """Singleton async MySQL connection pool.

    _pool is a class-level variable shared by all callers.
    Repositories call Database.get_pool() directly — no injection needed.
    The pool is created once on first call and reused for all subsequent calls.
    Close once at application shutdown via Database.close().
    """
    _pool = None

    @classmethod
    async def get_pool(cls) -> aiomysql.Pool:
        if cls._pool is None:
            cls._pool = await aiomysql.create_pool(
                host=os.getenv('DB_HOST', 'localhost'),
                port=int(os.getenv('DB_PORT', '3306')),
                user=os.getenv('DB_USER', 'root'),
                password=os.getenv('DB_PWD', ''),
                db=os.getenv('DB_NAME', 'customer_service'),
                minsize=2,
                maxsize=10,
                autocommit=True,
                charset='utf8mb4',
                cursorclass=aiomysql.DictCursor,
            )
        return cls._pool

    @classmethod
    async def close(cls) -> None:
        if cls._pool:
            cls._pool.close()
            await cls._pool.wait_closed()
            cls._pool = None
```

### 5.3 Repositories

**Location**: `db/repositories/`

Each repository owns its query responsibility. It calls `Database.get_pool()` on every method
call — the singleton ensures no pool is created more than once. No connection or pool object
is passed to repositories as a constructor argument.

**`db/repositories/customer_repo.py`**

```python
from db.connection import Database


class CustomerRepository:

    async def get_by_id(self, customer_id: str) -> dict | None:
        pool = await Database.get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id, name, email, tier, "
                    "DATE_FORMAT(joined_date, '%%Y-%%m-%%d') AS joined_date "
                    "FROM customers WHERE id = %s",
                    (customer_id,)
                )
                return await cur.fetchone()
```

**`db/repositories/order_repo.py`**

```python
from db.connection import Database


class OrderRepository:

    async def get_order(self, order_id: str, customer_id: str) -> dict | None:
        """Fetch order header. Verifies ownership — requires matching customer_id."""
        pool = await Database.get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT order_id, customer_id, status, "
                    "DATE_FORMAT(order_date, '%%Y-%%m-%%d') AS order_date, "
                    "DATEDIFF(CURDATE(), order_date) AS days_since_purchase "
                    "FROM orders "
                    "WHERE order_id = %s AND customer_id = %s",
                    (order_id, customer_id)
                )
                return await cur.fetchone()

    async def get_details(self, order_id: str) -> list[dict]:
        """All line items for an order including product name and category."""
        pool = await Database.get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT od.product_id, od.quantity, "
                    "od.unit_price, od.total_price, "
                    "p.name AS product_name, p.category, p.warranty_months "
                    "FROM order_details od "
                    "JOIN products p ON p.id = od.product_id "
                    "WHERE od.order_id = %s",
                    (order_id,)
                )
                return await cur.fetchall()

    async def get_total(self, order_id: str) -> float:
        """Sum of all line item totals for an order."""
        pool = await Database.get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT COALESCE(SUM(total_price), 0.00) "
                    "FROM order_details WHERE order_id = %s",
                    (order_id,)
                )
                row = await cur.fetchone()
                return float(list(row.values())[0]) if row else 0.00

    async def get_customer_order_count(self, customer_id: str) -> int:
        """Total number of orders placed by this customer."""
        pool = await Database.get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT COUNT(*) AS total FROM orders WHERE customer_id = %s",
                    (customer_id,)
                )
                row = await cur.fetchone()
                return int(row['total']) if row else 0
```

**`db/repositories/complaint_repo.py`**

```python
from db.connection import Database


class ComplaintRepository:

    async def get_history(self, customer_id: str, limit: int = 10) -> list[dict]:
        """Most recent complaints for a customer. Never loads full history."""
        pool = await Database.get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT complaint_id, order_id, "
                    "DATE_FORMAT(date, '%%Y-%%m-%%d') AS date, "
                    "type, resolution "
                    "FROM complaint_history "
                    "WHERE customer_id = %s "
                    "ORDER BY date DESC LIMIT %s",
                    (customer_id, limit)
                )
                return await cur.fetchall()

    async def get_count(self, customer_id: str) -> int:
        """Total complaint count for a customer."""
        pool = await Database.get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT COUNT(*) AS total FROM complaint_history "
                    "WHERE customer_id = %s",
                    (customer_id,)
                )
                row = await cur.fetchone()
                return int(row['total']) if row else 0
```

**`db/repositories/policy_repo.py`**

```python
import json
from db.connection import Database
from schemas.data.policy import Policy


class PolicyRepository:

    async def get(self) -> Policy:
        """Load the single policy row. Called once at startup."""
        pool = await Database.get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT * FROM policy WHERE id = 1")
                row = await cur.fetchone()
        if not row:
            raise RuntimeError("Policy table is empty — run schema.sql")
        row['replacement_eligible_categories'] = json.loads(
            row['replacement_eligible_categories']
        )
        row['auto_refund_complaint_types'] = json.loads(
            row['auto_refund_complaint_types']
        )
        return Policy.model_validate(row)
```

### 5.4 RepoFacade

**Location**: `db/repositories/facade.py`

Groups all repositories into one injection point for `Deps`. Tools call
`ctx.deps.repo.order.get_order(...)` — consistent, clean, no field proliferation on `Deps`.

```python
from dataclasses import dataclass, field
from db.repositories.customer_repo import CustomerRepository
from db.repositories.order_repo import OrderRepository
from db.repositories.complaint_repo import ComplaintRepository
from db.repositories.policy_repo import PolicyRepository


@dataclass
class RepoFacade:
    """Single injection point for all repositories.

    All repos call Database.get_pool() internally — no pool passed here.
    Instantiate once per application and reuse across all requests.
    """
    customer:  CustomerRepository  = field(default_factory=CustomerRepository)
    order:     OrderRepository     = field(default_factory=OrderRepository)
    complaint: ComplaintRepository = field(default_factory=ComplaintRepository)
    policy:    PolicyRepository    = field(default_factory=PolicyRepository)
```

---

## 6. Schemas

### 6.1 Data Schemas

**`schemas/data/customer.py`**

```python
from pydantic import BaseModel

class Customer(BaseModel):
    id: str
    name: str
    email: str
    tier: str          # "standard" | "premium" | "vip"
    joined_date: str
```

**`schemas/data/order.py`**

```python
from pydantic import BaseModel

class OrderDetail(BaseModel):
    product_id: str
    quantity: int
    unit_price: float
    total_price: float
    product_name: str
    category: str
    warranty_months: int

class SalesOrder(BaseModel):
    order_id: str
    customer_id: str
    order_date: str
    status: str
    days_since_purchase: int
```

**`schemas/data/policy.py`**

```python
from pydantic import BaseModel

class Policy(BaseModel):
    refund_window_days: int
    vip_extended_refund_days: int
    premium_extended_refund_days: int
    complaint_escalation_threshold: int
    replacement_eligible_categories: list[str]
    auto_refund_complaint_types: list[str]
```

### 6.2 Event Schemas

**`schemas/events/inbound.py`**

```python
from pydantic import BaseModel

class CustomerMessageEvent(BaseModel):
    message_id: str    # uuid — ties all findings for this request together
    customer_id: str
    order_id: str
    message: str
    timestamp: str
```

**`schemas/events/findings.py`**

```python
from pydantic import BaseModel

class PurchaseVerifiedEvent(BaseModel):
    message_id: str
    verified: bool
    order_date: str | None = None
    product_name: str | None = None
    product_category: str | None = None
    days_since_purchase: int | None = None
    order_total: float | None = None
    reason: str | None = None

class CustomerProfileEvent(BaseModel):
    message_id: str
    customer_id: str
    tier: str
    total_orders: int
    previous_complaints: int
    is_repeat_issue: bool
    sentiment_score: float = 0.0
    sentiment_label: str = "neutral"

class ComplaintTypeEvent(BaseModel):
    message_id: str
    complaint_type: str        # "packaging"|"product"|"delivery"|"billing"
    severity: str              # "low"|"medium"|"high"
    keywords: list[str]
    wants_refund: bool
    wants_replacement: bool
    wants_complaint_filed: bool

class RefundEligibilityEvent(BaseModel):
    message_id: str
    eligible: bool
    reason: str
    refund_amount: float | None = None
    extended_due_to_tier: bool = False

class ResolutionOptionsEvent(BaseModel):
    message_id: str
    options: list[str]
    recommended: str
    escalate_to_human: bool = False
    escalation_reason: str | None = None

class CustomerResponseEvent(BaseModel):
    message_id: str
    response: str
    actions_taken: list[str]
    resolved: bool
```

---

## 7. Core Infrastructure

### 7.1 Blackboard

**Location**: `core/blackboard.py`

```python
from dataclasses import dataclass
from schemas.events.findings import (
    PurchaseVerifiedEvent, CustomerProfileEvent, ComplaintTypeEvent,
    RefundEligibilityEvent, ResolutionOptionsEvent, CustomerResponseEvent,
)

@dataclass
class Blackboard:
    """Result accumulator for one request lifecycle.

    Phase 1 agents write here after their LLM call completes.
    Phase 2 agents read here to check gate conditions.
    One instance per CustomerMessageEvent. Discarded when complete.
    """
    purchase:           PurchaseVerifiedEvent  | None = None
    profile:            CustomerProfileEvent   | None = None
    complaint_type:     ComplaintTypeEvent     | None = None
    refund_eligibility: RefundEligibilityEvent | None = None
    resolution:         ResolutionOptionsEvent | None = None
    response:           CustomerResponseEvent  | None = None

    def is_complete(self) -> bool:
        return self.response is not None
```

### 7.2 Deps

**Location**: `core/deps.py`

```python
from dataclasses import dataclass
from bus.event_bus import EventBus
from core.blackboard import Blackboard
from db.repositories.facade import RepoFacade
from schemas.data.policy import Policy

@dataclass
class Deps:
    """Injected into every agent run via RunContext.

    repo         — facade grouping all repositories. Tools call ctx.deps.repo.<repo>.<method>().
    bus          — event bus. Agents publish findings through it.
    board        — accumulates agent output events for this request.
    policy       — single policy config, loaded once at startup.
    message_id,
    customer_id,
    order_id     — request identifiers for this activation.
    total_tokens — accumulated LLM token usage across all agents for this request.
    """
    repo:         RepoFacade
    bus:          EventBus
    board:        Blackboard
    policy:       Policy
    message_id:   str
    customer_id:  str
    order_id:     str
    total_tokens: int = 0
```

---

## 8. Tools
### 8.1 Design decision

Tools are the **only** mechanism through which agents access source data. Each tool is an async
function that calls `ctx.deps.repo.<repo>.<method>()` with specific parameters. The LLM decides
which tools to call and when. Every query is targeted — no tool loads more than it needs.

Tools return JSON strings. The LLM reads the string and reasons about the content.

### 8.2 log_decision

**Location**: `tools/agent_logger.py`

Registered on all LLM-powered agents. The LLM calls it once per activation to narrate its
reasoning. Accumulates token usage into `deps.total_tokens`.

```python
import logging
from pydantic_ai import RunContext
from core.deps import Deps

logger = logging.getLogger(__name__)

async def log_decision(ctx: RunContext[Deps], message: str) -> str:
    """Call this once to explain your reasoning before returning your result."""
    usage = ctx.usage
    ctx.deps.total_tokens += usage.total_tokens or 0
    logger.info(
        f"[decision] {message} | "
        f"tokens: request={usage.request_tokens} "
        f"response={usage.response_tokens} "
        f"total={usage.total_tokens}"
    )
    return "logged"
```

### 8.3 Customer tools

**Location**: `tools/customer_tools.py`

```python
import json
from pydantic_ai import RunContext
from core.deps import Deps

async def get_customer_profile(ctx: RunContext[Deps]) -> str:
    """Retrieve customer name, tier, and join date."""
    row = await ctx.deps.repo.customer.get_by_id(ctx.deps.customer_id)
    return json.dumps(row if row else {"error": "Customer not found"})
```

### 8.4 Order tools

**Location**: `tools/order_tools.py`

```python
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
```

### 8.5 Complaint tools

**Location**: `tools/complaint_tools.py`

```python
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
```

### 8.6 Tool registration per agent

| Tool | PurchaseVerification | CustomerHistory | ComplaintClassification | Sentiment | RefundEligibility | Resolution | ResponseComposer |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `log_decision` | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ |
| `get_customer_profile` | — | ✓ | — | — | — | — | ✓ |
| `get_order_summary` | ✓ | — | — | — | — | — | — |
| `get_order_line_items` | ✓ | — | — | — | — | — | — |
| `get_order_total` | ✓ | — | — | — | — | — | — |
| `get_customer_order_count` | — | ✓ | — | — | — | — | — |
| `get_recent_complaints` | — | ✓ | — | — | — | — | — |
| `get_complaint_count` | — | ✓ | — | — | — | — | — |

`RefundEligibilityAgent` is pure Python — no tools, no LLM.

---

## 9. Agents

### 9.1 Design principles

Each agent has one domain responsibility, one subscription set, and one tool set. The LLM calls
tools mid-reasoning to pull exactly the data it needs — never pre-loaded. Each agent posts its
finding to `deps.board` and publishes it to the bus. No agent knows or cares what other
agents exist.

All Phase 2 agents carry an `asyncio.Lock` and a `_fired` flag. See Section 3.4 for the full
explanation of why this is mandatory.

### 9.2 BaseAgent


**Location**: `agents/base_agent.py`

````python
from abc import ABC, abstractmethod
from pydantic_ai import Agent
from core.deps import Deps
from core.blackboard import Blackboard
from schemas.events.inbound import CustomerMessageEvent


class BaseAgent(ABC):

    def __init__(self, name: str, agent: Agent | None):
        self._name  = name
        self._agent = agent

    @property
    def name(self) -> str:
        return self._name

    @abstractmethod
    async def run(self, *args, **kwargs) -> None: ...

    @abstractmethod
    def get_instruction(self) -> str: ...
````

---

### 9.3 PurchaseVerificationAgent
**Location**: `agents/purchase_verification_agent.py`

- **Reads**: `message.order_id`, `message.customer_id`
- **Writes**: `board.purchase`
- **Tools**: `get_order_summary`, `get_order_line_items`, `get_order_total`, `log_decision`

````python
class PurchaseVerificationAgent(BaseAgent):

    def get_instruction(self) -> str:
        return """
            You are the PurchaseVerificationAgent in a customer service system.
            Your sole job is to verify the customer's purchase claim.

            Use your tools to:
            - Retrieve the order header (get_order_summary)
            - Retrieve line items to identify what was purchased (get_order_line_items)
            - Retrieve the order total value (get_order_total)

            Determine:
            - Is the order found and does it belong to this customer?
            - Is the status "delivered"?
            - How many days since purchase?
            - What product and category?

            Set verified=False with a clear reason if order not found,
            not belonging to this customer, or not "delivered".

            Call log_decision once. Return a PurchaseVerifiedEvent.
        """

    async def run(self, message: CustomerMessageEvent, board: Blackboard, deps: Deps) -> None:
        result = await self._agent.run(
            f"Verify purchase for order {message.order_id} "
            f"by customer {message.customer_id}. "
            f"Customer message: {message.message}",
            deps=deps,
            instructions=self.get_instruction(),
        )
        board.purchase = result.output
````

---

### 9.4 CustomerHistoryAgent
**Location**: `agents/customer_history_agent.py`

- **Reads**: `message.customer_id`
- **Writes**: `board.profile`
- **Tools**: `get_customer_profile`, `get_customer_order_count`, `get_recent_complaints`, `get_complaint_count`, `log_decision`

````python
class CustomerHistoryAgent(BaseAgent):

    def get_instruction(self) -> str:
        return """
            You are the CustomerHistoryAgent in a customer service system.
            Build a profile of this customer from their history.

            Use your tools to:
            - Retrieve customer profile and tier (get_customer_profile)
            - Retrieve total order count (get_customer_order_count)
            - Retrieve recent complaint history (get_recent_complaints)
            - Retrieve total complaint count (get_complaint_count)

            Determine:
            - Tier (standard / premium / vip)
            - Total orders placed
            - Total previous complaints
            - is_repeat_issue — has this customer complained about the same type before?

            Initialise sentiment_score=0.0, sentiment_label="neutral".
            SentimentAgent will update these fields after gather() returns.

            Call log_decision once. Return a CustomerProfileEvent.
        """

    async def run(self, message: CustomerMessageEvent, board: Blackboard, deps: Deps) -> None:
        result = await self._agent.run(
            f"Build profile for customer {message.customer_id}. "
            f"Message context: {message.message}",
            deps=deps,
            instructions=self.get_instruction(),
        )
        board.profile = result.output
````

---

### 9.5 ComplaintClassificationAgent
**Location**: `agents/complaint_classification_agent.py`

- **Reads**: `message.message`
- **Writes**: `board.complaint_type`
- **Tools**: `log_decision`

````python
class ComplaintClassificationAgent(BaseAgent):

    def get_instruction(self) -> str:
        return """
            You are the ComplaintClassificationAgent.
            Classify the customer complaint from the message text only.

            complaint_type — exactly one of:
              "packaging" | "product" | "delivery" | "billing"

            severity — exactly one of:
              "low"    minor inconvenience, product still usable
              "medium" product impaired or unusable
              "high"   safety risk or significant financial impact

            keywords — key phrases describing the specific problem.

            Flags — set ONLY on explicit customer mention:
              wants_refund
              wants_replacement
              wants_complaint_filed

            Call log_decision once. Return a ComplaintTypeEvent.
        """

    async def run(self, message: CustomerMessageEvent, board: Blackboard, deps: Deps) -> None:
        result = await self._agent.run(
            f"Classify this complaint: {message.message}",
            deps=deps,
            instructions=self.get_instruction(),
        )
        board.complaint_type = result.output
````

---

### 9.6 SentimentAgent
**Location**: `agents/sentiment_agent.py`

- **Reads**: `message.message`
- **Writes**: `board.profile.sentiment_score`, `board.profile.sentiment_label`
- **Note**: Runs inside gather() alongside the other Phase 1 agents. `board.profile`
  is guaranteed to be set by the time sentiment writes to it because both agents run
  concurrently — if sentiment finishes before history, it checks and holds.
- **Tools**: `log_decision`

````python
class SentimentAgent(BaseAgent):

    def get_instruction(self) -> str:
        return """
            You are the SentimentAgent.
            Score the emotional tone of the customer message.

            sentiment_score from -1.0 to 1.0:
              < -0.5          very angry
              -0.5 to -0.1   frustrated
              -0.1 to  0.1   neutral
              > 0.1           satisfied

            sentiment_label:
              "angry"       score < -0.5
              "frustrated"  -0.5 <= score < -0.1
              "neutral"     -0.1 <= score <= 0.1
              "satisfied"   score > 0.1

            Call log_decision once.
            Return {"sentiment_score": float, "sentiment_label": str}.
        """

    async def run(self, message: CustomerMessageEvent, board: Blacboard, deps: Deps) -> None:
        result = await self._agent.run(
            f"Score the sentiment of this message: {message.message}",
            deps=deps,
            instructions=self.get_instruction(),
        )
        score = result.output
        if board.profile is not None:
            board.profile.sentiment_score = score["sentiment_score"]
            board.profile.sentiment_label = score["sentiment_label"]
````

---

### 9.7 RefundEligibilityAgent

**Location**: `agents/refund_eligibility_agent.py`

- **Reads**: `board.purchase`, `board.complaint_type`, `deps.policy`
- **Writes**: `board.refund_eligibility`
- **No LLM** — pure Python logic against policy rules

````python
class RefundEligibilityAgent(BaseAgent):

    def __init__(self, name: str):
        super().__init__(name, agent=None)

    def get_instruction(self) -> str:
        return ""

    async def run(self, board: Blackboard, deps: Deps) -> None:
        purchase  = board.purchase
        complaint = board.complaint_type
        policy    = deps.policy

        if not purchase.verified:
            board.refund_eligibility = RefundEligibilityEvent(
                message_id=purchase.message_id,
                eligible=False,
                reason="Purchase could not be verified",
            )
            return

        window = policy.refund_window_days
        days   = purchase.days_since_purchase or 0

        if days > window:
            board.refund_eligibility = RefundEligibilityEvent(
                message_id=purchase.message_id,
                eligible=False,
                reason=f"Purchase is {days} days old. Refund window is {window} days.",
            )
            return

        auto = complaint.complaint_type in policy.auto_refund_complaint_types
        board.refund_eligibility = RefundEligibilityEvent(
            message_id=purchase.message_id,
            eligible=True,
            reason=(
                f"Auto-approved: '{complaint.complaint_type}' qualifies under policy."
                if auto else
                "Within refund window. Complaint type is eligible."
            ),
            refund_amount=purchase.order_total,
        )
````

---

### 9.8 ResolutionAgent
**Location**: `agents/resolution_agent.py`

- **Reads**: `board.purchase`, `board.profile`, `board.complaint_type`, `board.refund_eligibility`
- **Writes**: `board.resolution`
- **Tools**: `log_decision`

````python
class ResolutionAgent(BaseAgent):

    def get_instruction(self) -> str:
        return """
            You are the ResolutionAgent.
            Recommend resolution options based on all available findings.

            Consider:
            - Refund eligibility and amount
            - Customer tier and complaint history
            - Complaint type and severity
            - Whether replacement is applicable

            escalate_to_human if: complaint is high severity and customer is vip,
            or previous complaints exceed the policy threshold.

            Call log_decision once. Return a ResolutionOptionsEvent.
        """

    async def run(self, board: Blackboard, deps: Deps) -> None:
        result = await self._agent.run(
            f"Purchase:           {board.purchase}\n"
            f"Customer profile:   {board.profile}\n"
            f"Complaint type:     {board.complaint_type}\n"
            f"Refund eligibility: {board.refund_eligibility}\n"
            f"Policy:             {deps.policy}\n"
            f"Decide resolution options.",
            deps=deps,
            instructions=self.get_instruction(),
        )
        board.resolution = result.output
````

---

### 9.9 ResponseComposerAgent
**Location**: `agents/response_composer_agent.py`

- **Reads**: `board.resolution`, `board.profile`, `board.complaint_type`
- **Writes**: `board.response`
- **Tools**: `get_customer_profile`, `log_decision`

````python
class ResponseComposerAgent(BaseAgent):

    def get_instruction(self) -> str:
        return """
            You are the ResponseComposerAgent.
            Write the final customer-facing response.

            Tone rules:
            - Address the customer by first name
            - Acknowledge the complaint specifically
            - State each action taken clearly
            - Match tone to sentiment: frustrated = warmer, neutral = professional
            - Never mention internal agent names or system details

            Call log_decision once. Return a CustomerResponseEvent.
        """

    async def run(self, board: Blackboard, deps: Deps) -> None:
        result = await self._agent.run(
            f"Resolution:      {board.resolution}\n"
            f"Customer profile:{board.profile}\n"
            f"Complaint:       {board.complaint_type}\n"
            f"Write the customer response.",
            deps=deps,
            instructions=self.get_instruction(),
        )
        board.response = result.output
---

## 10. main.py

```python
```python
import asyncio
import json
import uuid
from datetime import datetime

from agents.purchase_verification_agent import PurchaseVerificationAgent
from agents.customer_history_agent import CustomerHistoryAgent
from agents.complaint_classification_agent import ComplaintClassificationAgent
from agents.sentiment_agent import SentimentAgent
from agents.refund_eligibility_agent import RefundEligibilityAgent
from agents.resolution_agent import ResolutionAgent
from agents.response_composer_agent import ResponseComposerAgent

from core.deps import Deps
from core.blackboard import Blackboard
from core.logger import logger
from core.llm_factory import make_model

from db.connection import Database
from db.repositories.facade import RepoFacade
from db.repositories.policy_repo import PolicyRepository

from pydantic_ai import Agent

from schemas.events.inbound import CustomerMessageEvent
from schemas.events.board import (
    PurchaseVerifiedEvent, CustomerProfileEvent, ComplaintTypeEvent,
    RefundEligibilityEvent, ResolutionOptionsEvent, CustomerResponseEvent,
)

from tools.agent_logger import log_decision
from tools.customer_tools import get_customer_profile
from tools.order_tools import get_order_summary, get_order_line_items, get_order_total, get_customer_order_count
from tools.complaint_tools import get_recent_complaints, get_complaint_count


# ── Agents (constructed once, reused across requests) ─────────────────────────

purchase_agent = PurchaseVerificationAgent(
    name="purchase_verification",
    agent=Agent(
        model=make_model(PROVIDER), deps_type=Deps,
        output_type=PurchaseVerifiedEvent,
        tools=[log_decision, get_order_summary, get_order_line_items, get_order_total],
    ),
)
history_agent = CustomerHistoryAgent(
    name="customer_history",
    agent=Agent(
        model=make_model(PROVIDER), deps_type=Deps,
        output_type=CustomerProfileEvent,
        tools=[log_decision, get_customer_profile, get_customer_order_count,
               get_recent_complaints, get_complaint_count],
    ),
)
classification_agent = ComplaintClassificationAgent(
    name="complaint_classification",
    agent=Agent(
        model=make_model(PROVIDER), deps_type=Deps,
        output_type=ComplaintTypeEvent,
        tools=[log_decision],
    ),
)
sentiment_agent = SentimentAgent(
    name="sentiment",
    agent=Agent(
        model=make_model(PROVIDER), deps_type=Deps,
        output_type=dict,
        tools=[log_decision],
    ),
)
refund_agent = RefundEligibilityAgent(name="refund_eligibility")
resolution_agent = ResolutionAgent(
    name="resolution",
    agent=Agent(
        model=make_model(PROVIDER), deps_type=Deps,
        output_type=ResolutionOptionsEvent,
        tools=[log_decision],
    ),
)
composer_agent = ResponseComposerAgent(
    name="response_composer",
    agent=Agent(
        model=make_model(PROVIDER), deps_type=Deps,
        output_type=CustomerResponseEvent,
        tools=[log_decision, get_customer_profile],
    ),
)


async def handle_customer_message(message: CustomerMessageEvent, repo: RepoFacade, policy) -> dict:

    board = Blackboard()
    deps = Deps(
        repo=repo,
        board=board,
        policy=policy,
        message_id=message.message_id,
        customer_id=message.customer_id,
        order_id=message.order_id,
        total_tokens=0,
    )

    # ── Phase 1 — run all 4 concurrently ─────────────────────────────────────
    logger.info(f"[{message.message_id}] phase 1 start")
    await asyncio.gather(
        purchase_agent.run(message, board, deps),
        history_agent.run(message, board, deps),
        classification_agent.run(message, board, deps),
        sentiment_agent.run(message, board, deps),
    )
    logger.info(f"[{message.message_id}] phase 1 complete")

    # ── Phase 2 — run sequentially, each reads from board ─────────────────
    logger.info(f"[{message.message_id}] phase 2 start")
    await refund_agent.run(board, deps)
    await resolution_agent.run(board, deps)
    await composer_agent.run(board, deps)
    logger.info(f"[{message.message_id}] phase 2 complete")

    response = board.response
    return {
        "resolved":      response.resolved      if response else False,
        "response":      response.response       if response else "System error",
        "actions_taken": response.actions_taken  if response else [],
        "total_tokens":  deps.total_tokens,
    }


async def main():
    await Database.get_pool()
    repo   = RepoFacade()
    policy = await PolicyRepository().get()

    try:
        message = CustomerMessageEvent(
            message_id=str(uuid.uuid4()),
            customer_id="C001",
            order_id="ORD-1001",
            message=(
                "Hi, I bought a keyboard last month but when it arrived the packaging "
                "was completely crushed and one of the keys is already not working. "
                "I never had this problem with you before. This is really frustrating. "
                "Can I get a refund or at least file a complaint about this?"
            ),
            timestamp=datetime.now().isoformat(),
        )

        result = await handle_customer_message(message, repo, policy)
        print(json.dumps(result, indent=2))

    finally:
        await Database.close()


asyncio.run(main())
```

---

## Section 11 — Replace Implementation Sequence

**Phase 1 — Database** (`db/`)
- Run `schema.sql`. Test each repository method directly.
  
**Phase 2 — Schemas** (`schemas/`)
- Instantiate each schema with dummy data, round-trip `model_dump()` / `model_validate()`.

**Phase 3 — Core** (`core/`)
- Build `Blackboard` and `Deps`. Confirm `Blackboard.is_complete()` is `False` until `response` is set.

**Phase 4 — Tools** (`tools/`)
- Test each tool directly with a live `RepoFacade`.

**Phase 5 — RefundEligibilityAgent** (`agents/`)
- Build first — no LLM. Test `run()` directly against all branches: purchase not verified,
outside window, auto-approved type, standard eligible.

**Phase 6 — Phase 1 agents** (`agents/`)
- Build in order: 
  - `ComplaintClassificationAgent` → `PurchaseVerificationAgent` →
`CustomerHistoryAgent` → `SentimentAgent`. 
  - For each: call `agent.run(message, findings, deps)`
directly and confirm the correct field is written to `board`.

**Phase 7 — Phase 2 agents** (`agents/`)
- Build in order: 
  - `ResolutionAgent` → `ResponseComposerAgent`. 
  - Pre-populate `board` with
dummy Phase 1 results and confirm each agent reads correctly and writes its own field.

**Phase 8 — Integration** (`main.py`)
- Run end-to-end with sample message. Confirm Phase 1 agents activate concurrently,
`board.response` is set after Phase 2 completes.

---

## 12. Expected Output

### Success

```json
{
  "resolved": true,
  "response": "Hi Aisha, I'm really sorry about the damaged packaging and the faulty
               key — that's not the experience we want for you. I've approved a full
               refund of RM149.90 to your original payment method, arriving within
               3-5 business days. I've also logged a formal complaint so our quality
               team can investigate. Thank you for letting us know.",
  "actions_taken": ["refund_approved", "complaint_filed"],
  "total_tokens": 4820
}
```

### Purchase not verified

```json
{
  "resolved": false,
  "response": "We were unable to verify your purchase with the order number provided.
               Please contact our support team directly with your order confirmation.",
  "actions_taken": [],
  "total_tokens": 1240
}
```