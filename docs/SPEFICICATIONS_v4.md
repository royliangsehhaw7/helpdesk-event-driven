# Multi-Agent Customer Service System
## Specification v4.0 — Observer / Event-Driven Pattern with Tool-Based Data Access

---

## 1. Purpose

A learning-oriented multi-agent system that handles customer service requests end-to-end. The
primary goal is to understand and implement the **Observer Pattern with concurrent agent fan-out**
in a grounded, production-mimicking context using pydantic-ai's deps, agents, and tools features
properly.

---

## 2. Architecture

### 2.1 Pattern

This system implements the **Observer Pattern with Event-Driven Agent Fan-out**.

An `MessageHub` is a dictionary that maps contract types to lists of handler functions. When
`hub.publish(contract)` is called, the hub looks up the contract type in its dictionary, finds all
registered handlers, and calls them all via `asyncio.gather()`.

Agents register themselves into that dictionary at startup by calling `subscribe()`. Each agent
tells the message hub which contract type it wants to listen to. From that point on, whenever that contract 
type is published, the hub calls that agent's handler.

No agent is told to go by an orchestrator. No scheduler sequences the work. Each agent fires
the moment the message hub calls its handler — and whether it does real work or exits silently depends
entirely on its own internal gate condition.

### 2.2 Why no Blackboard

The Blackboard pattern is appropriate when the solution is unknown upfront and emerges
incrementally from agent contributions — medical diagnosis, speech recognition, document
analysis. This system's source data — customers, orders, complaint history — are known,
structured, queryable facts in MySQL. Agents query them on demand through tools. The
`BlackBoard` class is removed entirely.

The only accumulation need — storing agent findings so downstream agents can read them —
is handled by `Blackboard`, a plain typed dataclass on `Deps`. It is a result accumulator, not
a blackboard.

### 2.3 Agents

There are seven agents. There is no orchestrator. There is no scheduler loop.

| Agent | Subscribes to | Posts |
|---|---|---|
| `PurchaseVerificationAgent` | `CustomerMessageContract` | `PurchaseVerifiedContract` |
| `CustomerHistoryAgent` | `CustomerMessageContract` | `CustomerProfileContract` |
| `ComplaintClassificationAgent` | `CustomerMessageContract` | `ComplaintTypeContract` |
| `SentimentAgent` | `CustomerMessageContract` | updates `findings.profile.sentiment_*` |
| `RefundEligibilityAgent` | `PurchaseVerifiedContract` + `ComplaintTypeContract` | `RefundEligibilityContract` |
| `ResolutionAgent` | `RefundEligibilityContract` + `CustomerProfileContract` | `ResolutionOptionsContract` |
| `ResponseComposerAgent` | `ResolutionOptionsContract` | `CustomerResponseContract` |

---

## 3. Execution Model

### 3.1 What the MessageHub actually is

The `MessageHub` is a dictionary and a `asyncio.gather()` call. Nothing more.

```python
# The entire state of the hub after all subscribe() calls:
{
    CustomerMessageContract:   [purchase_handler, history_handler, classification_handler, sentiment_handler],
    PurchaseVerifiedContract:  [refund_handler],
    ComplaintTypeContract:     [refund_handler],
    CustomerProfileContract:   [resolution_handler],
    RefundEligibilityContract: [resolution_handler],
    ResolutionOptionContract:  [composer_handler],
}

# publish() does exactly this:
async def publish(self, contract: BaseModel) -> None:
    handlers = self._subscribers.get(type(contract), [])
    if handlers:
        await asyncio.gather(*[h(contract) for h in handlers])
```

- When `publish(CustomerMessageContract)` is called 
  - the hub looks up `CustomerMessageContract`        
    - finds 4 handlers, and calls `asyncio.gather()` on all 4. 
- When `publish(PurchaseVerifiedContract)` is called 
  - the hub looks up `PurchaseVerifiedContract` 
    - finds 1 handler, and calls it.

### 3.2 What asyncio.gather() actually does

```python
await asyncio.gather(coro_a(), coro_b(), coro_c())
```

Python schedules all three coroutines on the event loop.
- It starts `coro_a` and runs it until it hits an `await`. At that point Python suspends `coro_a` and starts `coro_b`.
- It runs `coro_b` until its first `await`, then suspends it and starts `coro_c`.
- When any awaited operation completes (e.g. a network response arrives), Python resumes the
  corresponding coroutine from where it left off.

The key implication: **coroutines interleave at `await` points, not at arbitrary lines**.
Between two `await` statements, a coroutine runs uninterrupted.

### 3.3 The correct execution order

There is no rule that says "all Phase 1 agents complete before any Phase 2 agent is called."
**Phase 2 agents are called immediately as each Phase 1 agent finishes and publishes its result.**

What controls whether a Phase 2 agent does real work is its internal gate condition:

```python
if deps.board.purchase is None or deps.board.complaint_type is None:
    return  # called but does nothing
```

So the correct statement is:

- Phase 2 agents **get called** as soon as any Phase 1 agent that they subscribed to finishes
- Phase 2 agents **do real work** only when all their required findings are present
- The gate condition is the only thing enforcing this — not the hub, not asyncio

### 3.4 Step by step execution

**Step 1 — the subscribe loop builds the dictionary**

