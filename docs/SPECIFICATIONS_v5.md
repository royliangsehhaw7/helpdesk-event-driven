# Multi-Agent Customer Service System
## Specification v5.0 — Observer / Event-Driven Pattern with Conversational Intake and Tool-Based Data Access

---

## 1. Purpose

A learning-oriented multi-agent system that handles customer service requests end-to-end,
including a conversational intake phase that collects context before resolution begins.

The primary goal is to understand and implement two complementary patterns in one system:

- **Sequential conversational intake** — a single LLM agent holds a multi-turn chat with
  the customer, accumulates context across turns, and signals when it has enough to proceed.
- **Observer pattern with concurrent agent fan-out** — once intake is complete, a single
  publish call triggers a cascade of specialised agents that work concurrently, each
  reacting to typed contracts rather than being told what to do.

The system is built on pydantic-ai's `Agent`, `deps`, and `tools` features and uses
`asyncio.gather()` for concurrent execution within the resolution cascade.

---

## 2. Architecture

### 2.1 Two-phase design

The system is split into two distinct phases with a hard boundary between them.

```
Customer
   │
   │  turn 1: "hi, I have a problem with an order"
   │  turn 2: "it's ORD-1001"
   │  turn 3: "the keyboard arrived crushed and a key doesn't work, I want a refund"
   ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│  Phase 1 — Conversational Intake                                                  │
│                                                                                   │
│  IntakeAgent holds a multi-turn chat with the customer.                           │
│  Asks for order_id and a full complaint description. One question at a time.      │
│  Grows its own _history across turns via pydantic-ai's message_history parameter. │
│  Returns IntakeResult on every turn. When ready=True, hands off.                  │
└──────────────────────────┬────────────────────────────────────────────────────────┘
                           │  ready=True
                           │  order_id="ORD-1001"
                           │  message="Customer received a keyboard with
                           │           crushed packaging and a faulty key.
                           │           Wants a refund or complaint filed."
                           ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│  Phase 2 — Resolution Cascade                                                     │
│                                                                                   │
│  CustomerServiceHandler builds a CustomerMessage and fires one publish() call.    │
│  Seven agents fan out concurrently via the MessageHub.                            │ 
│  Each reacts to typed contracts. No orchestrator. No scheduler.                   │
└───────────────────────────────────────────────────────────────────────────────────┘
```

The two phases never overlap. Phase 2 does not start until `IntakeAgent` signals
`ready=True`. `IntakeAgent` has no knowledge of `MessageHub`, `Deps`, `Blackboard`, or
any resolution agent.

---

### 2.2 Pattern — Observer with typed contracts

The resolution cascade implements the **Observer pattern**. Its mechanism in this system
is a `MessageHub` — a dictionary that maps contract types to lists of async handler
functions.

In the classical Observer pattern, subjects maintain a registry of observers and notify
them when something changes. Here:

- **The subject** is `MessageHub`. It maintains the registry and notifies observers.
- **The observers** are the resolution agents. Each registers a handler for one or more
  contract types via `subscribe()`.
- **The notification** is `hub.publish(contract)` — the hub calls all handlers registered
  for that contract type, concurrently, via `asyncio.gather()`.

The classical pattern typically passes a single mutable subject to all observers. This
system passes **typed, immutable contracts** instead — each publish carries a fully
populated Pydantic `BaseModel` that defines exactly what is known at that point. This
makes the notification payload explicit, validated, and self-documenting.

#### What a contract is

A contract is a Pydantic `BaseModel` that serves two roles simultaneously:

1. **The typed message** that travels through the hub — `hub.publish(contract)` routes
   it to the correct handlers by `type(contract)`.
2. **The output schema** that constrains what the LLM must produce — pydantic-ai's
   `output_type=PurchaseResult` enforces the shape before the agent's `handle()` method
   ever sees the result.

One class, two jobs. The LLM produces it; the hub routes it; downstream agents read it
from `deps.board`.

#### What the MessageHub is

The `MessageHub` is a dictionary and an `asyncio.gather()` call. Nothing more.

```python
# State of the hub after all subscribe() calls:
{
    CustomerMessage:  [purchase_handler, history_handler,
                       classification_handler, sentiment_handler],
    PurchaseResult:   [refund_handler],
    ComplaintResult:  [refund_handler],
    ProfileResult:    [resolution_handler],
    RefundResult:     [resolution_handler],
    ResolutionResult: [composer_handler],
}

# publish() does exactly this:
async def publish(self, contract: BaseModel) -> None:
    handlers = self._subscribers.get(type(contract), [])
    if handlers:
        await asyncio.gather(*[h(contract) for h in handlers])
```

The hub has zero domain knowledge. It does not know what a customer is, what a refund
is, or what any contract contains. It only knows contract types and handler lists.

#### Why this is still called event-driven

Renaming `EventBus` → `MessageHub` and `Event` → `Contract` does not change the pattern.
The contracts are events in the architectural sense — each one signals that something
has been determined (`PurchaseResult` = "purchase has been verified",
`ComplaintResult` = "complaint has been classified"). The cascade is driven by these
signals, not by a scheduler or orchestrator. The terms "event-driven" and "Observer
pattern" both apply and are used interchangeably in this specification.

The term "event loop" is reserved exclusively for Python's `asyncio` event loop and is
never used to describe the hub's dispatch mechanism.

---

### 2.3 Why no Blackboard

The Blackboard pattern is appropriate when the solution is unknown upfront and emerges
incrementally from agent contributions — medical diagnosis, speech recognition, document
analysis. This system's source data — customers, orders, complaint history — are known,
structured, queryable facts in MySQL. Agents query them on demand through tools. The
`BlackBoard` class is removed entirely.

The only accumulation need — storing agent findings so downstream agents can read them —
is handled by `Blackboard`, a plain typed dataclass on `Deps`. It is a result accumulator,
not a blackboard.

