# Multi-Agent Customer Service System
## Specification v3.0 — Observer / Event-Driven Pattern with Tool-Based Data Access

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

An `EventBus` fans incoming customer message events out to all Phase 1 analysis agents
simultaneously via `asyncio.gather()`. Each agent activates independently, queries MySQL through
its tools via the `RepoFacade`, posts its finding to `deps.findings`, and publishes a new event
to the bus. Phase 2 synthesis agents subscribe to those finding events, check gate conditions
protected by `asyncio.Lock`, and activate only when all required findings are present and only
once per request regardless of how many subscribed events arrive.

No agent is told to go. No orchestrator sequences the work. The cascade emerges from
subscriptions and gate conditions alone.

### 2.2 Why no Blackboard

The Blackboard pattern is appropriate when the solution is unknown upfront and emerges
incrementally from agent contributions — medical diagnosis, speech recognition, document
analysis. This system's source data — customers, orders, complaint history — are known,
structured, queryable facts in MySQL. Agents query them on demand through tools. The
`BlackBoard` class is removed entirely.

The only accumulation need — storing agent findings so Phase 2 agents can read Phase 1 outputs
— is handled by `Findings`, a plain typed dataclass on `Deps`. It is a result accumulator, not
a blackboard.

### 2.3 Agents

There are seven agents. There is no orchestrator. There is no scheduler loop.

| Agent | Phase | Subscribes to | Posts |
|---|---|---|---|
| `PurchaseVerificationAgent` | 1 | `CustomerMessageEvent` | `PurchaseVerifiedEvent` |
| `CustomerHistoryAgent` | 1 | `CustomerMessageEvent` | `CustomerProfileEvent` |
| `ComplaintClassificationAgent` | 1 | `CustomerMessageEvent` | `ComplaintTypeEvent` |
| `SentimentAgent` | 1 | `CustomerMessageEvent` | updates `findings.profile.sentiment_*` |
| `RefundEligibilityAgent` | 2 | `PurchaseVerifiedEvent` + `ComplaintTypeEvent` | `RefundEligibilityEvent` |
| `ResolutionAgent` | 2 | `RefundEligibilityEvent` + `CustomerProfileEvent` | `ResolutionOptionsEvent` |
| `ResponseComposerAgent` | 2 | `ResolutionOptionsEvent` | `CustomerResponseEvent` |

---

## 3. Execution Model

This is the most important section to understand before writing a single line of agent code.
Python's `asyncio` is **single-threaded**. There are no real threads. Parallelism is achieved
by interleaving coroutines at `await` points. Understanding exactly when agents run, when they
yield, and when race conditions are possible determines the entire correctness of the system.

### 3.1 What asyncio.gather() actually does

```python
await asyncio.gather(coro_a(), coro_b(), coro_c())
```

Python schedules all three **coroutines** on the event loop. 
- It starts `coro_a` and runs it until it hits an `await`. At that `await` point, Python suspends `coro_a` and starts `coro_b`. 
- It runs `coro_b` until its first `await`, then suspends it and starts `coro_c`. 
- When any awaited operation completes (e.g. a network response arrives), Python resumes the corresponding
coroutine from where it left off.

The key implication: **coroutines interleave at `await` points, not at arbitrary lines**.
Between two `await` statements, a coroutine runs uninterrupted. No other coroutine can
intervene in that window. 
>[!NOTE]
>This is why Python asyncio is safe from true data races on pure Python objects

>[!WARNING]
>but it is not safe from **logical races** where two coroutines both pass a
>gate check before either has had a chance to post its result.

### 3.2 Phase 1 — Concurrent fan-out

When `bus.publish(CustomerMessageEvent)` is called, the `EventBus` does this:

```python
await asyncio.gather(
    purchase_agent_handler(event),       # wraps purchase_agent.handle(event, deps)
    history_agent_handler(event),        # wraps history_agent.handle(event, deps)
    classification_agent_handler(event), # wraps classification_agent.handle(event, deps)
    sentiment_agent_handler(event),      # wraps sentiment_agent.handle(event, deps)
)
```