```python
for agent in [
    purchase_agent,
    history_agent,
    classification_agent,
    sentiment_agent,
    refund_agent,
    resolution_agent,
    composer_agent,
]:
    agent.subscribe(hub, deps)
```

Each agent's `subscribe()` method calls `hub.subscribe(ContractType, handler)` which appends
the handler to the dictionary list for that contract type. Nothing runs. The dictionary is just built.

**Step 2 — the single trigger in main.py**

```python
await hub.publish(message)  # message is CustomerMessageContract
```

Inside `hub.publish()`:

```python
handlers = self._subscribers.get(type(contract), [])
# handlers = [purchase_handler, history_handler, classification_handler, sentiment_handler]
await asyncio.gather(*[h(contract) for h in handlers])
```

All 4 handlers start. Each one immediately hits `await self._agent.run(...)` inside its
`handle()` method and suspends. All 4 LLM calls are now in flight simultaneously.

**Step 3 — say classification_agent LLM responds first**

Its `handle()` resumes from `await self._agent.run(...)`:

```python
async def handle(self, contract: CustomerMessageContract, deps: Deps) -> None:
    result = await self._agent.run(...)           # resumes here
    finding: ComplaintTypeContract = result.output
    deps.board.complaint_type = finding        # stores finding
    await deps.hub.publish(finding)               # publishes ComplaintTypeContract
```

`hub.publish(finding)` looks up `ComplaintTypeContract`. Finds `[refund_handler]`. Calls
`refund_agent.handle()` immediately — right now, while the other 3 Phase 1 agents are
still waiting for their LLM responses:

```python
async def handle(self, contract, deps: Deps) -> None:
    async with self._lock:
        if self._fired:
            return
        if deps.board.purchase is None or deps.board.complaint_type is None:
            return  # purchase is None — exits silently
```

`deps.board.purchase` is `None` because `purchase_agent` has not finished yet. Gate fails.
`refund_agent` exits silently. Control returns to `classification_agent.handle()` which is now done.

**Step 4 — say purchase_agent LLM responds next**

Its `handle()` resumes:

```python
async def handle(self, contract: CustomerMessageContract, deps: Deps) -> None:
    result = await self._agent.run(...)        # resumes here
    finding: PurchaseVerifiedContract = result.output
    deps.board.purchase = finding           # stores finding
    await deps.hub.publish(finding)            # publishes PurchaseVerifiedContract
```

`hub.publish(finding)` looks up `PurchaseVerifiedContract`. Finds `[refund_handler]`. Calls
`refund_agent.handle()` again — immediately:

```python
async def handle(self, contract, deps: Deps) -> None:
    async with self._lock:
        if self._fired:
            return
        if deps.board.purchase is None or deps.board.complaint_type is None:
            return
        # purchase is set (just now) and complaint_type is set (from Step 3)
        # gate passes
        self._fired = True  # set inside lock so it never fires twice
```

Gate passes. Continues outside the lock:

```python
    finding = self._check(
        deps.board.purchase,
        deps.board.complaint_type,
        deps.policy,
    )
    deps.board.refund_eligibility = finding
    await deps.hub.publish(finding)            # publishes RefundEligibilityContract
```

`hub.publish(finding)` looks up `RefundEligibilityContract`. Finds `[resolution_handler]`. Calls
`resolution_agent.handle()` immediately — while `history_agent` and `sentiment_agent` are
still waiting for their LLM responses:

```python
async def handle(self, contract, deps: Deps) -> None:
    async with self._lock:
        if self._fired:
            return
        if (deps.board.refund_eligibility is None
                or deps.board.profile is None
                or deps.board.complaint_type is None):
            return  # profile is None — history_agent not done yet — exits silently
```

Gate fails. `resolution_agent` exits silently. Control unwinds back through `refund_agent.handle()`,
back through `purchase_agent.handle()` which is now done. Back to the original `asyncio.gather()`.
Still waiting for `history_agent` and `sentiment_agent`.

**Step 5 — history_agent LLM responds**

Its `handle()` resumes:

```python
async def handle(self, contract: CustomerMessageContract, deps: Deps) -> None:
    result = await self._agent.run(...)        # resumes here
    finding: CustomerProfileContract = result.output
    deps.board.profile = finding            # stores finding
    await deps.hub.publish(finding)            # publishes CustomerProfileContract
```

`hub.publish(finding)` looks up `CustomerProfileContract`. Finds `[resolution_handler]`. Calls
`resolution_agent.handle()`:

```python
async def handle(self, contract, deps: Deps) -> None:
    async with self._lock:
        if self._fired:
            return
        if (deps.board.refund_eligibility is None
                or deps.board.profile is None
                or deps.board.complaint_type is None):
            return
        # all three are now set — gate passes
        self._fired = True
```

Gate passes. Continues:

```python
    result = await self._agent.run(...)
    finding: ResolutionOptionsContract = result.output
    deps.board.resolution = finding
    await deps.hub.publish(finding)            # publishes ResolutionOptionsContract
```

`hub.publish(finding)` looks up `ResolutionOptionsContract`. Finds `[composer_handler]`. Calls
`composer_agent.handle()`:

```python
async def handle(self, contract: ResolutionOptionsContract, deps: Deps) -> None:
    async with self._lock:
        if self._fired:
            return
        self._fired = True

    result = await self._agent.run(...)
    finding: CustomerResponseContract = result.output
    deps.board.response = finding
    await deps.hub.publish(finding)            # publishes CustomerResponseContract
```

`hub.publish(finding)` looks up `CustomerResponseContract`. Nobody subscribed. `handlers` is `[]`.
Does nothing. `composer_agent.handle()` is done.

Control unwinds: back through `resolution_agent.handle()`, back through `history_agent.handle()`
which is now done. `sentiment_agent` finishes around this time too. The original `asyncio.gather()`
has all 4 coroutines done. Returns. `await hub.publish(message)` in `main.py` returns.
`deps.board.response` is set.

**Step 6 — main.py reads the result**

```python
await hub.publish(message)
# returns here — entire cascade is complete

response = deps.board.response
return {
    "resolved":      response.resolved,
    "response":      response.response,
    "actions_taken": response.actions_taken,
    "total_tokens":  deps.total_tokens,
}
```

### 3.5 The race condition — and why locks are mandatory

Consider this scenario: `purchase_agent` and `classification_agent` finish at almost the same
time. Both call `await deps.hub.publish(their_finding)` in rapid succession. The event loop
can interleave them:

```
classification_agent:  deps.board.complaint_type = ComplaintTypeContract
                       await hub.publish(ComplaintTypeContract)
                             └── refund_agent.handle()
                                   gate check: purchase ✓  complaint ✓  → PASSES
                                   ← still inside _check(), hasn't set _fired yet

purchase_agent:        await hub.publish(PurchaseVerifiedContract)
                             └── refund_agent.handle()
                                   gate check: purchase ✓  complaint ✓  → PASSES AGAIN
```

Both invocations pass the gate. `_check()` runs twice. `RefundEligibilityContract` is published
twice. `ResolutionContract` fires twice. `CustomerResponseContract` is written twice.

**The fix: asyncio.Lock with a _fired flag on every agent that must fire only once.**

```python
async def handle(self, contract, deps: Deps) -> None:
    async with self._lock:
        if self._fired:
            return
        if deps.board.purchase is None or deps.board.complaint_type is None:
            return
        self._fired = True      # set inside lock before releasing

    # only one invocation ever reaches here
    finding = self._check(...)
    deps.board.refund_eligibility = finding
    await deps.hub.publish(finding)
```

The lock ensures only one invocation can be inside the critical section at a time. The `_fired`
flag ensures subsequent invocations exit immediately even after the lock is released.

### 3.6 Gate conditions per agent

| Agent | Gate condition | Lock needed |
|---|---|---|
| `RefundEligibilityAgent` | `purchase` + `complaint_type` both set | Yes |
| `ResolutionAgent` | `refund_eligibility` + `profile` + `complaint_type` all set | Yes |
| `ResponseComposerAgent` | receives `ResolutionOptionsContract` | Yes (defensive) |

`ResponseComposerAgent` technically only receives one contract and `ResolutionAgent` fires it
exactly once due to its own lock. But the defensive lock is cheap and makes the invariant explicit.

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
│   ├── llm_factory.py
│   └── message_hub.py
├── db/
│   ├── connection.py
│   └── repositories/
│       ├── facade.py
│       ├── customer_repo.py
│       ├── order_repo.py
│       ├── complaint_repo.py
│       └── policy_repo.py
├── schemas/
│   ├── customer.py
│   ├── order.py
│   ├── complaint.py
│   ├── policy.py
│   └── contracts/
│       ├── facade_contracts.py
│       ├── customer_message.py
│       ├── customer_message.py
│       ├── customer_profile.py
│       ├── customer_response.py
│       ├── purchase_verified.py
│       ├── refund_eligibility.py
│       └── resolution_option.py
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

`db/repositories/facade.py`

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

**`schemas/customer.py`**

```python
from pydantic import BaseModel

class Customer(BaseModel):
    id: str
    name: str
    email: str
    tier: str          # "standard" | "premium" | "vip"
    joined_date: str
```

**`schemas/order.py`**

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

**`schemas/policy.py`**

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

### 6.2 Contract Schemas

**`schemas/contracts/customer_message.py`**
```python
class CustomerMessageContract(BaseModel):
    message_id: str    # uuid — ties all findings for this request together
    customer_id: str
    order_id: str
    message: str
    timestamp: str
```

**`schemas/contracts/purchases_verified.py`**
```python
class PurchaseVerifiedContract(BaseModel):
    message_id: str
    verified: bool
    order_date: str | None = None
    product_name: str | None = None
    product_category: str | None = None
    days_since_purchase: int | None = None
    order_total: float | None = None
    reason: str | None = None
```

**`schemas/contracts/customer_profile.py`**
```python
class CustomerProfileContract(BaseModel):
    message_id: str
    customer_id: str
    tier: str
    total_orders: int
    previous_complaints: int
    is_repeat_issue: bool
    sentiment_score: float = 0.0
    sentiment_label: str = "neutral"
```

**`schemas/contracts/complaint_type.py`**
```python
class ComplaintTypeContract(BaseModel):
    message_id: str
    complaint_type: str        # "packaging"|"product"|"delivery"|"billing"
    severity: str              # "low"|"medium"|"high"
    keywords: list[str]
    wants_refund: bool
    wants_replacement: bool
    wants_complaint_filed: bool
```