---

### 2.4 Responsibility boundaries

Three classes own the system's top-level concerns. They do not overlap.

| Class | Location | Responsibility |
|---|---|---|
| `IntakeAgent` | `agents/intake_agent.py` | Multi-turn chat with customer. Collects `order_id` and complete complaint description. Signals `ready=True` when done. No hub, no deps, no findings. |
| `CustomerServiceHandler` | `service/handler.py` | Builds agents once at startup. Per request: creates `MessageHub`, `Blackboard`, `Deps`; resets stateful agents; subscribes all agents; fires the single `publish()` call; returns the result dict. |
| `MessageHub` | `core/message_hub.py` | Pure fan-out. Maps contract types to handler lists. Calls `asyncio.gather()` on publish. Zero domain knowledge. |

---

### 2.5 Agents

There are eight agents in total. One handles intake. Seven handle resolution.

**Intake agent** — outside the hub, outside `Deps`.

| Agent | Role |
|---|---|
| `IntakeAgent` | Conversational. Holds `_history` across turns. Returns `IntakeResult` each turn. No LLM `output_type` constraint beyond `IntakeResult`. Discarded at session end. |

**Resolution agents** — subscribed to the hub, receive `Deps`.

| Agent | Subscribes to | Posts |
|---|---|---|
| PurchaseVerificationAgent | CustomerMessage | PurchaseResult |
| CustomerHistoryAgent | CustomerMessage | ProfileResult |
| ComplaintClassificationAgent | CustomerMessage | ComplaintResult |
| SentimentAgent | CustomerMessage | updates board.profile.sentiment_* in place |
| RefundEligibilityAgent | PurchaseResult + ComplaintResult` | RefundResult |
| ResolutionAgent | RefundResult + ProfileResult | ResolutionResult |
| ResponseComposerAgent | ResolutionResult | CustomerResponse |

`RefundEligibilityAgent` is pure Python — no LLM, no tools. It applies policy rules
directly against `deps.board`.

---

### 2.6 Contract naming convention

Contracts follow two naming rules based on how they are used:

**Noun payload** — used when the contract is passed in as a parameter (an inbound
trigger, intake output, or a terminal output that nobody subscribes to):

| Contract | Role |
|---|---|
| `CustomerMessage` | Inbound trigger. Enters the hub as the first publish call. |
| `CustomerResponse` | Terminal output. Nobody subscribes to it — it ends the cascade. |
| `IntakeResult` | Intake output. Signals ready state and carries collected context. |

**Result suffix** — used when the contract is the output of a resolution agent:

| Contract | Produced by |
|---|---|
| `PurchaseResult` | `PurchaseVerificationAgent` |
| `ProfileResult` | `CustomerHistoryAgent` |
| `ComplaintResult` | `ComplaintClassificationAgent` |
| `RefundResult` | `RefundEligibilityAgent` |
| `ResolutionResult` | `ResolutionAgent` |

The `Contract` suffix is not used. Names live in `schemas/contracts/` — the location
already makes their role clear.

---

### 2.7 Per-request vs shared state

`CustomerServiceHandler` is built once and reused across all customer requests. Agents
and their underlying pydantic-ai `Agent` instances are also built once in
`_build_agents()` — they carry no per-request state themselves.

Per-request state is isolated inside `handle()`:

| Object | Scope | Why |
|---|---|---|
| `MessageHub` | Per request | Handler lists must be built fresh on every `subscribe()` call. |
| `Blackboard` | Per request | Accumulates this request's agent outputs only. |
| `Deps` | Per request | Carries `message_id`, `customer_id`, `order_id`, `total_tokens`, and references to the per-request hub and findings. |

Agents that carry `_fired` (`RefundEligibilityAgent`, `ResolutionAgent`,
`ResponseComposerAgent`) must be reset before each request's subscribe loop. See
Section 3.5.

`IntakeAgent` is per-session — it holds `_history` across turns for one customer
conversation and is discarded when the session ends.

---

## 3. Execution Model

### 3.1 Intake phase — sequential multi-turn

`IntakeAgent` uses pydantic-ai's native `message_history` parameter to maintain
conversation state across turns. On each call to `collect()`:

```python
result = await self._agent.run(
    user_input,
    message_history=self._history,
    instructions=self._get_instruction(customer_id),
)
self._history = result.all_messages()
return result.output   # IntakeResult
```

The LLM sees the full conversation thread on every turn. `IntakeResult.ready` is
`False` until the agent has collected both `order_id` and a complete complaint
description, at which point it sets `ready=True` and populates `message` with a clean
2-4 sentence summary of the full conversation.

`main.py` loops on `ready=False`, printing `intake_result.reply` to the customer each
turn. When `ready=True`, it constructs a `CustomerMessage` and hands it to
`CustomerServiceHandler.handle()`. The intake loop ends.

```
Customer: "hi, I have a problem with an order"
Agent:    "I'm sorry to hear that. Could you share your order number?"   ← ready=False

Customer: "ORD-1001"
Agent:    "Thanks. Could you describe what happened with the order?"      ← ready=False

Customer: "packaging was crushed and a key isn't working. I want a refund."
Agent:    "Got it, I'm looking into that for you now."                    ← ready=True
                                                                            → hand off
```

---

### 3.2 What the MessageHub actually is

The `MessageHub` is a dictionary and an `asyncio.gather()` call. Nothing more.

```python
# The entire state of the hub after all subscribe() calls:
{
    CustomerMessage:  [purchase_handler, history_handler, classification_handler, sentiment_handler],
    PurchaseResult:   [refund_handler],
    ComplaintResult:  [refund_handler],
    ProfileResult:    [resolution_handler],
    RefundResult:     [resolution_handler],
    ResolutionResult: [composer_handler],
}