All four handlers start. Each immediately calls its pydantic-ai `agent.run()` which internally
makes an async HTTP call to the LLM API. The moment each handler hits `await agent.run(...)`,
it suspends and yields control back to the event loop. The event loop then runs the next handler
until it too hits an `await`. All four LLM HTTP requests are in-flight simultaneously.

Visually:

```
time →

purchase_agent:       [──────── LLM call ──────────────]→ post finding → publish
history_agent:        [──────── LLM call ────────]→ post finding → publish
classification_agent: [──── LLM call ──]→ post finding → publish
sentiment_agent:      [──────── LLM call ──────]→ post finding → publish

                      ^ all start here (asyncio.gather)
```

Each agent finishes in whatever order the LLM API responds. The `asyncio.gather()` call in
`bus.publish()` does not return until **all four** have completed.

**Implication for deps.findings**: Phase 1 agents write to `deps.findings` between their
`await agent.run()` returning and their `await bus.publish(finding)` call. Since Python does
not context-switch between non-`await` lines, each write to `deps.findings` is atomic with
respect to the other coroutines. There is no race condition on writing findings fields in
Phase 1 because each agent writes a different field.

### 3.3 Phase 2 — The cascade inside asyncio.gather

This is where the execution model becomes non-obvious. 
- When a Phase 1 agent posts its finding
and calls `await bus.publish(finding_event)`, that publish call is **nested inside** the outer
`asyncio.gather()`. 
- The Phase 2 handler runs synchronously from the Phase 1 agent's perspective
before the Phase 1 agent's `handle()` returns.

Concretely, when `classification_agent` finishes and calls
`await bus.publish(ComplaintTypeEvent)`, the event loop runs
`refund_agent.handle(ComplaintTypeEvent, deps)` immediately, inside the gather. If
`deps.findings.purchase` is already set (because `purchase_agent` finished first), the gate
passes and `refund_agent` runs its check, posts `RefundEligibilityEvent`, and calls
`await bus.publish(RefundEligibilityEvent)` — which in turn runs `resolution_agent.handle()` —
which may or may not pass its gate — all before `classification_agent.handle()` returns to the
outer gather.

The full nested execution tree when `classification_agent` is the last Phase 1 agent to finish:

```
asyncio.gather() — Phase 1
  │
  ├── purchase_agent.handle()
  │     await agent.run() ...
  │     deps.findings.purchase = PurchaseVerifiedEvent
  │     await bus.publish(PurchaseVerifiedEvent)
  │           └── refund_agent.handle()
  │                 gate: purchase ✓  complaint ✗  → return silently
  │     ← returns to gather
  │
  ├── history_agent.handle()
  │     await agent.run() ...
  │     deps.findings.profile = CustomerProfileEvent
  │     await bus.publish(CustomerProfileEvent)
  │           └── resolution_agent.handle()
  │                 gate: refund_eligibility ✗  → return silently
  │     ← returns to gather
  │
  ├── sentiment_agent.handle()
  │     await agent.run() ...
  │     deps.findings.profile.sentiment_score = score   (in-place update)
  │     ← returns to gather (no publish needed)
  │
  └── classification_agent.handle()   ← finishes last in this scenario
        await agent.run() ...
        deps.findings.complaint_type = ComplaintTypeEvent
        await bus.publish(ComplaintTypeEvent)
              └── refund_agent.handle()
                    gate: purchase ✓  complaint ✓  → FIRES
                    _check() → RefundEligibilityEvent
                    deps.findings.refund_eligibility = RefundEligibilityEvent
                    await bus.publish(RefundEligibilityEvent)
                          └── resolution_agent.handle()
                                gate: refund ✓  profile ✓  complaint ✓  → FIRES
                                await agent.run() ...
                                deps.findings.resolution = ResolutionOptionsEvent
                                await bus.publish(ResolutionOptionsEvent)
                                      └── composer_agent.handle()
                                            await agent.run() ...
                                            deps.findings.response = CustomerResponseEvent
                                            ← returns
                                ← returns
                          ← returns
                    ← returns
              ← returns
        ← returns
  ← gather returns

await bus.publish(message) returns.
deps.findings.response is set.
```