**`schemas/contracts/refund_eligibility.py`**
```python
class RefundEligibilityContract(BaseModel):
    message_id: str
    eligible: bool
    reason: str
    refund_amount: float | None = None
    extended_due_to_tier: bool = False
```

**`schemas/contracts/resolution_option.py`**
```python
class ResolutionOptionsContract(BaseModel):
    message_id: str
    options: list[str]
    recommended: str
    escalate_to_human: bool = False
    escalation_reason: str | None = None
```

**`schemas/contracts/customer_response.py`**
```python
class CustomerResponseContract(BaseModel):
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
from schemas.contracts import (
    PurchaseVerifiedContract, CustomerProfileContract, ComplaintTypeContract,
    RefundEligibilityContract, ResolutionOptionsContract, CustomerResponseContract,
)

@dataclass
class Blackboard:
    """Result accumulator for one request lifecycle.

    Agents write here after their LLM call completes.
    Downstream agents read here to check gate conditions.
    One instance per CustomerMessageContract. Discarded when complete.
    """
    purchase:           PurchaseVerifiedContract  | None = None
    profile:            CustomerProfileContract   | None = None
    complaint_type:     ComplaintTypeContract     | None = None
    refund_eligibility: RefundEligibilityContract | None = None
    resolution:         ResolutionOptionsContract | None = None
    response:           CustomerResponseContract  | None = None

    def is_complete(self) -> bool:
        return self.response is not None
```

### 7.2 Deps

**Location**: `core/deps.py`

```python
from dataclasses import dataclass
from core.message_hub import MessageHub
from core.board import Blackboard
from db.repositories.facade import RepoFacade
from schemas.data.policy import Policy

@dataclass
class Deps:
    """Injected into every agent run via RunContext.

    repo         — facade grouping all repositories. Tools call ctx.deps.repo.<repo>.<method>().
    hub          — message hub. Agents publish findings through it.
    findings     — accumulates agent output events for this request.
    policy       — single policy config, loaded once at startup.
    message_id,
    customer_id,
    order_id     — request identifiers for this activation.
    total_tokens — accumulated LLM token usage across all agents for this request.
    """
    repo:         RepoFacade
    hub:          MessageHub
    board:        Blalckboard
    policy:       Policy
    message_id:   str
    customer_id:  str
    order_id:     str
    total_tokens: int = 0
```

### 7.3 MessageHub

**Location**: `core/message_hub.py`

The `MessageHub` has one job: fan out. It is a dictionary mapping contract types to lists of handler
functions. `subscribe()` appends to the list. `publish()` looks up the list and calls
`asyncio.gather()` on all handlers for that contract type.

Handlers are registered as **closures that capture `deps`** at subscription time. This keeps
the hub signature clean — `publish(contract)` only, no `deps` parameter leaking into the hub.

```python
import asyncio
from collections import defaultdict
from typing import Callable
from pydantic import BaseModel


class MessageHub:
    """Pure fan-out message hub. Zero domain knowledge.

    subscribe(contract_type, handler):
        Appends handler to the list for that contract type.
        Handler signature: async def handler(contract: BaseModel) -> None
        Called once per agent per contract type at startup.

    publish(contract):
        Looks up type(contract) in the dictionary.
        Calls asyncio.gather() on all handlers in that list.
        Does not return until all handlers and their downstream publishes complete.
        Handlers for other contract types are never called.
    """

    def __init__(self):
        self._subscribers: dict[type, list[Callable]] = defaultdict(list)

    def subscribe(self, contract_type: type, handler: Callable) -> None:
        self._subscribers[contract_type].append(handler)

    async def publish(self, contract: BaseModel) -> None:
        handlers = self._subscribers.get(type(contract), [])
        if handlers:
            await asyncio.gather(*[h(contract) for h in handlers])
```

**How handlers capture deps via closure in each agent's `subscribe()`:**

```python
# inside any agent's subscribe() method
def subscribe(self, hub: MessageHub, deps: Deps) -> None:
    async def handler(contract):
        await self.handle(contract, deps)   # deps captured here at subscription time
    hub.subscribe(CustomerMessageContract, handler)
```

The hub calls `handler(contract)`. The handler calls `self.handle(contract, deps)` with the captured
`deps`. The hub never receives or knows about `deps`.

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
tools mid-reasoning to pull exactly the data it needs — never pre-loaded. Each agent stores its
finding in `deps.board` and publishes it to the hub. No agent knows or cares what other
agents exist.

All agents that must fire only once carry an `asyncio.Lock` and a `_fired` flag. See Section
3.5 for the full explanation of why this is mandatory.

### 9.2 BaseAgent

**Location**: `agents/base_agent.py`

```python
from abc import ABC, abstractmethod
from pydantic_ai import Agent
from core.message_hub import MessageHub
from core.deps import Deps


class BaseAgent(ABC):
    def __init__(self, name: str, agent: Agent | None):
        self._name = name
        self._agent = agent

    @property
    def name(self) -> str:
        return self._name

    @abstractmethod
    def subscribe(self, hub: MessageHub, deps: Deps) -> None: ...

    @abstractmethod
    def get_instruction(self) -> str: ...
```

### 9.3 PurchaseVerificationAgent