# publish() does exactly this:
async def publish(self, contract: BaseModel) -> None:
    handlers = self._subscribers.get(type(contract), [])
    if handlers:
        await asyncio.gather(*[h(contract) for h in handlers])
```

- When `publish(CustomerMessage)` is called
  - the hub looks up `CustomerMessage`
    - finds 4 handlers, and calls `asyncio.gather()` on all 4.
- When `publish(PurchaseResult)` is called
  - the hub looks up `PurchaseResult`
    - finds 1 handler, and calls it.

---

### 3.3 What asyncio.gather() actually does

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

---

### 3.4 The correct execution order

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

---

### 3.5 Step by step execution

**Step 1 — the subscribe loop builds the dictionary**

```python
for agent in self._agents:
    agent.reset()           # clears _fired on stateful agents — no-op on others
    agent.subscribe(bus, deps)
```

Each agent's `subscribe()` method calls `hub.subscribe(ContractType, handler)` which
appends the handler to the dictionary list for that contract type. Nothing runs. The
dictionary is just built.

**Step 2 — the single trigger**

```python
await bus.publish(message)  # message is CustomerMessage
```

Inside `bus.publish()`:

```python
handlers = self._subscribers.get(type(contract), [])
# handlers = [purchase_handler, history_handler, classification_handler, sentiment_handler]
await asyncio.gather(*[h(contract) for h in handlers])
```

All 4 handlers start. Each one immediately hits `await self._agent.run(...)` inside its
`handle()` method and suspends. All 4 LLM calls are now in flight simultaneously.

**Step 3 — say ComplaintClassificationAgent LLM responds first**

Its `handle()` resumes from `await self._agent.run(...)`:

```python
async def handle(self, contract: CustomerMessage, deps: Deps) -> None:
    result = await self._agent.run(...)           # resumes here
    finding: ComplaintResult = result.output
    deps.board.complaint_type = finding        # stores finding
    await deps.bus.publish(finding)               # publishes ComplaintResult
```

`bus.publish(finding)` looks up `ComplaintResult`. Finds `[refund_handler]`. Calls
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

**Step 4 — say PurchaseVerificationAgent LLM responds next**

Its `handle()` resumes:

```python
async def handle(self, contract: CustomerMessage, deps: Deps) -> None:
    result = await self._agent.run(...)        # resumes here
    finding: PurchaseResult = result.output
    deps.board.purchase = finding           # stores finding
    await deps.bus.publish(finding)            # publishes PurchaseResult
```

`bus.publish(finding)` looks up `PurchaseResult`. Finds `[refund_handler]`. Calls
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
    await deps.bus.publish(finding)            # publishes RefundResult
```

`bus.publish(finding)` looks up `RefundResult`. Finds `[resolution_handler]`. Calls
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

**Step 5 — CustomerHistoryAgent LLM responds**

Its `handle()` resumes:

```python
async def handle(self, contract: CustomerMessage, deps: Deps) -> None:
    result = await self._agent.run(...)        # resumes here
    finding: ProfileResult = result.output
    deps.board.profile = finding            # stores finding
    await deps.bus.publish(finding)            # publishes ProfileResult
```

`bus.publish(finding)` looks up `ProfileResult`. Finds `[resolution_handler]`. Calls
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
    finding: ResolutionResult = result.output
    deps.board.resolution = finding
    await deps.bus.publish(finding)            # publishes ResolutionResult
```

`bus.publish(finding)` looks up `ResolutionResult`. Finds `[composer_handler]`. Calls
`composer_agent.handle()`:

```python
async def handle(self, contract: ResolutionResult, deps: Deps) -> None:
    async with self._lock:
        if self._fired:
            return
        self._fired = True

    result = await self._agent.run(...)
    finding: CustomerResponse = result.output
    deps.board.response = finding
    await deps.bus.publish(finding)            # publishes CustomerResponse
```

`bus.publish(finding)` looks up `CustomerResponse`. Nobody subscribed. `handlers` is `[]`.
Does nothing. `composer_agent.handle()` is done.

Control unwinds: back through `resolution_agent.handle()`, back through `history_agent.handle()`
which is now done. `sentiment_agent` finishes around this time too. The original `asyncio.gather()`
has all 4 coroutines done. Returns. `await bus.publish(message)` in `CustomerServiceHandler.handle()`
returns. `deps.board.response` is set.

**Step 6 — CustomerServiceHandler reads the result**

```python
await bus.publish(message)
# returns here — entire cascade is complete

response = deps.board.response
return {
    "resolved":      response.resolved,
    "response":      response.response,
    "actions_taken": response.actions_taken,
    "total_tokens":  deps.total_tokens,
}
```

---

### 3.6 The race condition — and why locks are mandatory

Consider this scenario: `purchase_agent` and `classification_agent` finish at almost the same
time. Both call `await deps.bus.publish(their_finding)` in rapid succession. The event loop
can interleave them:

```
classification_agent:  deps.board.complaint_type = ComplaintResult(...)
                       await bus.publish(ComplaintResult)
                             └── refund_agent.handle()
                                   gate check: purchase ✓  complaint ✓  → PASSES
                                   ← still inside _check(), hasn't set _fired yet

purchase_agent:        await bus.publish(PurchaseResult)
                             └── refund_agent.handle()
                                   gate check: purchase ✓  complaint ✓  → PASSES AGAIN
```

Both invocations pass the gate. `_check()` runs twice. `RefundResult` is published
twice. `ResolutionResult` fires twice. `CustomerResponse` is written twice.

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
    await deps.bus.publish(finding)
```

The lock ensures only one invocation can be inside the critical section at a time. The `_fired`
flag ensures subsequent invocations exit immediately even after the lock is released.

---

### 3.7 Gate conditions per agent