**Key insight**: `await bus.publish(message)` in `main.py` does not return until the entire
cascade — including all Phase 2 agents — has completed. The cascade resolves depth-first,
driven by whichever Phase 1 agent finishes last.

### 3.4 The race condition — and why locks are mandatory

Consider this scenario: `purchase_agent` and `classification_agent` finish at almost the same
time. Both call `await bus.publish(their_finding_event)` in rapid succession. Because these
`await` calls are inside the outer `asyncio.gather()`, the event loop can interleave them:

```
classification_agent:  deps.findings.complaint_type = ComplaintTypeEvent
                       await bus.publish(ComplaintTypeEvent)
                             └── refund_agent.handle()
                                   gate check: purchase ✓  complaint ✓  → PASSES
                                   ← hasn't posted yet, still in _check()

purchase_agent:        (already finished, but its bus.publish triggered refund_agent earlier)
                       await bus.publish(PurchaseVerifiedEvent)
                             └── refund_agent.handle()
                                   gate check: purchase ✓  complaint ✓  → PASSES AGAIN
```

Both invocations of `refund_agent.handle()` pass the gate. `_check()` runs twice.
`RefundEligibilityEvent` is published twice. `ResolutionAgent` fires twice.
`CustomerResponseEvent` is posted twice — second write overwrites first.

This is a **logical race condition**. It cannot happen with true threading because asyncio is
single-threaded, but it can happen because the gate check and the fired flag update are not
atomic across `await` points.

**The fix: asyncio.Lock with a fired flag on every Phase 2 agent.**

The lock ensures only one invocation of `handle()` can be inside the critical section at a
time. The `_fired` flag ensures subsequent invocations exit immediately even after the lock
is released.

```python
class RefundEligibilityAgent(BaseAgent):
    def __init__(self, name: str):
        super().__init__(name, agent=None)
        self._lock = asyncio.Lock()
        self._fired = False

    async def handle(self, event, deps: Deps) -> None:
        async with self._lock:
            if self._fired:
                return
            if deps.findings.purchase is None or deps.findings.complaint_type is None:
                return
            self._fired = True          # set before releasing lock

        # outside the lock — only one invocation ever reaches here
        finding = self._check(
            deps.findings.purchase,
            deps.findings.complaint_type,
            deps.policy,
        )
        deps.findings.refund_eligibility = finding
        await deps.bus.publish(finding)
```

The same lock-and-fired pattern applies to `ResolutionAgent` and `ResponseComposerAgent`.
Every Phase 2 agent must have it. Without it the system produces correct output most of the
time but fails non-deterministically under load.

### 3.5 Phase 2 execution summary

| Agent | Gate condition | Fires when | Lock needed |
|---|---|---|---|
| `RefundEligibilityAgent` | purchase + complaint_type set | Last of the two arrives | Yes |
| `ResolutionAgent` | refund_eligibility + profile + complaint_type set | Last of the three arrives | Yes |
| `ResponseComposerAgent` | ResolutionOptionsEvent received | Immediately on event | Yes (defensive) |

`ResponseComposerAgent` technically only receives one event (`ResolutionOptionsEvent`) and
`ResolutionAgent` fires it exactly once due to its own lock. But the defensive lock is cheap
and makes the invariant explicit.

### 3.6 Complete execution timeline

```
main.py calls await bus.publish(CustomerMessageEvent)
│
├─ asyncio.gather fires all Phase 1 handlers concurrently
│   ├─ All four LLM HTTP requests go out simultaneously
│   ├─ Each awaits its LLM response independently
│   └─ Each posts its finding and publishes its event when done
│
├─ Phase 2 gate checks fire inside Phase 1 publish calls
│   ├─ Early arrivals → gate fails → silent return
│   └─ Final arrival → gate passes → Phase 2 fires
│
├─ RefundEligibilityAgent fires (pure Python, no LLM)
│   └─ Publishes RefundEligibilityEvent
│         └─ ResolutionAgent gate check
│               └─ If profile already posted → ResolutionAgent fires (LLM)
│                     └─ Publishes ResolutionOptionsEvent
│                           └─ ResponseComposerAgent fires (LLM)
│                                 └─ Posts CustomerResponseEvent
│
└─ await bus.publish() returns
   deps.findings.response is set
   main.py reads result and returns
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
├── bus/
│   └── event_bus.py
├── core/
│   ├── deps.py
│   ├── findings.py
│   ├── logger.py
│   └── llm_factory.py
├── db/
│   ├── connection.py          ← Database singleton
│   ├── schema.sql
│   └── repositories/
│       ├── facade.py          ← RepoFacade
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

### 7.1 Findings

**Location**: `core/findings.py`

```python
from dataclasses import dataclass
from schemas.events.findings import (
    PurchaseVerifiedEvent, CustomerProfileEvent, ComplaintTypeEvent,
    RefundEligibilityEvent, ResolutionOptionsEvent, CustomerResponseEvent,
)