- **Subscribes to**: `CustomerMessageContract`
- **Responsibility**: Verify the order exists, belongs to this customer, is delivered, and
  determine days since purchase and what was purchased.
- **Output**: `PurchaseVerifiedContract` → `deps.board.purchase`
- **Tools**: `get_order_summary`, `get_order_line_items`, `get_order_total`, `log_decision`
- **Lock**: Not needed — writes a unique findings field, never fires twice for the same field.

```python
class PurchaseVerificationAgent(BaseAgent):

    def subscribe(self, hub: MessageHub, deps: Deps) -> None:
        async def handler(contract):
            await self.handle(contract, deps)
        hub.subscribe(CustomerMessageContract, handler)

    def get_instruction(self) -> str:
        return """
            You are the PurchaseVerificationAgent in a customer service system.
            Your sole job is to verify the customer's purchase claim.

            Use your tools to:
            - Retrieve the order header (get_order_summary) — checks ownership automatically
            - Retrieve line items to identify what was purchased (get_order_line_items)
            - Retrieve the order total value (get_order_total)

            Determine:
            - Is the order found and does it belong to this customer?
            - Is the status "delivered"?
            - How many days since purchase?
            - What product and category?

            Set verified=False with a clear reason if order not found,
            not belonging to this customer, or not "delivered".

            Call log_decision once. Return a PurchaseVerifiedContract.
        """

    async def handle(self, contract: CustomerMessageContract, deps: Deps) -> None:
        result = await self._agent.run(
            f"Verify purchase for order {deps.order_id} "
            f"by customer {deps.customer_id}. "
            f"Customer message: {contract.message}",
            deps=deps,
            instructions=self.get_instruction(),
        )
        finding: PurchaseVerifiedContract = result.output
        deps.board.purchase = finding
        await deps.hub.publish(finding)
```

### 9.4 CustomerHistoryAgent

- **Subscribes to**: `CustomerMessageContract`
- **Responsibility**: Build customer profile — tier, order count, complaint count, repeat issue
  detection. Initialises sentiment to 0.0 for `SentimentAgent` to update.
- **Output**: `CustomerProfileContract` → `deps.board.profile`
- **Tools**: `get_customer_profile`, `get_customer_order_count`, `get_recent_complaints`,
  `get_complaint_count`, `log_decision`
- **Lock**: Not needed — writes a unique findings field.

```python
class CustomerHistoryAgent(BaseAgent):

    def subscribe(self, hub: MessageHub, deps: Deps) -> None:
        async def handler(contract):
            await self.handle(contract, deps)
        hub.subscribe(CustomerMessageContract, handler)

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
              Infer the complaint type from the customer's message to check history.

            Initialise sentiment_score=0.0, sentiment_label="neutral".
            SentimentAgent will update these fields independently.

            Call log_decision once. Return a CustomerProfileContract.
        """

    async def handle(self, contract: CustomerMessageContract, deps: Deps) -> None:
        result = await self._agent.run(
            f"Build profile for customer {deps.customer_id}. "
            f"Message context: {contract.message}",
            deps=deps,
            instructions=self.get_instruction(),
        )
        finding: CustomerProfileContract = result.output
        deps.board.profile = finding
        await deps.hub.publish(finding)
```

### 9.5 ComplaintClassificationAgent

- **Subscribes to**: `CustomerMessageContract`
- **Responsibility**: Classify complaint type, severity, keywords, and explicit intent flags
  from the message text. No database queries needed.
- **Output**: `ComplaintTypeContract` → `deps.board.complaint_type`
- **Tools**: `log_decision` only
- **Lock**: Not needed — writes a unique findings field.

```python
class ComplaintClassificationAgent(BaseAgent):

    def subscribe(self, hub: MessageHub, deps: Deps) -> None:
        async def handler(contract):
            await self.handle(contract, deps)
        hub.subscribe(CustomerMessageContract, handler)

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

            Flags — set ONLY on explicit customer mention, never inferred:
              wants_refund          customer mentions refund or money back
              wants_replacement     customer mentions replacement or exchange
              wants_complaint_filed customer mentions complaint or report

            Call log_decision once. Return a ComplaintTypeContract.
        """

    async def handle(self, contract: CustomerMessageContract, deps: Deps) -> None:
        result = await self._agent.run(
            f"Classify this complaint: {contract.message}",
            deps=deps,
            instructions=self.get_instruction(),
        )
        finding: ComplaintTypeContract = result.output
        deps.board.complaint_type = finding
        await deps.hub.publish(finding)
```

### 9.6 SentimentAgent

- **Subscribes to**: `CustomerMessageContract`
- **Responsibility**: Score emotional tone independently. Updates profile sentiment fields
  in place if profile is already posted. If not yet posted, the score is held until profile
  arrives — a second subscription on `CustomerProfileContract` applies the update.
- **Output**: in-place update of `deps.board.profile.sentiment_score` and `sentiment_label`
- **Tools**: `log_decision` only
- **Lock**: Not needed — in-place field update on an existing object, no publish.