| Agent | Gate condition | Lock needed |
|---|---|---|
| `RefundEligibilityAgent` | `purchase` + `complaint_type` both set | Yes |
| `ResolutionAgent` | `refund_eligibility` + `profile` + `complaint_type` all set | Yes |
| `ResponseComposerAgent` | receives `ResolutionResult` | Yes (defensive) |

`ResponseComposerAgent` technically only receives one contract and `ResolutionAgent` fires it
exactly once due to its own lock. But the defensive lock is cheap and makes the invariant explicit.

---

### 3.8 Agent reset between requests

`RefundEligibilityAgent`, `ResolutionAgent`, and `ResponseComposerAgent` carry `_fired` on
the instance. Because agents are built once and reused across requests, `_fired = True` from
request 1 would cause the agent to silently exit on request 2.

`BaseAgent` exposes a `reset()` method — no-op by default. Agents that carry `_fired`
override it:

```python
def reset(self) -> None:
    self._fired = False
```

`CustomerServiceHandler.handle()` calls `reset()` on every agent before the subscribe loop:

```python
for agent in self._agents:
    agent.reset()           # clears _fired — no-op on agents without it
    agent.subscribe(bus, deps)
```

---

## 4. Project Structure

```
customer_service/
├── agents/
│   ├── base_agent.py
│   ├── intake_agent.py                  ← conversational intake, outside the hub
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
│   ├── data/                            ← database-mapped Pydantic models
│   │   ├── customer.py
│   │   ├── order.py
│   │   ├── complaint.py
│   │   └── policy.py
│   └── contracts/                       ← hub-routed contracts
│       ├── customer_message.py          ← inbound trigger (noun payload)
│       ├── intake_result.py             ← intake output (noun payload)
│       ├── purchase_result.py           ← PurchaseVerificationAgent output
│       ├── profile_result.py            ← CustomerHistoryAgent output
│       ├── complaint_result.py          ← ComplaintClassificationAgent output
│       ├── refund_result.py             ← RefundEligibilityAgent output
│       ├── resolution_result.py         ← ResolutionAgent output
│       └── customer_response.py         ← terminal output (noun payload)
├── service/
│   └── handler.py                       ← CustomerServiceHandler
├── tools/
│   ├── agent_logger.py
│   ├── customer_tools.py
│   ├── order_tools.py
│   └── complaint_tools.py
└── main.py
```

`service/` sits alongside `agents/`, `core/`, and `tools/` as a peer. It contains
`CustomerServiceHandler` — the one class that assembles the resolution cascade and drives
a single request to completion. It is domain logic, not infrastructure, and does not
belong in `core/`.

`schemas/data/` holds the database-mapped Pydantic models (`Customer`, `SalesOrder`,
`Policy`, etc.). `schemas/contracts/` holds the hub-routed contracts. The split prevents
naming collisions — `ProfileResult` (contract) and `Customer` (data schema) are
unambiguous because they live in different subpackages.

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

### 6.2 Contract Schemas

**`schemas/contracts/customer_message.py`**
```python
from pydantic import BaseModel

class CustomerMessage(BaseModel):
    message_id: str    # uuid — ties all findings for this request together
    customer_id: str
    order_id: str
    message: str       # full summarised complaint from IntakeAgent
    timestamp: str
```

**`schemas/contracts/intake_result.py`**
```python
from pydantic import BaseModel

class IntakeResult(BaseModel):
    """Output contract for IntakeAgent.

    ready=False — still gathering. reply is the next question to ask the customer.
                  order_id and message are None.
    ready=True  — enough context collected. reply is a brief handoff acknowledgement.
                  order_id and message are fully populated.
    """
    ready:    bool
    reply:    str             # always set — shown to customer every turn
    order_id: str | None = None
    message:  str | None = None   # full summarised complaint, set when ready=True
```

**`schemas/contracts/purchase_result.py`**
```python
from pydantic import BaseModel

class PurchaseResult(BaseModel):
    message_id: str
    verified: bool
    order_date: str | None = None
    product_name: str | None = None
    product_category: str | None = None
    days_since_purchase: int | None = None
    order_total: float | None = None
    reason: str | None = None
```

**`schemas/contracts/profile_result.py`**
```python
from pydantic import BaseModel

class ProfileResult(BaseModel):
    message_id: str
    customer_id: str
    tier: str
    total_orders: int
    previous_complaints: int
    is_repeat_issue: bool
    sentiment_score: float = 0.0
    sentiment_label: str = "neutral"
```

**`schemas/contracts/complaint_result.py`**
```python
from pydantic import BaseModel

class ComplaintResult(BaseModel):
    message_id: str
    complaint_type: str        # "packaging"|"product"|"delivery"|"billing"
    severity: str              # "low"|"medium"|"high"
    keywords: list[str]
    wants_refund: bool
    wants_replacement: bool
    wants_complaint_filed: bool
```

**`schemas/contracts/refund_result.py`**
```python
from pydantic import BaseModel

class RefundResult(BaseModel):
    message_id: str
    eligible: bool
    reason: str
    refund_amount: float | None = None
    extended_due_to_tier: bool = False
```

**`schemas/contracts/resolution_result.py`**
```python
from pydantic import BaseModel

class ResolutionResult(BaseModel):
    message_id: str
    options: list[str]
    recommended: str
    escalate_to_human: bool = False
    escalation_reason: str | None = None
```