@dataclass
class Findings:
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
from core.findings import Findings
from db.repositories.facade import RepoFacade
from schemas.data.policy import Policy

@dataclass
class Deps:
    """Injected into every agent run via RunContext.

    repo         — facade grouping all repositories. Tools call ctx.deps.repo.<repo>.<method>().
    bus          — event bus. Agents publish findings through it.
    findings     — accumulates agent output events for this request.
    policy       — single policy config, loaded once at startup.
    message_id,
    customer_id,
    order_id     — request identifiers for this activation.
    total_tokens — accumulated LLM token usage across all agents for this request.
    """
    repo:         RepoFacade
    bus:          EventBus
    findings:     Findings
    policy:       Policy
    message_id:   str
    customer_id:  str
    order_id:     str
    total_tokens: int = 0
```

### 7.3 EventBus

**Location**: `bus/event_bus.py`

The `EventBus` has one job: fan out. It knows nothing about domains, agents, or findings.
Handlers are registered at startup via `subscribe()`. Each `publish()` call fans the event out
to all registered handlers for that event type via `asyncio.gather()`.

Handlers are registered as **closures that capture `deps`** at subscription time. This keeps
the bus signature clean — `publish(event)` only, no `deps` parameter leaking into the bus.

```python
import asyncio
from collections import defaultdict
from typing import Callable
from pydantic import BaseModel


class EventBus:
    """Pure Observer fan-out. Zero domain knowledge.

    subscribe(event_type, handler):
        Register an async handler for an event type.
        Handler signature: async def handler(event: BaseModel) -> None
        Called once per agent per event type at startup.

    publish(event):
        Fan out to all registered handlers for type(event) via asyncio.gather().
        Does not return until all handlers (and their downstream publishes) complete.
        Handlers for other event types are never called.
    """

    def __init__(self):
        self._subscribers: dict[type, list[Callable]] = defaultdict(list)

    def subscribe(self, event_type: type, handler: Callable) -> None:
        self._subscribers[event_type].append(handler)

    async def publish(self, event: BaseModel) -> None:
        handlers = self._subscribers.get(type(event), [])
        if handlers:
            await asyncio.gather(*[h(event) for h in handlers])
```

**How handlers capture deps via closure in each agent's `subscribe()`:**

```python
# inside any agent's subscribe() method
def subscribe(self, bus: EventBus, deps: Deps) -> None:
    async def handler(event):
        await self.handle(event, deps)   # deps captured here at subscription time
    bus.subscribe(CustomerMessageEvent, handler)
```

The bus calls `handler(event)`. The handler calls `self.handle(event, deps)` with the captured
`deps`. The bus never receives or knows about `deps`.

**What the EventBus does not do:**

- Does not filter events by content
- Does not decide which handlers are relevant
- Does not retry failed handlers
- Does not catch or suppress handler exceptions
- Does not know what a customer, order, or finding is

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
finding to `deps.findings` and publishes it to the bus. No agent knows or cares what other
agents exist.

All Phase 2 agents carry an `asyncio.Lock` and a `_fired` flag. See Section 3.4 for the full
explanation of why this is mandatory.

### 9.2 BaseAgent

**Location**: `agents/base_agent.py`

```python
from abc import ABC, abstractmethod
from pydantic_ai import Agent
from bus.event_bus import EventBus
from core.deps import Deps