```python
class SentimentAgent(BaseAgent):

    def subscribe(self, hub: MessageHub, deps: Deps) -> None:
        self._pending_score = None
        self._pending_label = None

        async def on_message(contract):
            await self.handle(contract, deps)

        async def on_profile(contract):
            # if sentiment finished before profile was posted, apply now
            if self._pending_score is not None:
                contract.sentiment_score = self._pending_score
                contract.sentiment_label = self._pending_label
                deps.board.profile = contract

        hub.subscribe(CustomerMessageContract, on_message)
        hub.subscribe(CustomerProfileContract, on_profile)

    def get_instruction(self) -> str:
        return """
            You are the SentimentAgent.
            Score the emotional tone of the customer message — tone only, not the complaint.

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

    async def handle(self, contract: CustomerMessageContract, deps: Deps) -> None:
        result = await self._agent.run(
            f"Score the sentiment of this message: {contract.message}",
            deps=deps,
            instructions=self.get_instruction(),
        )
        score = result.output
        if deps.board.profile is not None:
            deps.board.profile.sentiment_score = score["sentiment_score"]
            deps.board.profile.sentiment_label = score["sentiment_label"]
        else:
            # profile not yet posted — hold until on_profile fires
            self._pending_score = score["sentiment_score"]
            self._pending_label = score["sentiment_label"]
```

### 9.7 RefundEligibilityAgent

- **Subscribes to**: `PurchaseVerifiedContract` AND `ComplaintTypeContract`
- **Responsibility**: Pure Python eligibility check against policy. No LLM. No database.
- **Gate condition**: both `deps.board.purchase` and `deps.board.complaint_type` set.
- **Lock**: Mandatory — both subscribed contract can arrive and call this handler before either
  has had a chance to set `_fired`, causing it to run twice without the lock.
- **Output**: `RefundEligibilityContract` → `deps.board.refund_eligibility`

```python
import asyncio

class RefundEligibilityAgent(BaseAgent):

    def __init__(self, name: str):
        super().__init__(name, agent=None)
        self._lock   = asyncio.Lock()
        self._fired  = False

    def subscribe(self, hub: MessageHub, deps: Deps) -> None:
        async def handler(contract):
            await self.handle(contract, deps)
        hub.subscribe(PurchaseVerifiedContract, handler)
        hub.subscribe(ComplaintTypeContract, handler)

    def get_instruction(self) -> str:
        return ""

    async def handle(self, contract, deps: Deps) -> None:
        async with self._lock:
            if self._fired:
                return
            if (deps.board.purchase is None
                    or deps.board.complaint_type is None):
                return
            self._fired = True       # set inside lock before releasing

        # only one invocation ever reaches here
        finding = self._check(
            deps.board.purchase,
            deps.board.complaint_type,
            deps.policy,
        )
        deps.board.refund_eligibility = finding
        await deps.hub.publish(finding)

    def _check(self, purchase, complaint, policy) -> RefundEligibilityContract:
        if not purchase.verified:
            return RefundEligibilityContract(
                message_id=purchase.message_id,
                eligible=False,
                reason="Purchase could not be verified",
            )

        window = policy.refund_window_days
        days   = purchase.days_since_purchase or 0

        if days > window:
            return RefundEligibilityContract(
                message_id=purchase.message_id,
                eligible=False,
                reason=(
                    f"Purchase is {days} days old. "
                    f"Refund window is {window} days."
                ),
            )

        auto = complaint.complaint_type in policy.auto_refund_complaint_types
        return RefundEligibilityContract(
            message_id=purchase.message_id,
            eligible=True,
            reason=(
                f"Auto-approved: '{complaint.complaint_type}' qualifies under policy."
                if auto else
                "Within refund window. Complaint type is eligible."
            ),
            refund_amount=purchase.order_total,
        )
```

### 9.8 ResolutionAgent

- **Subscribes to**: `RefundEligibilityContract` AND `CustomerProfileContract`
- **Gate condition**: `refund_eligibility`, `profile`, and `complaint_type` all set.
- **Lock**: Mandatory — both subscribed contract can call this handler before either sets `_fired`.
- **Output**: `ResolutionOptionsContract` → `deps.board.resolution`
- **Tools**: `log_decision` only — all context from `deps.board`

```python
class ResolutionAgent(BaseAgent):

    def __init__(self, name: str, agent):
        super().__init__(name, agent)
        self._lock  = asyncio.Lock()
        self._fired = False

    def subscribe(self, hub: MessageHub, deps: Deps) -> None:
        async def handler(contract):
            await self.handle(contract, deps)
        hub.subscribe(RefundEligibilityContract, handler)
        hub.subscribe(CustomerProfileContract, handler)

    def get_instruction(self) -> str:
        return """
            You are the ResolutionAgent.
            Determine the best resolution options for this customer request.

            You receive a full context of all findings. Reason over all of them.

            Build options from: "refund" | "replacement" | "complaint_filing" | "escalation"
            - "refund"            if refund_eligibility.eligible is True
            - "replacement"       if product_category is in replacement_eligible_categories
            - "complaint_filing"  if customer requested it OR severity is "high"
            - "escalation"        if previous_complaints >= complaint_escalation_threshold
                                  OR severity is "high" and refund is not eligible

            Set recommended to the single best option given all context.
            Set escalate_to_human True if "escalation" is in options.

            Call log_decision once. Return a ResolutionOptionsContract.
        """

    async def handle(self, contract, deps: Deps) -> None:
        async with self._lock:
            if self._fired:
                return
            if (deps.board.refund_eligibility is None
                    or deps.board.profile is None
                    or deps.board.complaint_type is None):
                return
            self._fired = True

        import json
        context = json.dumps({
            "purchase":           deps.board.purchase.model_dump()
                                  if deps.board.purchase else {},
            "profile":            deps.board.profile.model_dump(),
            "complaint_type":     deps.board.complaint_type.model_dump(),
            "refund_eligibility": deps.board.refund_eligibility.model_dump(),
            "policy": {
                "replacement_eligible_categories":
                    deps.policy.replacement_eligible_categories,
                "complaint_escalation_threshold":
                    deps.policy.complaint_escalation_threshold,
            },
        }, indent=2)

        result = await self._agent.run(
            f"Determine resolution options:\n{context}",
            deps=deps,
            instructions=self.get_instruction(),
        )
        finding: ResolutionOptionsContract = result.output
        deps.board.resolution = finding
        await deps.hub.publish(finding)
```