**`schemas/contracts/customer_response.py`**
```python
from pydantic import BaseModel

class CustomerResponse(BaseModel):
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
from schemas.contracts.purchase_result import PurchaseResult
from schemas.contracts.profile_result import ProfileResult
from schemas.contracts.complaint_result import ComplaintResult
from schemas.contracts.refund_result import RefundResult
from schemas.contracts.resolution_result import ResolutionResult
from schemas.contracts.customer_response import CustomerResponse

@dataclass
class Blackboard:
    """Result accumulator for one request lifecycle.

    Agents write here after their LLM call completes.
    Downstream agents read here to check gate conditions.
    One instance per CustomerMessage. Discarded when complete.
    """
    purchase:           PurchaseResult  | None = None
    profile:            ProfileResult   | None = None
    complaint_type:     ComplaintResult | None = None
    refund_eligibility: RefundResult    | None = None
    resolution:         ResolutionResult| None = None
    response:           CustomerResponse| None = None

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
    findings     — accumulates agent output contracts for this request.
    policy       — single policy config, loaded once at startup.
    message_id,
    customer_id,
    order_id     — request identifiers for this activation.
    total_tokens — accumulated LLM token usage across all agents for this request.
    """
    repo:         RepoFacade
    hub:          MessageHub
    board:        Blackboard
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
def subscribe(self, bus: MessageHub, deps: Deps) -> None:
    async def handler(contract):
        await self.handle(contract, deps)   # deps captured here at subscription time
    bus.subscribe(CustomerMessage, handler)
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
3.6 for the full explanation of why this is mandatory, and Section 3.8 for the reset requirement.

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

    def reset(self) -> None:
        """Called before each request's subscribe loop.
        No-op by default. Agents that carry _fired override this."""
        pass

    @abstractmethod
    def subscribe(self, bus: MessageHub, deps: Deps) -> None: ...

    @abstractmethod
    def get_instruction(self) -> str: ...
```

### 9.3 IntakeAgent

**Location**: `agents/intake_agent.py`

- **Role**: Conversational intake. Sits entirely outside the observer cascade.
- **Responsibility**: Hold a multi-turn chat with the customer until both `order_id` and a
  complete complaint description have been collected. One question at a time. Never guess
  or assume an `order_id`. Summarise the full conversation into `message` when `ready=True`.
- **Output**: `IntakeResult` — returned directly to the caller on every turn, never published
  to the hub.
- **State**: Holds `_history` (pydantic-ai message history) across turns. Per-session — a new
  `IntakeAgent` is created for each customer session.
- **No `Deps`**: Does not participate in the hub. Has no access to `Blackboard`, `RepoFacade`,
  or `Policy`.

```python
import logging
from pydantic_ai import Agent

from core.llm_factory import make_model
from schemas.contracts.intake_result import IntakeResult

logger = logging.getLogger(__name__)

PROVIDER = "openai:gpt-4o-mini"


class IntakeAgent:
    """Conversational intake agent. Sits in front of CustomerServiceHandler.

    Holds a multi-turn conversation with the customer until it has collected
    the three things the resolution cascade requires:

        - order_id     — extracted from what the customer says
        - message      — a complete, coherent description of the complaint

    customer_id is already known from the session and is not collected here.

    On each turn, collect() returns an IntakeResult:
        ready=False → still gathering; show reply to the customer and wait
        ready=True  → hand off to CustomerServiceHandler with order_id + message

    The agent never touches MessageHub, Deps, or Blackboard. It is entirely
    outside the observer cascade and has no knowledge of it.
    """

    def __init__(self) -> None:
        self._agent   = Agent(
            model=make_model(PROVIDER),
            output_type=IntakeResult,
        )
        self._history = []   # pydantic-ai message history — grows across turns

    def _get_instruction(self, customer_id: str) -> str:
        return f"""
            You are a customer service intake agent for an e-commerce platform.
            The customer's ID is {customer_id}. Do not ask for it.

            Your only job is to gather enough information to handle the customer's
            complaint. You need exactly two things:

            1. order_id  — the order reference number (format: ORD-XXXX).
                           Ask for it if the customer has not provided it.
            2. message   — a complete, self-contained description of the complaint
                           that covers: what happened, which product or order,
                           and what outcome the customer is seeking (refund,
                           replacement, complaint filed, etc.).

            Rules:
            - Ask for one thing at a time. Never ask two questions in one reply.
            - Be warm, concise, and professional. Do not use jargon.
            - Once you have both order_id and a complete complaint description,
              set ready=True. Summarise the full complaint into message in plain
              language (2-4 sentences). Set reply to a brief acknowledgement
              that you are looking into it now.
            - If you do not yet have both, set ready=False and set reply to
              your next question. Leave order_id and message as null.
            - Never make up or assume an order_id. If the customer is vague
              ("my last order", "order from last week"), ask them to confirm
              the order number.
            - Do not attempt to resolve the complaint yourself. Do not offer
              refunds, decisions, or outcomes. Your job ends when ready=True.
        """

    async def collect(self, user_input: str, customer_id: str) -> IntakeResult:
        """Process one customer turn. Returns IntakeResult.

        Call repeatedly until result.ready is True, then hand off to
        CustomerServiceHandler using result.order_id and result.message.
        """
        logger.info(f"[intake] customer={customer_id} input='{user_input[:60]}...'")

        result = await self._agent.run(
            user_input,
            message_history=self._history,
            instructions=self._get_instruction(customer_id),
        )

        self._history = result.all_messages()

        logger.info(
            f"[intake] ready={result.output.ready} "
            f"order_id={result.output.order_id}"
        )

        return result.output
```

### 9.4 PurchaseVerificationAgent

- **Subscribes to**: `CustomerMessage`
- **Responsibility**: Verify the order exists, belongs to this customer, is delivered, and
  determine days since purchase and what was purchased.
- **Output**: `PurchaseResult` → `deps.board.purchase`
- **Tools**: `get_order_summary`, `get_order_line_items`, `get_order_total`, `log_decision`
- **Lock**: Not needed — writes a unique findings field, never fires twice for the same field.