class BaseAgent(ABC):
    def __init__(self, name: str, agent: Agent | None):
        self._name = name
        self._agent = agent

    @property
    def name(self) -> str:
        return self._name

    @abstractmethod
    def subscribe(self, bus: EventBus, deps: Deps) -> None: ...

    @abstractmethod
    def get_instruction(self) -> str: ...
```

---

### 9.3 PurchaseVerificationAgent

- **Subscribes to**: `CustomerMessageEvent`
- **Responsibility**: Verify the order exists, belongs to this customer, is delivered, and
  determine days since purchase and what was purchased.
- **Output**: `PurchaseVerifiedEvent` → `deps.findings.purchase`
- **Tools**: `get_order_summary`, `get_order_line_items`, `get_order_total`, `log_decision`
- **Lock**: Not needed — Phase 1 agent, writes a unique findings field.

```python
class PurchaseVerificationAgent(BaseAgent):

    def subscribe(self, bus: EventBus, deps: Deps) -> None:
        async def handler(event):
            await self.handle(event, deps)
            
        bus.subscribe(CustomerMessageEvent, handler)

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

            Call log_decision once. Return a PurchaseVerifiedEvent.
        """

    async def handle(self, event: CustomerMessageEvent, deps: Deps) -> None:
        result = await self._agent.run(
            f"Verify purchase for order {deps.order_id} "
            f"by customer {deps.customer_id}. "
            f"Customer message: {event.message}",
            deps=deps,
            instructions=self.get_instruction(),
        )
        finding: PurchaseVerifiedEvent = result.output
        deps.findings.purchase = finding
        await deps.bus.publish(finding)
```

---

### 9.4 CustomerHistoryAgent

- **Subscribes to**: `CustomerMessageEvent`
- **Responsibility**: Build customer profile — tier, order count, complaint count, repeat issue
  detection. Initialises sentiment to 0.0 for `SentimentAgent` to update.
- **Output**: `CustomerProfileEvent` → `deps.findings.profile`
- **Tools**: `get_customer_profile`, `get_customer_order_count`, `get_recent_complaints`,
  `get_complaint_count`, `log_decision`
- **Lock**: Not needed — Phase 1, unique findings field.

```python
class CustomerHistoryAgent(BaseAgent):

    def subscribe(self, bus: EventBus, deps: Deps) -> None:
        async def handler(event):
            await self.handle(event, deps)
        bus.subscribe(CustomerMessageEvent, handler)

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

            Call log_decision once. Return a CustomerProfileEvent.
        """

    async def handle(self, event: CustomerMessageEvent, deps: Deps) -> None:
        result = await self._agent.run(
            f"Build profile for customer {deps.customer_id}. "
            f"Message context: {event.message}",
            deps=deps,
            instructions=self.get_instruction(),
        )
        finding: CustomerProfileEvent = result.output
        deps.findings.profile = finding
        await deps.bus.publish(finding)