### 9.9 ResponseComposerAgent

- **Subscribes to**: `ResolutionOptionsContract`
- **Responsibility**: Compose the final customer-facing response. All upstream findings are
  guaranteed present when this agent activates because `ResolutionAgent` only fires after
  all its own gate conditions pass.
- **Lock**: Defensive — `ResolutionAgent` fires only once due to its own lock, but the
  defensive lock here makes the invariant explicit and costs nothing.
- **Output**: `CustomerResponseContract` → `deps.board.response`
- **Tools**: `get_customer_profile`, `log_decision`

```python
class ResponseComposerAgent(BaseAgent):

    def __init__(self, name: str, agent):
        super().__init__(name, agent)
        self._lock  = asyncio.Lock()
        self._fired = False

    def subscribe(self, hub: MessageHub, deps: Deps) -> None:
        async def handler(contract):
            await self.handle(contract, deps)
        hub.subscribe(ResolutionOptionsContract, handler)

    def get_instruction(self) -> str:
        return """
            You are the ResponseComposerAgent.
            Compose a warm, clear, professional response to the customer.

            Use get_customer_profile to retrieve the customer's name for personalisation.

            Guidelines:
            - Address the customer by name
            - Acknowledge their specific complaint — reference what they described
            - State the resolution clearly — what happens and when
            - If escalating, explain what the customer should expect
            - Match tone to sentiment:
                "angry" or "frustrated"  → empathetic and apologetic opening
                "neutral"                → professional and direct
                "satisfied"              → warm and efficient
            - Plain language only. No corporate jargon.
            - Under 150 words.

            Call log_decision once. Return a CustomerResponseContract.
        """

    async def handle(self, contract: ResolutionOptionsContract, deps: Deps) -> None:
        async with self._lock:
            if self._fired:
                return
            self._fired = True

        import json
        context = json.dumps({
            "profile":            deps.board.profile.model_dump()
                                  if deps.board.profile else {},
            "complaint_type":     deps.board.complaint_type.model_dump()
                                  if deps.board.complaint_type else {},
            "purchase":           deps.board.purchase.model_dump()
                                  if deps.board.purchase else {},
            "refund_eligibility": deps.board.refund_eligibility.model_dump()
                                  if deps.board.refund_eligibility else {},
            "resolution":         contract.model_dump(),
        }, indent=2)

        result = await self._agent.run(
            f"Compose a customer response:\n{context}",
            deps=deps,
            instructions=self.get_instruction(),
        )
        finding: CustomerResponseContract = result.output
        deps.board.response = finding
        await deps.hub.publish(finding)
```

---

## 10. main.py