```python
class PurchaseVerificationAgent(BaseAgent):

    def subscribe(self, bus: MessageHub, deps: Deps) -> None:
        async def handler(contract):
            await self.handle(contract, deps)
        bus.subscribe(CustomerMessage, handler)

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

            Call log_decision once. Return a PurchaseResult.
        """

    async def handle(self, contract: CustomerMessage, deps: Deps) -> None:
        result = await self._agent.run(
            f"Verify purchase for order {deps.order_id} "
            f"by customer {deps.customer_id}. "
            f"Customer message: {contract.message}",
            deps=deps,
            instructions=self.get_instruction(),
        )
        finding: PurchaseResult = result.output
        deps.board.purchase = finding
        await deps.bus.publish(finding)
```

### 9.5 CustomerHistoryAgent

- **Subscribes to**: `CustomerMessage`
- **Responsibility**: Build customer profile — tier, order count, complaint count, repeat issue
  detection. Initialises sentiment to 0.0 for `SentimentAgent` to update.
- **Output**: `ProfileResult` → `deps.board.profile`
- **Tools**: `get_customer_profile`, `get_customer_order_count`, `get_recent_complaints`,
  `get_complaint_count`, `log_decision`
- **Lock**: Not needed — writes a unique findings field.

```python
class CustomerHistoryAgent(BaseAgent):

    def subscribe(self, bus: MessageHub, deps: Deps) -> None:
        async def handler(contract):
            await self.handle(contract, deps)
        bus.subscribe(CustomerMessage, handler)

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

            Call log_decision once. Return a ProfileResult.
        """

    async def handle(self, contract: CustomerMessage, deps: Deps) -> None:
        result = await self._agent.run(
            f"Build profile for customer {deps.customer_id}. "
            f"Message context: {contract.message}",
            deps=deps,
            instructions=self.get_instruction(),
        )
        finding: ProfileResult = result.output
        deps.board.profile = finding
        await deps.bus.publish(finding)
```

### 9.6 ComplaintClassificationAgent

- **Subscribes to**: `CustomerMessage`
- **Responsibility**: Classify complaint type, severity, keywords, and explicit intent flags
  from the message text. No database queries needed.
- **Output**: `ComplaintResult` → `deps.board.complaint_type`
- **Tools**: `log_decision` only
- **Lock**: Not needed — writes a unique findings field.

```python
class ComplaintClassificationAgent(BaseAgent):

    def subscribe(self, bus: MessageHub, deps: Deps) -> None:
        async def handler(contract):
            await self.handle(contract, deps)
        bus.subscribe(CustomerMessage, handler)

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

            Call log_decision once. Return a ComplaintResult.
        """

    async def handle(self, contract: CustomerMessage, deps: Deps) -> None:
        result = await self._agent.run(
            f"Classify this complaint: {contract.message}",
            deps=deps,
            instructions=self.get_instruction(),
        )
        finding: ComplaintResult = result.output
        deps.board.complaint_type = finding
        await deps.bus.publish(finding)
```

### 9.7 SentimentAgent

- **Subscribes to**: `CustomerMessage`
- **Responsibility**: Score emotional tone independently. Updates profile sentiment fields
  in place if profile is already posted. If not yet posted, the score is held until profile
  arrives — a second subscription on `ProfileResult` applies the update.
- **Output**: in-place update of `deps.board.profile.sentiment_score` and `sentiment_label`
- **Tools**: `log_decision` only
- **Lock**: Not needed — in-place field update on an existing object, no publish.

```python
class SentimentAgent(BaseAgent):

    def subscribe(self, bus: MessageHub, deps: Deps) -> None:
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

        bus.subscribe(CustomerMessage, on_message)
        bus.subscribe(ProfileResult, on_profile)

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

    async def handle(self, contract: CustomerMessage, deps: Deps) -> None:
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

### 9.8 RefundEligibilityAgent

- **Subscribes to**: `PurchaseResult` AND `ComplaintResult`
- **Responsibility**: Pure Python eligibility check against policy. No LLM. No database.
- **Gate condition**: both `deps.board.purchase` and `deps.board.complaint_type` set.
- **Lock**: Mandatory — both subscribed contracts can arrive and call this handler before either
  has had a chance to set `_fired`, causing it to run twice without the lock.
- **Output**: `RefundResult` → `deps.board.refund_eligibility`

```python
import asyncio