```

---

### 9.5 ComplaintClassificationAgent

- **Subscribes to**: `CustomerMessageEvent`
- **Responsibility**: Classify complaint type, severity, keywords, and explicit intent flags
  from the message text. No database queries needed.
- **Output**: `ComplaintTypeEvent` → `deps.findings.complaint_type`
- **Tools**: `log_decision` only
- **Lock**: Not needed — Phase 1, unique findings field.

```python
class ComplaintClassificationAgent(BaseAgent):

    def subscribe(self, bus: EventBus, deps: Deps) -> None:
        async def handler(event):
            await self.handle(event, deps)
        bus.subscribe(CustomerMessageEvent, handler)

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

            Call log_decision once. Return a ComplaintTypeEvent.
        """

    async def handle(self, event: CustomerMessageEvent, deps: Deps) -> None:
        result = await self._agent.run(
            f"Classify this complaint: {event.message}",
            deps=deps,
            instructions=self.get_instruction(),
        )
        finding: ComplaintTypeEvent = result.output
        deps.findings.complaint_type = finding
        await deps.bus.publish(finding)
```

---

### 9.6 SentimentAgent

- **Subscribes to**: `CustomerMessageEvent`
- **Responsibility**: Score emotional tone independently. Updates profile sentiment fields
  in place if profile is already posted. If not yet posted, the score is held until profile
  arrives — a second subscription on `CustomerProfileEvent` applies the update.
- **Output**: in-place update of `deps.findings.profile.sentiment_score` and `sentiment_label`
- **Tools**: `log_decision` only
- **Lock**: Not needed — in-place field update on an existing object, no publish.

```python
class SentimentAgent(BaseAgent):

    def subscribe(self, bus: EventBus, deps: Deps) -> None:
        self._pending_score = None
        self._pending_label = None

        async def on_message(event):
            await self.handle(event, deps)

        async def on_profile(event):
            # if sentiment finished before profile was posted, apply now
            if self._pending_score is not None:
                event.sentiment_score = self._pending_score
                event.sentiment_label = self._pending_label
                deps.findings.profile = event

        bus.subscribe(CustomerMessageEvent, on_message)
        bus.subscribe(CustomerProfileEvent, on_profile)

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

    async def handle(self, event: CustomerMessageEvent, deps: Deps) -> None:
        result = await self._agent.run(
            f"Score the sentiment of this message: {event.message}",
            deps=deps,
            instructions=self.get_instruction(),
        )
        score = result.output
        if deps.findings.profile is not None:
            deps.findings.profile.sentiment_score = score["sentiment_score"]
            deps.findings.profile.sentiment_label = score["sentiment_label"]
        else:
            # profile not yet posted — hold until on_profile fires
            self._pending_score = score["sentiment_score"]
            self._pending_label = score["sentiment_label"]
```

---

### 9.7 RefundEligibilityAgent

- **Subscribes to**: `PurchaseVerifiedEvent` AND `ComplaintTypeEvent`
- **Responsibility**: Pure Python eligibility check against policy. No LLM. No database.
- **Gate condition**: both `deps.findings.purchase` and `deps.findings.complaint_type` set.
- **Lock**: Mandatory — see Section 3.4. Both subscribed events can arrive near-simultaneously
  and both pass the gate, causing double-firing without the lock.
- **Output**: `RefundEligibilityEvent` → `deps.findings.refund_eligibility`

```python
import asyncio

class RefundEligibilityAgent(BaseAgent):

    def __init__(self, name: str):
        super().__init__(name, agent=None)
        self._lock   = asyncio.Lock()
        self._fired  = False

    def subscribe(self, bus: EventBus, deps: Deps) -> None:
        async def handler(event):
            await self.handle(event, deps)
        bus.subscribe(PurchaseVerifiedEvent, handler)
        bus.subscribe(ComplaintTypeEvent, handler)

    def get_instruction(self) -> str:
        return ""

    async def handle(self, event, deps: Deps) -> None:
        async with self._lock:
            if self._fired:
                return
            if (deps.findings.purchase is None
                    or deps.findings.complaint_type is None):
                return
            self._fired = True       # set inside lock before releasing

        # only one invocation ever reaches here
        finding = self._check(
            deps.findings.purchase,
            deps.findings.complaint_type,
            deps.policy,
        )
        deps.findings.refund_eligibility = finding
        await deps.bus.publish(finding)

    def _check(self, purchase, complaint, policy) -> RefundEligibilityEvent:
        if not purchase.verified:
            return RefundEligibilityEvent(
                message_id=purchase.message_id,
                eligible=False,
                reason="Purchase could not be verified",
            )

        window = policy.refund_window_days
        days   = purchase.days_since_purchase or 0

        if days > window:
            return RefundEligibilityEvent(
                message_id=purchase.message_id,
                eligible=False,
                reason=(
                    f"Purchase is {days} days old. "
                    f"Refund window is {window} days."
                ),
            )

        auto = complaint.complaint_type in policy.auto_refund_complaint_types
        return RefundEligibilityEvent(
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

---

### 9.8 ResolutionAgent

- **Subscribes to**: `RefundEligibilityEvent` AND `CustomerProfileEvent`
- **Gate condition**: `refund_eligibility`, `profile`, and `complaint_type` all set.
- **Lock**: Mandatory — both subscribed events can near-simultaneously pass the gate.
- **Output**: `ResolutionOptionsEvent` → `deps.findings.resolution`
- **Tools**: `log_decision` only — all context from `deps.findings`

```python
class ResolutionAgent(BaseAgent):

    def __init__(self, name: str, agent):
        super().__init__(name, agent)
        self._lock  = asyncio.Lock()
        self._fired = False

    def subscribe(self, bus: EventBus, deps: Deps) -> None:
        async def handler(event):
            await self.handle(event, deps)
        bus.subscribe(RefundEligibilityEvent, handler)
        bus.subscribe(CustomerProfileEvent, handler)

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

            Call log_decision once. Return a ResolutionOptionsEvent.
        """

    async def handle(self, event, deps: Deps) -> None:
        async with self._lock:
            if self._fired:
                return
            if (deps.findings.refund_eligibility is None
                    or deps.findings.profile is None
                    or deps.findings.complaint_type is None):
                return
            self._fired = True

        import json
        context = json.dumps({
            "purchase":           deps.findings.purchase.model_dump()
                                  if deps.findings.purchase else {},
            "profile":            deps.findings.profile.model_dump(),
            "complaint_type":     deps.findings.complaint_type.model_dump(),
            "refund_eligibility": deps.findings.refund_eligibility.model_dump(),
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
        finding: ResolutionOptionsEvent = result.output
        deps.findings.resolution = finding
        await deps.bus.publish(finding)
```

---

### 9.9 ResponseComposerAgent

- **Subscribes to**: `ResolutionOptionsEvent`
- **Responsibility**: Compose the final customer-facing response. All upstream findings are
  guaranteed present when this agent activates.
- **Lock**: Defensive — `ResolutionAgent` fires only once due to its own lock, but the
  defensive lock here makes the invariant explicit and costs nothing.
- **Output**: `CustomerResponseEvent` → `deps.findings.response`
- **Tools**: `get_customer_profile`, `log_decision`

```python
class ResponseComposerAgent(BaseAgent):

    def __init__(self, name: str, agent):
        super().__init__(name, agent)
        self._lock  = asyncio.Lock()
        self._fired = False

    def subscribe(self, bus: EventBus, deps: Deps) -> None:
        async def handler(event):
            await self.handle(event, deps)
        bus.subscribe(ResolutionOptionsEvent, handler)

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

            Call log_decision once. Return a CustomerResponseEvent.
        """

    async def handle(self, event: ResolutionOptionsEvent, deps: Deps) -> None:
        async with self._lock:
            if self._fired:
                return
            self._fired = True

        import json
        context = json.dumps({
            "profile":            deps.findings.profile.model_dump()
                                  if deps.findings.profile else {},
            "complaint_type":     deps.findings.complaint_type.model_dump()
                                  if deps.findings.complaint_type else {},
            "purchase":           deps.findings.purchase.model_dump()
                                  if deps.findings.purchase else {},
            "refund_eligibility": deps.findings.refund_eligibility.model_dump()
                                  if deps.findings.refund_eligibility else {},
            "resolution":         event.model_dump(),
        }, indent=2)

        result = await self._agent.run(
            f"Compose a customer response:\n{context}",
            deps=deps,
            instructions=self.get_instruction(),
        )
        finding: CustomerResponseEvent = result.output
        deps.findings.response = finding
        await deps.bus.publish(finding)
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

from bus.event_bus import EventBus
from core.deps import Deps
from core.findings import Findings
from core.llm_factory import make_model
from core.logger import logger

from db.connection import Database
from db.repositories.facade import RepoFacade
from db.repositories.policy_repo import PolicyRepository

from schemas.events.inbound import CustomerMessageEvent
from schemas.events.findings import (
    PurchaseVerifiedEvent, CustomerProfileEvent, ComplaintTypeEvent,
    RefundEligibilityEvent, ResolutionOptionsEvent, CustomerResponseEvent,
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
    message: CustomerMessageEvent,
    repo: RepoFacade,
    policy,
) -> dict:
    """Handle one customer request end-to-end.

    Creates a fresh EventBus, Findings, and Deps per request.
    Agents are instantiated, subscribed, and the cascade is triggered
    by a single bus.publish(message) call.
    The call does not return until the full cascade completes.
    """
    bus      = EventBus()
    findings = Findings()

    deps = Deps(
        repo=repo,
        bus=bus,
        findings=findings,
        policy=policy,
        message_id=message.message_id,
        customer_id=message.customer_id,
        order_id=message.order_id,
        total_tokens=0,
    )

    # ── Instantiate agents ────────────────────────────────────────────────────
    purchase_agent = PurchaseVerificationAgent(
        name="purchase_verification",
        agent=Agent(
            model=make_model(PROVIDER), deps_type=Deps,
            output_type=PurchaseVerifiedEvent,
            tools=[log_decision, get_order_summary,
                   get_order_line_items, get_order_total],
        ),
    )
    history_agent = CustomerHistoryAgent(
        name="customer_history",
        agent=Agent(
            model=make_model(PROVIDER), deps_type=Deps,
            output_type=CustomerProfileEvent,
            tools=[log_decision, get_customer_profile,
                   get_customer_order_count,
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

    # ── Subscribe all agents — deps captured in closure per agent ─────────────
    #
    # Phase 1 agents subscribe to CustomerMessageEvent.
    # Phase 2 agents subscribe to their respective trigger events.
    # The bus fan-out and gate conditions drive the entire cascade from here.
    # No orchestrator. No scheduler. No polling loop.
    #
    for agent in [
        purchase_agent,
        history_agent,
        classification_agent,
        sentiment_agent,
        refund_agent,
        resolution_agent,
        composer_agent,
    ]:
        agent.subscribe(bus, deps)

    # ── Single publish triggers the entire cascade ────────────────────────────
    #
    # bus.publish(message) calls asyncio.gather() on all Phase 1 handlers.
    # All four LLM calls go out simultaneously.
    # Each agent posts its finding and publishes its event when its LLM responds.
    # Phase 2 gate checks fire inside Phase 1 publish calls (nested awaits).
    # This call does not return until deps.findings.response is set.
    #
    logger.info(f"[{message.message_id}] cascade start")
    await bus.publish(message)
    logger.info(f"[{message.message_id}] cascade complete")

    response = deps.findings.response
    return {
        "resolved":      response.resolved      if response else False,
        "response":      response.response       if response else "System error",
        "actions_taken": response.actions_taken  if response else [],
        "total_tokens":  deps.total_tokens,
    }


async def main():
    # Pool initialised once here. All repos call Database.get_pool() and
    # receive this same pool via the singleton. No pool passed to repos.
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

## 11. Implementation Sequence

**Phase 1 — Database** (`db/`)

Run `schema.sql`. Verify all tables and seed data. Test `Database.get_pool()` returns the same
pool object on repeated calls — confirming singleton behaviour. Test each repository method
directly: `get_order` with mismatched `customer_id` returns `None`, `get_history` with
`limit=10` never exceeds 10 rows, `PolicyRepository.get()` returns a valid `Policy` object.

**Phase 2 — Schemas** (`schemas/`)

Instantiate each schema with dummy data, round-trip `model_dump()` / `model_validate()`.
All event schemas carry `message_id`. No schema has methods.

**Phase 3 — EventBus** (`bus/event_bus.py`)

Verify fan-out with a standalone script: three dummy async handlers subscribed to the same
event type, one `publish()` call, confirm all three fire concurrently. Confirm handlers for
other event types are not called.

**Phase 4 — Core** (`core/`)

Build `Findings` and `Deps`. Confirm `Findings.is_complete()` is `False` until `response`
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
`CustomerHistoryAgent` → `SentimentAgent`. For each: subscribe to a live bus with deps,
call `bus.publish(CustomerMessageEvent)`, confirm finding posted to `deps.findings` and
published to bus.

**Phase 8 — Phase 2 agents** (`agents/`)

Build in order: `ResolutionAgent` → `ResponseComposerAgent`. Confirm gate exits silently when
findings missing. With all required findings set, confirm agent activates, calls LLM, posts
finding. Manually test lock: call `handle()` twice in quick succession, confirm second call
is a no-op.

**Phase 9 — Integration** (`main.py`)

Run end-to-end with sample message. Confirm: Phase 1 agents activate concurrently,
`log_decision` lines appear for all LLM agents, `RefundEligibilityAgent` produces no token
lines, `CustomerResponseEvent` is the final output with `resolved=True`.

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