```python
import asyncio
import json
import uuid
from datetime import datetime

from pydantic_ai import Agent

from agents.purchase_verification_agent import PurchaseVerificationAgent
from agents.customer_history_agent import CustomerHistoryAgent
from agents.complaint_classification_agent import ComplaintClassificationAgent
from agents.sentiment_agent import SentimentAgent
from agents.refund_eligibility_agent import RefundEligibilityAgent
from agents.resolution_agent import ResolutionAgent
from agents.response_composer_agent import ResponseComposerAgent

from core.message_hub import MessageHub
from core.deps import Deps
from core.blackboard import Blackboard
from core.llm_factory import make_model
from core.logger import logger

from db.connection import Database
from db.repositories.facade import RepoFacade
from db.repositories.policy_repo import PolicyRepository

from schemas.contract.inbound import CustomerMessageContract
from schemas.contract.findings import (
    PurchaseVerifiedContract, CustomerProfileContract, ComplaintTypeContract,
    RefundEligibilityContract, ResolutionOptionsContract, CustomerResponseContract,
)

from tools.agent_logger import log_decision
from tools.customer_tools import get_customer_profile
from tools.order_tools import (
    get_order_summary, get_order_line_items,
    get_order_total, get_customer_order_count,
)
from tools.complaint_tools import get_recent_complaints, get_complaint_count

PROVIDER = "openai:gpt-4o-mini"


async def handle_customer_message(
    message: CustomerMessageContract,
    repo: RepoFacade,
    policy,
) -> dict:
    hub   = MessageHub()
    board = Blalckboard()

    deps = Deps(
        repo=repo,
        hub=hub,
        board=board,
        policy=policy,
        message_id=message.message_id,
        customer_id=message.customer_id,
        order_id=message.order_id,
        total_tokens=0,
    )

    purchase_agent = PurchaseVerificationAgent(
        name="purchase_verification",
        agent=Agent(
            model=make_model(PROVIDER), deps_type=Deps,
            output_type=PurchaseVerifiedContract,
            tools=[log_decision, get_order_summary,
                   get_order_line_items, get_order_total],
        ),
    )
    history_agent = CustomerHistoryAgent(
        name="customer_history",
        agent=Agent(
            model=make_model(PROVIDER), deps_type=Deps,
            output_type=CustomerProfileContract,
            tools=[log_decision, get_customer_profile,
                   get_customer_order_count,
                   get_recent_complaints, get_complaint_count],
        ),
    )
    classification_agent = ComplaintClassificationAgent(
        name="complaint_classification",
        agent=Agent(
            model=make_model(PROVIDER), deps_type=Deps,
            output_type=ComplaintTypeContract,
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
            output_type=ResolutionOptionsContract,
            tools=[log_decision],
        ),
    )
    composer_agent = ResponseComposerAgent(
        name="response_composer",
        agent=Agent(
            model=make_model(PROVIDER), deps_type=Deps,
            output_type=CustomerResponseContract,
            tools=[log_decision, get_customer_profile],
        ),
    )

    # Subscribe all agents — this builds the hub dictionary.
    # Each agent appends its handler to the list for its contract type.
    # Nothing runs here. The dictionary is just populated.
    for agent in [
        purchase_agent,
        history_agent,
        classification_agent,
        sentiment_agent,
        refund_agent,
        resolution_agent,
        composer_agent,
    ]:
        agent.subscribe(hub, deps)

    # Single publish call kicks off the entire cascade.
    # hub.publish() looks up CustomerMessageContract in the dictionary,
    # finds 4 handlers, and calls asyncio.gather() on them.
    # This call does not return until deps.board.response is set.
    logger.info(f"[{message.message_id}] cascade start")
    await hub.publish(message)
    logger.info(f"[{message.message_id}] cascade complete")

    response = deps.board.response
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
        message = CustomerMessageContract(
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

## 11. Implementation Sequence

**Phase 1 — Database** (`db/`)

Run `schema.sql`. Verify all tables and seed data. Test `Database.get_pool()` returns the same
pool object on repeated calls — confirming singleton behaviour. Test each repository method
directly: `get_order` with mismatched `customer_id` returns `None`, `get_history` with
`limit=10` never exceeds 10 rows, `PolicyRepository.get()` returns a valid `Policy` object.

**Phase 2 — Schemas** (`schemas/`)

Instantiate each schema with dummy data, round-trip `model_dump()` / `model_validate()`.
All contract schemas carry `message_id`. No schema has methods.

**Phase 3 — MessageHub** (`core/message_hub.py`)

Verify fan-out with a standalone script: three dummy async handlers subscribed to the same
contract type, one `publish()` call, confirm all three fire. Confirm handlers for other contract
types are not called.

**Phase 4 — Core** (`core/`)

Build `Blackboard` and `Deps`. Confirm `Blackboard.is_complete()` is `False` until `response`
is set.

**Phase 5 — Tools** (`tools/`)

Test each tool directly with a live `RepoFacade`: confirm `get_order_summary` returns the
error dict for a non-existent order, `get_recent_complaints` never returns more than 10 rows.

**Phase 6 — RefundEligibilityAgent** (`agents/`)

Build first — no LLM. Test `_check()` directly against all branches: purchase not verified,
outside window, auto-approved type, standard eligible. Manually trigger `handle()` twice
simultaneously and confirm `_fired` prevents double-posting.

**Phase 7 — Phase 1 agents** (`agents/`)

Build in order: `ComplaintClassificationAgent` → `PurchaseVerificationAgent` →
`CustomerHistoryAgent` → `SentimentAgent`. For each: subscribe to a live hub with deps,
call `hub.publish(CustomerMessageContract)`, confirm finding posted to `deps.board` and
published to hub.

**Phase 8 — Phase 2 agents** (`agents/`)

Build in order: `ResolutionAgent` → `ResponseComposerAgent`. Confirm gate exits silently when
findings missing. With all required findings set, confirm agent activates, calls LLM, posts
finding. Manually test lock: call `handle()` twice in quick succession, confirm second call
is a no-op.

**Phase 9 — Integration** (`main.py`)

Run end-to-end with sample message. Confirm: the 4 handlers for `CustomerMessageContract` start
concurrently, `log_decision` lines appear for all LLM agents, `RefundEligibilityAgent` produces
no token lines, `CustomerResponseContract` is the final output with `resolved=True`.

---

## 12. Expected Output

### Success

```json
{
  "resolved": true,
  "response": "Hi Aisha, I'm really sorry about the damaged packaging and the faulty key — that's not the experience we want for you. I've approved a full refund of RM149.90 to your original payment method, arriving within 3-5 business days. I've also logged a formal complaint so our quality team can investigate. Thank you for letting us know.",
  "actions_taken": ["refund_approved", "complaint_filed"],
  "total_tokens": 4820
}
```

### Purchase not verified

```json
{
  "resolved": false,
  "response": "We were unable to verify your purchase with the order number provided. Please contact our support team directly with your order confirmation.",
  "actions_taken": [],
  "total_tokens": 1240
}
```