class RefundEligibilityAgent(BaseAgent):

    def __init__(self, name: str):
        super().__init__(name, agent=None)
        self._lock   = asyncio.Lock()
        self._fired  = False

    def reset(self) -> None:
        self._fired = False

    def subscribe(self, bus: MessageHub, deps: Deps) -> None:
        async def handler(contract):
            await self.handle(contract, deps)
        bus.subscribe(PurchaseResult, handler)
        bus.subscribe(ComplaintResult, handler)

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
        await deps.bus.publish(finding)

    def _check(self, purchase, complaint, policy) -> RefundResult:
        if not purchase.verified:
            return RefundResult(
                message_id=purchase.message_id,
                eligible=False,
                reason="Purchase could not be verified",
            )

        window = policy.refund_window_days
        days   = purchase.days_since_purchase or 0

        if days > window:
            return RefundResult(
                message_id=purchase.message_id,
                eligible=False,
                reason=(
                    f"Purchase is {days} days old. "
                    f"Refund window is {window} days."
                ),
            )

        auto = complaint.complaint_type in policy.auto_refund_complaint_types
        return RefundResult(
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

### 9.9 ResolutionAgent

- **Subscribes to**: `RefundResult` AND `ProfileResult`
- **Gate condition**: `refund_eligibility`, `profile`, and `complaint_type` all set.
- **Lock**: Mandatory — both subscribed contracts can call this handler before either sets `_fired`.
- **Output**: `ResolutionResult` → `deps.board.resolution`
- **Tools**: `log_decision` only — all context from `deps.board`

```python
class ResolutionAgent(BaseAgent):

    def __init__(self, name: str, agent):
        super().__init__(name, agent)
        self._lock  = asyncio.Lock()
        self._fired = False

    def reset(self) -> None:
        self._fired = False

    def subscribe(self, bus: MessageHub, deps: Deps) -> None:
        async def handler(contract):
            await self.handle(contract, deps)
        bus.subscribe(RefundResult, handler)
        bus.subscribe(ProfileResult, handler)

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

            Call log_decision once. Return a ResolutionResult.
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
        finding: ResolutionResult = result.output
        deps.board.resolution = finding
        await deps.bus.publish(finding)
```

### 9.10 ResponseComposerAgent

- **Subscribes to**: `ResolutionResult`
- **Responsibility**: Compose the final customer-facing response. All upstream findings are
  guaranteed present when this agent activates because `ResolutionAgent` only fires after
  all its own gate conditions pass.
- **Lock**: Defensive — `ResolutionAgent` fires only once due to its own lock, but the
  defensive lock here makes the invariant explicit and costs nothing.
- **Output**: `CustomerResponse` → `deps.board.response`
- **Tools**: `get_customer_profile`, `log_decision`

```python
class ResponseComposerAgent(BaseAgent):

    def __init__(self, name: str, agent):
        super().__init__(name, agent)
        self._lock  = asyncio.Lock()
        self._fired = False

    def reset(self) -> None:
        self._fired = False

    def subscribe(self, bus: MessageHub, deps: Deps) -> None:
        async def handler(contract):
            await self.handle(contract, deps)
        bus.subscribe(ResolutionResult, handler)

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

            Call log_decision once. Return a CustomerResponse.
        """

    async def handle(self, contract: ResolutionResult, deps: Deps) -> None:
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
        finding: CustomerResponse = result.output
        deps.board.response = finding
        await deps.bus.publish(finding)
```

---

## 10. Service Layer

### 10.1 CustomerServiceHandler

**Location**: `service/handler.py`

`CustomerServiceHandler` owns the resolution cascade end-to-end. It is built once at startup
and reused across all requests. Agents and their underlying pydantic-ai `Agent` instances are
constructed once in `_build_agents()` — they carry no per-request state themselves.

Per-request state (`MessageHub`, `Blackboard`, `Deps`) is created fresh inside `handle()` on
every call. Stateful agents (`RefundEligibilityAgent`, `ResolutionAgent`,
`ResponseComposerAgent`) are reset before each subscribe loop via `agent.reset()`.

```python
import logging
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
from core.board import Blackboard
from core.llm_factory import make_model

from db.repositories.facade import RepoFacade
from schemas.data.policy import Policy
from schemas.contracts.customer_message import CustomerMessage
from schemas.contracts.purchase_result import PurchaseResult
from schemas.contracts.profile_result import ProfileResult
from schemas.contracts.complaint_result import ComplaintResult
from schemas.contracts.refund_result import RefundResult
from schemas.contracts.resolution_result import ResolutionResult
from schemas.contracts.customer_response import CustomerResponse

from tools.agent_logger import log_decision
from tools.customer_tools import get_customer_profile
from tools.order_tools import (
    get_order_summary, get_order_line_items,
    get_order_total, get_customer_order_count,
)
from tools.complaint_tools import get_recent_complaints, get_complaint_count

logger = logging.getLogger(__name__)

PROVIDER = "openai:gpt-4o-mini"


class CustomerServiceHandler:
    """Resolves a customer message end-to-end using the Observer fan-out pattern.

    Built once at startup. Agents and their underlying LLM wrappers are
    constructed once and reused across all calls.

    Per-request state (MessageHub, Blackboard, Deps) is created fresh inside
    handle() — never shared between calls.
    """

    def __init__(self, repo: RepoFacade, policy: Policy) -> None:
        self._repo   = repo
        self._policy = policy
        self._agents = self._build_agents()

    def _build_agents(self) -> list:
        """Construct all agents once. Agent instances are stateless across
        requests — all mutable state lives in Deps, which is per-request."""
        return [
            PurchaseVerificationAgent(
                name="purchase_verification",
                agent=Agent(
                    model=make_model(PROVIDER), deps_type=Deps,
                    output_type=PurchaseResult,
                    tools=[log_decision, get_order_summary,
                           get_order_line_items, get_order_total],
                ),
            ),
            CustomerHistoryAgent(
                name="customer_history",
                agent=Agent(
                    model=make_model(PROVIDER), deps_type=Deps,
                    output_type=ProfileResult,
                    tools=[log_decision, get_customer_profile,
                           get_customer_order_count,
                           get_recent_complaints, get_complaint_count],
                ),
            ),
            ComplaintClassificationAgent(
                name="complaint_classification",
                agent=Agent(
                    model=make_model(PROVIDER), deps_type=Deps,
                    output_type=ComplaintResult,
                    tools=[log_decision],
                ),
            ),
            SentimentAgent(
                name="sentiment",
                agent=Agent(
                    model=make_model(PROVIDER), deps_type=Deps,
                    output_type=dict,
                    tools=[log_decision],
                ),
            ),
            RefundEligibilityAgent(
                name="refund_eligibility",
            ),
            ResolutionAgent(
                name="resolution",
                agent=Agent(
                    model=make_model(PROVIDER), deps_type=Deps,
                    output_type=ResolutionResult,
                    tools=[log_decision],
                ),
            ),
            ResponseComposerAgent(
                name="response_composer",
                agent=Agent(
                    model=make_model(PROVIDER), deps_type=Deps,
                    output_type=CustomerResponse,
                    tools=[log_decision, get_customer_profile],
                ),
            ),
        ]

    async def handle(self, message: CustomerMessage) -> dict:
        """Handle one customer message. Returns a result dict.

        Creates a fresh MessageHub, Blackboard, and Deps for this request.
        Resets stateful agents, subscribes all agents to the hub, then fires
        the single publish() call that triggers the entire agent cascade.
        """
        bus   = MessageHub()
        board = Blackboard()
        deps  = Deps(
            repo=self._repo,
            bus=bus,
            board=board,
            policy=self._policy,
            message_id=message.message_id,
            customer_id=message.customer_id,
            order_id=message.order_id,
            total_tokens=0,
        )

        # Reset stateful agents and build the subscription dictionary.
        # Each agent appends its handler to the list for its contract type.
        # Nothing runs here — the hub is just wired up.
        for agent in self._agents:
            agent.reset()
            agent.subscribe(bus, deps)

        # Single publish call triggers the entire cascade.
        # Does not return until deps.board.response is set.
        logger.info(f"[{message.message_id}] cascade start")
        await bus.publish(message)
        logger.info(f"[{message.message_id}] cascade complete")

        response = deps.board.response
        return {
            "resolved":      response.resolved      if response else False,
            "response":      response.response       if response else "System error",
            "actions_taken": response.actions_taken  if response else [],
            "total_tokens":  deps.total_tokens,
        }
```

---

## 11. main.py

```python
import asyncio
import json
import uuid
from datetime import datetime

from db.connection import Database
from db.repositories.facade import RepoFacade
from db.repositories.policy_repo import PolicyRepository

from schemas.contracts.customer_message import CustomerMessage

from agents.intake_agent import IntakeAgent
from service.handler import CustomerServiceHandler

CUSTOMER_ID = "C001"   # in production: resolved from auth / session


async def main() -> None:
    await Database.get_pool()

    repo    = RepoFacade()
    policy  = await PolicyRepository().get()
    handler = CustomerServiceHandler(repo, policy)

    try:
        await chat(handler)
    finally:
        await Database.close()


async def chat(handler: CustomerServiceHandler) -> None:
    """Intake loop. Runs until the customer's complaint is fully resolved.

    IntakeAgent holds a multi-turn conversation, accumulating context across
    turns via its own _history. When it signals ready=True, a CustomerMessage
    is constructed from the collected context and handed to CustomerServiceHandler.
    The resolution cascade fires once on the complete, summarised complaint.
    """
    intake = IntakeAgent()

    print("Agent: Hi, how can I help you today?")

    while True:
        user_input = input("Customer: ").strip()
        if not user_input:
            continue

        intake_result = await intake.collect(user_input, customer_id=CUSTOMER_ID)
        print(f"Agent: {intake_result.reply}")

        if not intake_result.ready:
            continue

        # IntakeAgent has collected order_id and a complete complaint description.
        # Hand off to CustomerServiceHandler for the full resolution cascade.
        message = CustomerMessage(
            message_id=str(uuid.uuid4()),
            customer_id=CUSTOMER_ID,
            order_id=intake_result.order_id,
            message=intake_result.message,
            timestamp=datetime.now().isoformat(),
        )

        result = await handler.handle(message)
        print(json.dumps(result, indent=2))
        break


asyncio.run(main())
```

---

## 12. Implementation Sequence

**Phase 1 — Database** (`db/`)

Run `schema.sql`. Verify all tables and seed data. Test `Database.get_pool()` returns the same
pool object on repeated calls — confirming singleton behaviour. Test each repository method
directly: `get_order` with mismatched `customer_id` returns `None`, `get_history` with
`limit=10` never exceeds 10 rows, `PolicyRepository.get()` returns a valid `Policy` object.

**Phase 2 — Schemas** (`schemas/`)

Instantiate each schema with dummy data, round-trip `model_dump()` / `model_validate()`.
All contract schemas carry `message_id`. No schema has methods. Confirm `IntakeResult`
round-trips with both `ready=False` (order_id and message are None) and `ready=True`
(all fields populated).

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
simultaneously and confirm `_fired` prevents double-posting. Confirm `reset()` clears
`_fired` and the agent fires correctly on a second request.

**Phase 7 — Phase 1 resolution agents** (`agents/`)

Build in order: `ComplaintClassificationAgent` → `PurchaseVerificationAgent` →
`CustomerHistoryAgent` → `SentimentAgent`. For each: subscribe to a live bus with deps,
call `bus.publish(CustomerMessage)`, confirm finding posted to `deps.board` and
published to bus.

**Phase 8 — Phase 2 resolution agents** (`agents/`)

Build in order: `ResolutionAgent` → `ResponseComposerAgent`. Confirm gate exits silently when
findings missing. With all required findings set, confirm agent activates, calls LLM, posts
finding. Manually test lock: call `handle()` twice in quick succession, confirm second call
is a no-op. Confirm `reset()` allows the agent to fire again on the next request.

**Phase 9 — CustomerServiceHandler** (`service/`)

Test with a fully formed `CustomerMessage` (bypassing intake). Confirm: the 4 handlers for
`CustomerMessage` start concurrently, `log_decision` lines appear for all LLM agents,
`RefundEligibilityAgent` produces no token lines, `CustomerResponse` is the final output
with `resolved=True`. Run two requests in sequence and confirm `reset()` prevents `_fired`
leaking between them.

**Phase 10 — IntakeAgent** (`agents/`)

Test `collect()` in isolation with a sequence of simulated customer turns. Confirm
`ready=False` until both `order_id` and a complete complaint are present. Confirm
`ready=True` populates `order_id` and `message`. Confirm `_history` grows correctly
across turns — the LLM sees the full thread on every call.

**Phase 11 — Integration** (`main.py`)

Run end-to-end with a multi-turn chat. Confirm: `IntakeAgent` asks one question at a time,
`ready=True` is only signalled when both `order_id` and complaint are collected,
`CustomerServiceHandler` fires the cascade exactly once on the summarised `message`, and
the final `CustomerResponse` is printed with `resolved=True`.

---

## 13. Expected Output

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