# Multi-Agent Customer Service System
## Specification v8.0 — Observer / Pub-Sub Pattern with Conversational Intake, Tool-Based Data Access, and Blackboard-Driven Agent Communication

---

## 1. Purpose

A learning-oriented multi-agent system that handles customer service requests end-to-end,
including a conversational intake phase that collects context before resolution begins.

The primary goal is to understand and implement two complementary patterns in one system:

- **Sequential conversational intake** — a single LLM agent holds a multi-turn chat with
  the customer, accumulates context across turns, and signals when it has enough to proceed.
- **Observer pattern with concurrent agent fan-out** — once intake is complete, a single
  publish call triggers a cascade of specialised agents that work concurrently, each
  reacting to typed messages rather than being told what to do.

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
│  CustomerServiceHandler builds a ServiceRequestMessage and fires one publish().   │
│  Seven agents fan out concurrently via the MessageHub.                            │
│  Each reacts to typed messages. No orchestrator. No scheduler.                    │
└───────────────────────────────────────────────────────────────────────────────────┘
```

The two phases never overlap. Phase 2 does not start until `IntakeAgent` signals
`ready=True`. `IntakeAgent` has no knowledge of `MessageHub`, `Deps`, `Blackboard`, or
any resolution agent.

---

### 2.2 Pattern — Observer with typed messages and Blackboard data sharing

The resolution cascade implements the **Observer pattern**. Its mechanism in this system
is a `MessageHub` — a dictionary that maps message types to lists of async handler
functions.

In the classical Observer pattern, subjects maintain a registry of observers and notify
them when something changes. Here:

- **The subject** is `MessageHub`. It maintains the registry and notifies observers.
- **The observers** are the resolution agents. Each registers a handler for one or more
  message types via `subscribe()`.
- **The notification** is `hub.publish(message)` — the hub calls all handlers registered
  for that message type, concurrently, via `asyncio.gather()`.

#### Two distinct concerns: outputs and messages

This system maintains a clean separation between what an LLM produces and what the hub routes.

**Output schemas** (`schemas/outputs/`) — what the LLM must produce. Shaped for the model's
reasoning. Rich, with all fields the agent needs to make its decision. Stored on
`deps.board` for any downstream agent to read directly.

**Messages** (`schemas/messages/`) — what gets published to the hub. Lean, typed notification
that something has been determined. Carries only `triggered_by` (which agent sent it) and
`timestamp` (when it was sent). Downstream agents do not read data off the message — they
read `deps.board`.

One agent, two distinct steps:

```python
# 1. write full rich output to blackboard
finding: PurchaseOutput = result.output
deps.board.purchase = finding

# 2. build lean message and publish to hub
await deps.hub.publish(PurchaseResultMessage(
    triggered_by="purchase_agent",
    timestamp=datetime.now().isoformat(),
))
```

This separation means:
- The LLM output schema can evolve independently of what the hub routes.
- Downstream agents always read full, rich data from `deps.board` — never reconstruct
  it from a message payload.
- The hub message is a pure notification: "this has been determined, read the board."
- `triggered_by` and `timestamp` on every message make the cascade easy to trace and debug.

#### What the MessageHub is

The `MessageHub` is a dictionary and an `asyncio.gather()` call. Nothing more.

```python
# State of the hub after all subscribe() calls:
{
    ServiceRequestMessage:      [purchase_handler, profile_handler, complaint_handler, sentiment_handler],
    PurchaseResultMessage:      [refund_handler],
    ComplaintResultMessage:     [refund_handler],
    ProfileResultMessage:       [resolution_handler],
    RefundResultMessage:        [resolution_handler],
    ResolutionResultMessage:    [composer_handler],
}

# publish() does exactly this:
async def publish(self, message: BaseModel) -> None:
    handlers = self._subscribers.get(type(message), [])
    if handlers:
        await asyncio.gather(*[h(message) for h in handlers])
```

The hub has zero domain knowledge. It does not know what a customer is, what a refund
is, or what any message contains. It only knows message types and handler lists.

The term "event loop" is reserved exclusively for Python's `asyncio` event loop and is
never used to describe the hub's dispatch mechanism.

---

### 2.3 The Blackboard

The `Blackboard` is a typed, per-request result accumulator. It is a plain dataclass on
`Deps` — one instance created fresh for each `ServiceRequestMessage`, discarded when the
cascade completes.

Agents write their full LLM output (`XxxxxOutput`) to `deps.board` immediately after the
LLM call completes. Downstream agents read from `deps.board` directly — never from the
hub message payload. The hub message is a lean notification only.

```python
@dataclass
class Blackboard:
    purchase:   PurchaseOutput   | None = None
    profile:    ProfileOutput    | None = None
    complaint:  ComplaintOutput  | None = None
    refund:     RefundOutput     | None = None
    resolution: ResolutionOutput | None = None
    response:   ResponseOutput   | None = None
```

Field names are short domain nouns. Types use the `Output` suffix, matching the
`schemas/outputs/` folder. Gate conditions in downstream agents check these fields
directly — e.g. `deps.board.purchase is None`.

This is not a Blackboard in the AI/knowledge-engineering sense (where the solution
emerges incrementally from unknown inputs). The source data here — customers, orders,
complaint history — are known structured facts in MySQL, queried on demand through tools.
The `Blackboard` here is purely a result accumulator: agents write findings, downstream
agents read them.

---

### 2.4 Responsibility boundaries

Three classes own the system's top-level concerns. They do not overlap.

| Class | Location | Responsibility |
|---|---|---|
| IntakeAgent | `agents/intake_agent.py` | Multi-turn chat with customer. Collects `order_id` and complete complaint description. Signals `ready=True` when done. No hub, no deps, no findings. |
| CustomerServiceHandler | `service/handler.py` | Initialises DB, policy, repo, LLM models, and agents once at startup. Per request: creates `MessageHub`, `Blackboard`, `Deps`; subscribes all agents; fires the single `publish()` call; returns the result dict. |
| MessageHub | `core/message_hub.py` | Pure fan-out. Maps message types to handler lists. Calls `asyncio.gather()` on publish. Zero domain knowledge. |

---

### 2.5 Agents

There are eight agents in total. One handles intake. Seven handle resolution.

**Intake agent** — outside the hub, outside `Deps`.

| Agent | Role |
|---|---|
| `IntakeAgent` | Conversational. Holds `_history` across turns. Returns `IntakeResult` each turn. Discarded at session end. |

**Resolution agents** — subscribed to the hub, receive `Deps`.

| Agent | Subscribes to | Writes to board | Publishes |
|---|---|---|---|
| PurchaseAgent | `ServiceRequestMessage` | `board.purchase` (`PurchaseOutput`) | PurchaseResultMessage |
| ProfileAgent | `ServiceRequestMessage` | `board.profile` (`ProfileOutput`) | ProfileResultMessage |
| ComplaintAgent | `ServiceRequestMessage` | `board.complaint` (`ComplaintOutput`) | ComplaintResultMessage |
| SentimentAgent | `ServiceRequestMessage` | updates `board.profile.sentiment_*` in place | — |
| RefundAgent | `PurchaseResultMessage` + `ComplaintResultMessage` | `board.refund` (`RefundOutput`) | RefundResultMessage |
| ResolutionAgent | `RefundResultMessage` + `ProfileResultMessage` | `board.resolution` (`ResolutionOutput`) | ResolutionResultMessage |
| ResponseComposerAgent | `ResolutionResultMessage` | `board.response` (`ResponseOutput`) | — |

`RefundAgent` is pure Python — no LLM, no tools. It applies policy rules directly
against `deps.board`.

---

### 2.6 Naming convention

**Output schemas** (`schemas/outputs/`) — what the LLM produced. `Output` suffix.

| Class | Produced by |
|---|---|
| PurchaseOutput | `PurchaseAgent` LLM |
| ProfileOutput | `ProfileAgent` LLM |
| ComplaintOutput | `ComplaintAgent` LLM |
| RefundOutput | `RefundAgent` (pure Python) |
| ResolutionOutput | `ResolutionAgent` LLM |
| ResponseOutput | `ResponseComposerAgent` LLM |

**Messages** (`schemas/messages/`) — what the hub routes. `Message` suffix.

| Class | Role |
|---|---|
| ServiceRequestMessage | Inbound trigger. First publish call. Carries `customer_id`, `order_id`, `message`, `triggered_by`, `timestamp`. |
| PurchaseResultMessage | Published by `PurchaseAgent` after writing to board. Carries `triggered_by`, `timestamp`. |
| ProfileResultMessage | Published by `ProfileAgent` after writing to board. Carries `triggered_by`, `timestamp`. |
| ComplaintResultMessage | Published by `ComplaintAgent` after writing to board. Carries `triggered_by`, `timestamp`. |
| RefundResultMessage | Published by `RefundAgent` after writing to board. Carries `triggered_by`, `timestamp`. |
| ResolutionResultMessage | Published by `ResolutionAgent` after writing to board. Carries `triggered_by`, `timestamp`. |

`IntakeResult` belongs to neither folder — it is the intake loop's turn-by-turn signal,
never published to the hub.

---

### 2.7 Per-request vs shared state

`CustomerServiceHandler` is built once and reused across all customer requests. Agents
and their underlying pydantic-ai `Agent` instances are also built once in
`_build_agents()` — they carry no per-request state themselves.

Per-request state is isolated inside `handle()`:

| Object | Scope | Why |
|---|---|---|
| MessageHub | Per request | Handler lists must be built fresh on every `subscribe()` call. |
| Blackboard | Per request | Accumulates this request's agent outputs only. |
| Deps | Per request | Carries `message_id`, `customer_id`, `order_id`, `total_tokens`, and references to the per-request hub and blackboard. |

All agents are stateless between requests. No agent carries instance-level mutable state
that needs to be reset. The Blackboard is the only state accumulator, and it is created
fresh per request.

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
turn. When `ready=True`, it constructs a `ServiceRequestMessage` and hands it to
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
    ServiceRequestMessage: [purchase_handler, profile_handler,
                            complaint_handler, sentiment_handler],
    PurchaseResultMessage:       [refund_handler],
    ComplaintResultMessage:      [refund_handler],
    ProfileResultMessage:        [resolution_handler],
    RefundResultMessage:         [resolution_handler],
    ResolutionResultMessage:     [composer_handler],
}

# publish() does exactly this:
async def publish(self, message: BaseModel) -> None:
    handlers = self._subscribers.get(type(message), [])
    if handlers:
        await asyncio.gather(*[h(message) for h in handlers])
```

- When `publish(ServiceRequestMessage)` is called
  - the hub looks up `ServiceRequestMessage`
    - finds 4 handlers, and calls `asyncio.gather()` on all 4.
- When `publish(PurchaseResultMessage)` is called
  - the hub looks up `PurchaseResultMessage`
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
**Phase 2 agents are called immediately as each Phase 1 agent finishes and publishes its message.**

What controls whether a Phase 2 agent does real work is its internal gate condition:

```python
if deps.board.purchase is None or deps.board.complaint is None:
    return  # called but does nothing
```

So the correct statement is:

- Phase 2 agents **get called** as soon as any Phase 1 agent that they subscribed to finishes
- Phase 2 agents **do real work** only when all their required findings are present on the board
- The gate condition is the only thing enforcing this — not the hub, not asyncio

---

### 3.5 Step by step execution

**Step 1 — the subscribe loop builds the dictionary**

```python
for agent in self._agents:
    agent.subscribe(hub, deps)
```

Each agent's `subscribe()` method calls `hub.subscribe(MessageType, handler)` which
appends the handler to the dictionary list for that message type. Nothing runs. The
dictionary is just built.

**Step 2 — the single trigger**

```python
await hub.publish(message)  # message is ServiceRequestMessage
```

Inside `hub.publish()`:

```python
handlers = self._subscribers.get(type(message), [])
# handlers = [purchase_handler, profile_handler, complaint_handler, sentiment_handler]
await asyncio.gather(*[h(message) for h in handlers])
```

All 4 handlers start. Each one immediately hits `await self._agent.run(...)` inside its
`handle()` method and suspends. All 4 LLM calls are now in flight simultaneously.

**Step 3 — say ComplaintAgent LLM responds first**

Its `handle()` resumes from `await self._agent.run(...)`:

```python
async def handle(self, message: ServiceRequestMessage, deps: Deps) -> None:
    result = await self._agent.run(...)           # resumes here
    finding: ComplaintOutput = result.output
    deps.board.complaint = finding                # 1. write to blackboard
    await deps.hub.publish(ComplaintResultMessage(      # 2. publish lean message
        triggered_by="complaint_agent",
        timestamp=datetime.now().isoformat(),
    ))
```

`hub.publish(ComplaintResultMessage)` looks up `ComplaintResultMessage`. Finds `[refund_handler]`.
Calls `refund_agent.handle()` immediately — right now, while the other 3 Phase 1 agents are
still waiting for their LLM responses:

```python
async def handle(self, message, deps: Deps) -> None:
    if deps.board.purchase is None or deps.board.complaint is None:
        return  # purchase is None — exits silently
```

`deps.board.purchase` is `None` because `purchase_agent` has not finished yet. Gate fails.
`refund_agent` exits silently. Control returns to `complaint_agent.handle()` which is now done.

**Step 4 — say PurchaseAgent LLM responds next**

Its `handle()` resumes:

```python
async def handle(self, message: ServiceRequestMessage, deps: Deps) -> None:
    result = await self._agent.run(...)           # resumes here
    finding: PurchaseOutput = result.output
    deps.board.purchase = finding                 # 1. write to blackboard
    await deps.hub.publish(PurchaseResultMessage(       # 2. publish lean message
        triggered_by="purchase_agent",
        timestamp=datetime.now().isoformat(),
    ))
```

`hub.publish(PurchaseResultMessage)` looks up `PurchaseResultMessage`. Finds `[refund_handler]`.
Calls `refund_agent.handle()` again:

```python
async def handle(self, message, deps: Deps) -> None:
    if deps.board.purchase is None or deps.board.complaint is None:
        return
    # purchase is now set, complaint was set in Step 3 — gate passes
    finding = self._check(deps.board.purchase, deps.board.complaint, deps.policy)
    deps.board.refund = finding
    await deps.hub.publish(RefundResultMessage(
        triggered_by="refund_agent",
        timestamp=datetime.now().isoformat(),
    ))
```

Gate passes. The gate check contains no `await` — so between the check and the work that
follows it, no other coroutine can interleave. `RefundAgent` proceeds, writes to the board,
and publishes `RefundResultMessage`.

`hub.publish(RefundResultMessage)` looks up `RefundResultMessage`. Finds `[resolution_handler]`.
Calls `resolution_agent.handle()` immediately — while `profile_agent` and `sentiment_agent` are
still waiting for their LLM responses:

```python
async def handle(self, message, deps: Deps) -> None:
    if (deps.board.refund is None
            or deps.board.profile is None
            or deps.board.complaint is None):
        return  # profile is None — profile_agent not done yet — exits silently
```

Gate fails. `resolution_agent` exits silently. Control unwinds back through `refund_agent.handle()`,
back through `purchase_agent.handle()` which is now done. Back to the original `asyncio.gather()`.
Still waiting for `profile_agent` and `sentiment_agent`.

**Step 5 — ProfileAgent LLM responds**

Its `handle()` resumes:

```python
async def handle(self, message: ServiceRequestMessage, deps: Deps) -> None:
    result = await self._agent.run(...)           # resumes here
    finding: ProfileOutput = result.output
    deps.board.profile = finding                  # 1. write to blackboard
    await deps.hub.publish(ProfileResultMessage(        # 2. publish lean message
        triggered_by="profile_agent",
        timestamp=datetime.now().isoformat(),
    ))
```

`hub.publish(ProfileResultMessage)` looks up `ProfileResultMessage`. Finds `[resolution_handler]`.
Calls `resolution_agent.handle()`:

```python
async def handle(self, message, deps: Deps) -> None:
    if (deps.board.refund is None
            or deps.board.profile is None
            or deps.board.complaint is None):
        return
    # all three are now set — gate passes
```

Gate passes. Continues:

```python
    result = await self._agent.run(...)
    finding: ResolutionOutput = result.output
    deps.board.resolution = finding
    await deps.hub.publish(ResolutionResultMessage(
        triggered_by="resolution_agent",
        timestamp=datetime.now().isoformat(),
    ))
```

`hub.publish(ResolutionResultMessage)` looks up `ResolutionResultMessage`. Finds `[composer_handler]`.
Calls `composer_agent.handle()`:

```python
async def handle(self, message: ResolutionResultMessage, deps: Deps) -> None:
    result = await self._agent.run(...)
    finding: ResponseOutput = result.output
    deps.board.response = finding
    # terminal — no publish
```

Nobody subscribed after `ResponseComposerAgent`. `composer_agent.handle()` is done.

Control unwinds: back through `resolution_agent.handle()`, back through `profile_agent.handle()`
which is now done. `sentiment_agent` finishes around this time too. The original `asyncio.gather()`
has all 4 coroutines done. Returns. `await hub.publish(message)` in `CustomerServiceHandler.handle()`
returns. `deps.board.response` is set.

**Step 6 — CustomerServiceHandler reads the result**

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

---

### 3.6 Why the gate condition is sufficient — no locks needed

`RefundAgent` and `ResolutionAgent` each subscribe to two message types, meaning their
`handle()` is called twice per request — once for each message they subscribed to. The
gate condition alone is sufficient to ensure they do real work exactly once. Here is why:

Python's `asyncio` is single-threaded. Coroutines only interleave at `await` points —
between two `await` statements, a coroutine runs uninterrupted. The gate check is pure
Python with no `await`:

```python
if deps.board.purchase is None or deps.board.complaint is None:
    return
```

This check is atomic from asyncio's perspective. No other coroutine can run between
evaluating the condition and the code that follows it. So the scenario where two
invocations both pass the gate simultaneously is impossible — only one coroutine can
be executing at any given moment, and by the time the second call reaches the gate,
the first has already written to the board and published its message.

The sequence is always:
- First call → one board field is `None` → gate fails → returns silently
- Second call → both board fields are set → gate passes → does real work → publishes once

No locks. No flags. No instance state. The blackboard itself is the synchronisation
mechanism — which is exactly what it was designed for.

---

### 3.7 Gate conditions per agent

| Agent | Gate condition | Lock needed |
|---|---|---|
| RefundAgent | `purchase` + `complaint` both set | No |
| ResolutionAgent | `refund` + `profile` + `complaint` all set | No |
| ResponseComposerAgent | no gate — called exactly once | No |

`ResponseComposerAgent` subscribes to `ResolutionResultMessage` only. `ResolutionAgent`
publishes that message exactly once — after its own gate passes. So `ResponseComposerAgent`
is guaranteed to be called exactly once. No gate required.

---

## 4. Project Structure

```
customer_service/
│
├── agents/
│   ├── base_agent.py
│   ├── intake_agent.py                  ← conversational intake, outside the hub
│   ├── purchase_agent.py
│   ├── profile_agent.py
│   ├── complaint_agent.py
│   ├── sentiment_agent.py
│   ├── refund_agent.py
│   ├── resolution_agent.py
│   └── response_composer_agent.py
│
├── core/
│   ├── blackboard.py
│   ├── deps.py
│   ├── logger.py
│   ├── llm_factory.py
│   └── message_hub.py
│
├── db/
│   ├── connection.py
│   └── repositories/
│       ├── facade.py
│       ├── customer_repo.py
│       ├── order_repo.py
│       ├── complaint_repo.py
│       └── policy_repo.py
│
├── schemas/
│   ├── data/                            ← database-mapped Pydantic models
│   │   ├── customer.py
│   │   ├── order.py
│   │   ├── complaint.py
│   │   └── policy.py
│   ├── outputs/                         ← LLM output types (rich, written to board)
│   │   ├── purchase_output.py
│   │   ├── profile_output.py
│   │   ├── complaint_output.py
│   │   ├── refund_output.py
│   │   ├── resolution_output.py
│   │   └── response_output.py
│   ├── messages/                        ← hub-routed message types (lean, routing only)
│   │    ├── service_request_message.py  ← inbound trigger, carries request payload
│   │    ├── purchase_message.py
│   │    ├── profile_message.py
│   │    ├── complaint_message.py
│   │    ├── refund_message.py
│   │    └── resolution_message.py
│   └── intake_result.py                ← initial chat result (non data/output/schemas)
│
├── service/
│   └── handler.py                       ← CustomerServiceHandler
│
├── tools/
│   ├── agent_logger.py
│   ├── customer_tools.py
│   ├── order_tools.py
│   └── complaint_tools.py
│
└── main.py
```

`service/` sits alongside `agents/`, `core/`, and `tools/` as a peer. It contains
`CustomerServiceHandler` — the one class that assembles the resolution cascade and drives
a single request to completion. It is domain logic, not infrastructure, and does not
belong in `core/`.

`schemas/data/` holds the database-mapped Pydantic models (`Customer`, `SalesOrder`,
`Policy`, etc.). `schemas/outputs/` holds what LLMs produce. `schemas/messages/` holds
what the hub routes. The split prevents naming collisions and makes each type's role
immediately obvious from its folder and suffix.

`IntakeResult` lives alongside `IntakeAgent` in `agents/intake_agent.py` or as
`schemas/intake_result.py` — it is neither an output (not LLM output_type for the
resolution cascade) nor a hub message. It is the intake loop's turn-by-turn signal only.

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

### 6.2 Output Schemas

All output schemas live in `schemas/outputs/`. These are what the LLM must produce —
rich, shaped for the agent's reasoning. Written to `deps.board` immediately after the
LLM call. Never published to the hub directly.

**`schemas/outputs/purchase_output.py`**
```python
from pydantic import BaseModel

class PurchaseOutput(BaseModel):
    verified: bool
    order_date: str | None = None
    product_name: str | None = None
    product_category: str | None = None
    days_since_purchase: int | None = None
    order_total: float | None = None
    reason: str | None = None
```

**`schemas/outputs/profile_output.py`**
```python
from pydantic import BaseModel

class ProfileOutput(BaseModel):
    customer_id: str
    tier: str
    total_orders: int
    previous_complaints: int
    is_repeat_issue: bool
    sentiment_score: float = 0.0
    sentiment_label: str = "neutral"
```

**`schemas/outputs/complaint_output.py`**
```python
from pydantic import BaseModel

class ComplaintOutput(BaseModel):
    complaint_type: str        # "packaging"|"product"|"delivery"|"billing"
    severity: str              # "low"|"medium"|"high"
    keywords: list[str]
    wants_refund: bool
    wants_replacement: bool
    wants_complaint_filed: bool
```

**`schemas/outputs/refund_output.py`**
```python
from pydantic import BaseModel

class RefundOutput(BaseModel):
    eligible: bool
    reason: str
    refund_amount: float | None = None
    extended_due_to_tier: bool = False
```

**`schemas/outputs/resolution_output.py`**
```python
from pydantic import BaseModel

class ResolutionOutput(BaseModel):
    options: list[str]
    recommended: str
    escalate_to_human: bool = False
    escalation_reason: str | None = None
```

**`schemas/outputs/response_output.py`**
```python
from pydantic import BaseModel

class ResponseOutput(BaseModel):
    response: str
    actions_taken: list[str]
    resolved: bool
```

### 6.3 Message Schemas

All message schemas live in `schemas/messages/`. These are lean hub-routed notifications.
All messages carry `triggered_by` (which agent sent it) and `timestamp` (when it was sent)
for tracing and debugging. Downstream agents never read data off the message — they read
`deps.board`.

**`schemas/messages/service_request_message.py`**
```python
from pydantic import BaseModel

class ServiceRequestMessage(BaseModel):
    message_id: str    # uuid — ties all findings for this request together
    customer_id: str
    order_id: str
    message: str       # full summarised complaint from IntakeAgent
    triggered_by: str  # "intake_agent"
    timestamp: str
```

**`schemas/messages/purchase_message.py`**
```python
from pydantic import BaseModel

class PurchaseResultMessage(BaseModel):
    triggered_by: str  # "purchase_agent"
    timestamp: str
```

**`schemas/messages/profile_message.py`**
```python
from pydantic import BaseModel

class ProfileResultMessage(BaseModel):
    triggered_by: str  # "profile_agent"
    timestamp: str
```

**`schemas/messages/complaint_message.py`**
```python
from pydantic import BaseModel

class ComplaintResultMessage(BaseModel):
    triggered_by: str  # "complaint_agent"
    timestamp: str
```

**`schemas/messages/refund_message.py`**
```python
from pydantic import BaseModel

class RefundResultMessage(BaseModel):
    triggered_by: str  # "refund_agent"
    timestamp: str
```

**`schemas/messages/resolution_message.py`**
```python
from pydantic import BaseModel

class ResolutionResultMessage(BaseModel):
    triggered_by: str  # "resolution_agent"
    timestamp: str
```

---

## 7. Core Infrastructure

### 7.1 Blackboard

**Location**: `core/blackboard.py`

```python
from dataclasses import dataclass
from schemas.outputs.purchase_output import PurchaseOutput
from schemas.outputs.profile_output import ProfileOutput
from schemas.outputs.complaint_output import ComplaintOutput
from schemas.outputs.refund_output import RefundOutput
from schemas.outputs.resolution_output import ResolutionOutput
from schemas.outputs.response_output import ResponseOutput

@dataclass
class Blackboard:
    """Result accumulator for one request lifecycle.

    Agents write their full LLM output here after each run completes.
    Downstream agents read here to check gate conditions and access rich data.
    One instance per ServiceRequestMessage. Discarded when cascade completes.
    """
    purchase:   PurchaseOutput   | None = None
    profile:    ProfileOutput    | None = None
    complaint:  ComplaintOutput  | None = None
    refund:     RefundOutput     | None = None
    resolution: ResolutionOutput | None = None
    response:   ResponseOutput   | None = None

    def is_complete(self) -> bool:
        return self.response is not None
```

### 7.2 Deps

**Location**: `core/deps.py`

```python
from dataclasses import dataclass
from core.message_hub import MessageHub
from core.blackboard import Blackboard
from db.repositories.facade import RepoFacade
from schemas.data.policy import Policy

@dataclass
class Deps:
    """Injected into every agent run via RunContext.

    repo         — facade grouping all repositories. Tools call ctx.deps.repo.<repo>.<method>().
    hub          — message hub. Agents publish messages through it.
    board        — blackboard. Agents write outputs and read gate conditions from it.
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

The `MessageHub` has one job: fan out. It is a dictionary mapping message types to lists of handler
functions. `subscribe()` appends to the list. `publish()` looks up the list and calls
`asyncio.gather()` on all handlers for that message type.

Handlers are registered as **closures that capture `deps`** at subscription time. This keeps
the hub signature clean — `publish(message)` only, no `deps` parameter leaking into the hub.

```python
import asyncio
from collections import defaultdict
from typing import Callable
from pydantic import BaseModel


class MessageHub:
    """Pure fan-out message hub. Zero domain knowledge.

    subscribe(message_type, handler):
        Appends handler to the list for that message type.
        Handler signature: async def handler(message: BaseModel) -> None
        Called once per agent per message type at startup.

    publish(message):
        Looks up type(message) in the dictionary.
        Calls asyncio.gather() on all handlers in that list.
        Does not return until all handlers and their downstream publishes complete.
        Handlers for other message types are never called.
    """

    def __init__(self):
        self._subscribers: dict[type, list[Callable]] = defaultdict(list)

    def subscribe(self, message_type: type, handler: Callable) -> None:
        self._subscribers[message_type].append(handler)

    async def publish(self, message: BaseModel) -> None:
        handlers = self._subscribers.get(type(message), [])
        if handlers:
            await asyncio.gather(*[h(message) for h in handlers])
```

**How handlers capture deps via closure in each agent's `subscribe()`:**

```python
# inside any agent's subscribe() method
def subscribe(self, hub: MessageHub, deps: Deps) -> None:
    async def handler(message):
        await self.handle(message, deps)   # deps captured here at subscription time
    hub.subscribe(ServiceRequestMessage, handler)
```

The hub calls `handler(message)`. The handler calls `self.handle(message, deps)` with the captured
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

| Tool | PurchaseAgent | ProfileAgent | ComplaintAgent | SentimentAgent | RefundAgent | ResolutionAgent | ResponseComposerAgent |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| log_decision | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ |
| get_customer_profile | — | ✓ | — | — | — | — | ✓ |
| get_order_summary | ✓ | — | — | — | — | — | — |
| get_order_line_items | ✓ | — | — | — | — | — | — |
| get_order_total | ✓ | — | — | — | — | — | — |
| get_customer_order_count | — | ✓ | — | — | — | — | — |
| get_recent_complaints | — | ✓ | — | — | — | — | — |
| get_complaint_count | — | ✓ | — | — | — | — | — |

RefundAgent is pure Python — no tools, no LLM.

---

## 9. Agents

### 9.1 Design principles

Each agent has one domain responsibility, one subscription set, and one tool set. The LLM calls
tools mid-reasoning to pull exactly the data it needs — never pre-loaded. Each agent writes its
full output to `deps.board` then publishes a lean message to the hub. No agent knows or cares
what other agents exist.

Phase 2 agents (`RefundAgent`, `ResolutionAgent`) subscribe to multiple message types and are
therefore called more than once per request. They use a blackboard gate condition to determine
whether all required inputs are present. The first call that finds a missing dependency exits
silently. The second call, arriving after all dependencies are on the board, proceeds. The gate
check contains no `await` — it is atomic from asyncio's perspective, so no locking is required.

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
from schemas.intake_result import IntakeResult

logger = logging.getLogger(__name__)

class IntakeAgent:
    """Conversational intake agent. Sits in front of CustomerServiceHandler.

    Holds a multi-turn conversation with the customer until it has collected
    the two things the resolution cascade requires:

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

### 9.4 PurchaseAgent

- **Subscribes to**: `ServiceRequestMessage`
- **Responsibility**: Verify the order exists, belongs to this customer, is delivered, and
  determine days since purchase and what was purchased.
- **Output**: `PurchaseOutput` → `deps.board.purchase`
- **Publishes**: `PurchaseResultMessage`
- **Tools**: `get_order_summary`, `get_order_line_items`, `get_order_total`, `log_decision`

```python
class PurchaseAgent(BaseAgent):
    def __init__(self, name: str, agent: Agent):
        # 1. Run the BaseAgent constructor to assign self._name and self._agent
        super().__init__(name=name, agent=agent)
        
        # 2. Intercept the newly assigned self._agent and register the instructions
        @self._agent.system_prompt
        def assign_system_instructions(ctx) -> str:
            return self.get_instruction()  

    def get_instruction(self) -> str:
        return """
            You are the PurchaseAgent in a customer service system.
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

            Call log_decision once. Return a PurchaseOutput.
        """

    async def handle(self, param: AgentParam) -> None:
        result = await self._agent.run(
            f"Verify purchase for order {deps.order_id} "
            f"by customer {deps.customer_id}. "
            f"Customer message: {message.message}",
            deps=deps,
            instructions=self.get_instruction(),
        )
        finding: PurchaseOutput = result.output
        param.deps.board.purchase = finding

        await param.deps.hub.publish(PurchaseResultMessage(
            triggered_by="purchase_agent",
            timestamp=datetime.now().isoformat(),
        ), deps = param.deps)
```

### 9.5 ProfileAgent

- **Subscribes to**: `ServiceRequestMessage`
- **Responsibility**: Build a customer profile from history. Initialises sentiment fields for
  `SentimentAgent` to update.
- **Output**: `ProfileOutput` → `deps.board.profile`
- **Publishes**: `ProfileResultMessage`
- **Tools**: `get_customer_profile`, `get_customer_order_count`, `get_recent_complaints`,
  `get_complaint_count`, `log_decision`

```python
from datetime import datetime
from agents.base_agent import BaseAgent
from core.deps import Deps
from schemas.messages.service_request_message import ServiceRequestMessage
from schemas.messages.profile_message import ProfileResultMessage
from schemas.outputs.profile_output import ProfileOutput


class ProfileAgent(BaseAgent):
    def __init__(self, name: str, agent: Agent):
        # 1. Run the BaseAgent constructor to assign self._name and self._agent
        super().__init__(name=name, agent=agent)
        
        # 2. Intercept the newly assigned self._agent and register the instructions
        @self._agent.system_prompt
        def assign_system_instructions(ctx) -> str:
            return self.get_instruction()
    
    def get_instruction(self) -> str:
        return """
            You are the ProfileAgent in a customer service system.
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

            Call log_decision once. Return a ProfileOutput.
        """

    async def handle(self, param: AgentParam) -> None:
        result = await self._agent.run(
            f"Build profile for customer {deps.customer_id}. "
            f"Message context: {message.message}",
            deps=deps,
        )
        finding: ProfileOutput = result.output
        param.deps.board.profile = finding

        await param.deps.hub.publish(ProfileResultMessage(
            triggered_by="profile_agent",
            timestamp=datetime.now().isoformat(),
        ), deps=param.deps)
```

### 9.6 ComplaintAgent

- **Subscribes to**: `ServiceRequestMessage`
- **Responsibility**: Classify complaint type, severity, keywords, and explicit intent flags
  from the message text. No database queries needed.
- **Output**: `ComplaintOutput` → `deps.board.complaint`
- **Publishes**: `ComplaintResultMessage`
- **Tools**: `log_decision` only

```python
class ComplaintAgent(BaseAgent):

    def get_instruction(self) -> str:
        return """
            You are the ComplaintAgent.
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

            Call log_decision once. Return a ComplaintOutput.
        """

    async def handle(self, message: ServiceRequestMessage, deps: Deps) -> None:
        result = await self._agent.run(
            f"Classify this complaint: {message.message}",
            deps=deps,
        )
        finding: ComplaintOutput = result.output
        param.deps.board.complaint = finding

        await param.deps.hub.publish(ComplaintResultMessage(
            triggered_by="complaint_agent",
            timestamp=datetime.now().isoformat(),
        ), deps=param.deps)
```

### 9.7 SentimentAgent

- **Subscribes to**: `ServiceRequestMessage`
- **Responsibility**: Score emotional tone independently. Updates profile sentiment fields
  in place if profile is already posted. If not yet posted, the score is held until profile
  arrives — a second subscription on `ProfileResultMessage` applies the update.
- **Output**: in-place update of `deps.board.profile.sentiment_score` and `sentiment_label`
- **Tools**: `log_decision` only

```python
from agents.base_agent import BaseAgent
from core.deps import Deps
from schemas.messages.service_request_message import ServiceRequestMessage
from schemas.messages.profile_message import ProfileResultMessage

class SentimentAgent(BaseAgent):

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

    async def handle(self, param: AgentParam) -> None:
        result = await self._agent.run(
            f"Score the sentiment of this message: {message.message}",
            deps=deps,
            instructions=self.get_instruction(),
        )
        score = result.output
        if param.deps.board.profile is not None:
            param.deps.board.profile.sentiment_score = score["sentiment_score"]
            param.deps.board.profile.sentiment_label = score["sentiment_label"]
        else:
            self._pending_score = score["sentiment_score"]
            self._pending_label = score["sentiment_label"]

    async def handle_profile(self, param: AgentParam) -> None:
        """
        Called when ProfileResultMessage arrives. Applies pending sentiment if
        SentimentAgent finished before ProfileAgent posted to the board.
        """
        if hasattr(self, "_pending_score") and self._pending_score is not None:
            param.deps.board.profile.sentiment_score = self._pending_score
            param.deps.board.profile.sentiment_label = self._pending_label
            self._pending_score = None
            self._pending_label = None
```

### 9.8 RefundAgent

- **Subscribes to**: `PurchaseResultMessage` AND `ComplaintResultMessage`
- **Responsibility**: Pure Python eligibility check against policy. No LLM. No database.
- **Gate condition**: both `deps.board.purchase` and `deps.board.complaint` must be set.
- **Output**: `RefundOutput` → `deps.board.refund`
- **Publishes**: `RefundResultMessage`

`RefundAgent` is called twice per request — once when `PurchaseResultMessage` arrives and
once when `ComplaintResultMessage` arrives. The gate check on the first line of `handle()`
determines whether both dependencies are on the board. The first call will always find one
missing and return silently. The second call finds both present and proceeds.

The gate check contains no `await` — it is atomic from asyncio's perspective. No other
coroutine can interleave between the check and the code that follows it, so no locking is
required.

```python
from datetime import datetime
from agents.base_agent import BaseAgent
from core.deps import Deps
from schemas.messages.refund_message import RefundResultMessage
from schemas.outputs.refund_output import RefundOutput

class RefundAgent(BaseAgent):

    def __init__(self, name: str):
        super().__init__(name, agent=None)

    def get_instruction(self) -> str:
        return ""

    async def handle(self, param: AgentParam) -> None:
        if param.deps.board.purchase is None or deps.board.complaint is None:
            return  # one dependency not yet on board — exit silently

        finding = self._check(
            param.deps.board.purchase,
            param.deps.board.complaint,
            param.deps.policy,
        )
        param.deps.board.refund = finding
        await param.deps.hub.publish(RefundResultMessage(
            triggered_by="refund_agent",
            timestamp=datetime.now().isoformat(),
        ))

    def _check(self, purchase, complaint, policy) -> RefundOutput:
        if not purchase.verified:
            return RefundOutput(
                eligible=False,
                reason="Purchase could not be verified",
            )

        window = policy.refund_window_days
        days   = purchase.days_since_purchase or 0

        if days > window:
            return RefundOutput(
                eligible=False,
                reason=(
                    f"Purchase is {days} days old. "
                    f"Refund window is {window} days."
                ),
            )

        auto = complaint.complaint_type in policy.auto_refund_complaint_types
        return RefundOutput(
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

- **Subscribes to**: `RefundResultMessage` AND `ProfileResultMessage`
- **Gate condition**: `refund`, `profile`, and `complaint` all set on board.
- **Output**: `ResolutionOutput` → `deps.board.resolution`
- **Publishes**: `ResolutionResultMessage`
- **Tools**: `log_decision` only — all context read from `deps.board`

`ResolutionAgent` is called twice per request — once when `RefundResultMessage` arrives and
once when `ProfileResultMessage` arrives. The same gate pattern as `RefundAgent` applies:
the first call exits silently, the second proceeds. The gate check is atomic — no locking
required.

```python
import json
from datetime import datetime
from agents.base_agent import BaseAgent
from core.deps import Deps
from schemas.messages.resolution_message import ResolutionResultMessage
from schemas.outputs.resolution_output import ResolutionOutput

class ResolutionAgent(BaseAgent):
    def __init__(self, name: str, agent: Agent):
        # 1. Run the BaseAgent constructor to assign self._name and self._agent
        super().__init__(name=name, agent=agent)
        
        # 2. Intercept the newly assigned self._agent and register the instructions
        @self._agent.system_prompt
        def assign_system_instructions(ctx) -> str:
            return self.get_instruction()


    def get_instruction(self) -> str:
        return """
            You are the ResolutionAgent.
            Determine the best resolution options for this customer request.

            You receive a full context of all findings from the board. Reason over all of them.

            Build options from: "refund" | "replacement" | "complaint_filing" | "escalation"
            - "refund"            if refund.eligible is True
            - "replacement"       if product_category is in replacement_eligible_categories
            - "complaint_filing"  if customer requested it OR severity is "high"
            - "escalation"        if previous_complaints >= complaint_escalation_threshold
                                  OR severity is "high" and refund is not eligible

            Set recommended to the single best option given all context.
            Set escalate_to_human True if "escalation" is in options.

            Call log_decision once. Return a ResolutionOutput.
        """

    async def handle(self, param: AgentParam) -> None:
        if (param.deps.board.refund is None
                or param.deps.board.profile is None
                or param.deps.board.complaint is None):
            return  # not all dependencies on board yet — exit silently

        context = json.dumps({
            "purchase":   param.deps.board.purchase.model_dump()  if deps.board.purchase  else {},
            "profile":    param.deps.board.profile.model_dump(),
            "complaint":  param.deps.board.complaint.model_dump(),
            "refund":     param.deps.board.refund.model_dump(),
            "policy": {
                "replacement_eligible_categories":
                    param.deps.policy.replacement_eligible_categories,
                "complaint_escalation_threshold":
                    param.deps.policy.complaint_escalation_threshold,
            },
        }, indent=2)

        result = await self._agent.run(
            f"Determine resolution options:\n{context}",
            deps=param.deps,
        )
        finding: ResolutionOutput = result.output
        deps.board.resolution = finding

        await deps.hub.publish(ResolutionResultMessage(
            triggered_by="resolution_agent",
            timestamp=datetime.now().isoformat(),
        ),deps=param.deps)
```

### 9.10 ResponseComposerAgent

- **Subscribes to**: `ResolutionResultMessage`
- **Responsibility**: Compose the final customer-facing response. All upstream findings are
  guaranteed present when this agent activates — `ResolutionAgent` only publishes after all
  its own gate conditions pass, and it publishes exactly once.
- **Output**: `ResponseOutput` → `deps.board.response`
- **Publishes**: nothing — terminal agent.
- **Tools**: `get_customer_profile`, `log_decision`

`ResponseComposerAgent` subscribes to a single message type published by a single agent.
It is called exactly once per request. No gate condition is needed.

```python
import json
from agents.base_agent import BaseAgent
from core.deps import Deps
from schemas.messages.resolution_message import ResolutionResultMessage
from schemas.outputs.response_output import ResponseOutput

class ResponseComposerAgent(BaseAgent):

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

            Call log_decision once. Return a ResponseOutput.
        """

    async def handle(self, param: AgentParam) -> None:
        context = json.dumps({
            "profile":    param.deps.board.profile.model_dump()   if param.deps.board.profile   else {},
            "complaint":  param.deps.board.complaint.model_dump() if param.deps.board.complaint else {},
            "purchase":   param.deps.board.purchase.model_dump()  if param.deps.board.purchase  else {},
            "refund":     param.deps.board.refund.model_dump()    if param.deps.board.refund    else {},
            "resolution": param.deps.board.resolution.model_dump(),
        }, indent=2)

        result = await self._agent.run(
            f"Compose a customer response:\n{context}",
            deps=param.deps,
            instructions=self.get_instruction(),
        )
        finding: ResponseOutput = result.output
        param.deps.board.response = finding
```

---

## 10. Service Layer

### 10.1 CustomerServiceHandler

**Location**: `service/handler.py`

`CustomerServiceHandler` owns the resolution cascade end-to-end. It is built once at startup
with no constructor arguments — it initialises the database pool, loads policy, builds the
repo facade, constructs LLM models, and builds all agents internally.

Per-request state (`MessageHub`, `Blackboard`, `Deps`) is created fresh inside `handle()` on
every call. Agents carry no per-request instance state and do not need to be reset between
requests.

```python
class CustomerServiceHandler:
    """
    Resolves a customer message end-to-end using the Observer fan-out pattern.

    Built once at startup. Agents and their underlying LLM wrappers are
    constructed once and reused across all calls.

    Per-request state (MessageHub, Blackboard, Deps) is created fresh inside
    handle() — never shared between calls.
    """

    def __init__(self) -> None:
        # -- gemini
        factory = LLMFactory("openrouter")
        self._gmodel = factory.get_model(model="")
        # -- openrouter
        factory = LLMFactory("openrouter")
        self._omodel = factory.get_model(model="nvidia/nemotron-3-super-120b-a12b:free")

        agents_list = self._build_agents()
        self._agents = {agent.name: agent for agent in agents_list}

    def _build_agents(self) -> list:
        """Construct all agents once. Agent instances are stateless across
        requests — all mutable state lives in Deps, which is per-request."""
        return [
            ProfileAgent(
                name="profile_agent",
                agent=Agent(
                    model=self._omodel, 
                    deps_type=Deps,
                    output_type=ProfileOutput,
                    tools=[log_decision, 
                           get_customer_profile,
                           get_customer_order_count,
                           get_recent_complaints, 
                           get_complaint_count],
                ),
            ),
            PurchaseAgent(
                name="purchase_agent",
                agent=Agent(
                    model=self._omodel, 
                    deps_type=Deps,
                    output_type=PurchaseOutput,
                    tools=[log_decision, 
                           get_order_summary,
                           get_order_line_items, 
                           get_order_total],
                ),
            ),
            RefundAgent(
                name="refund_agent",
                agent=Agent(
                    model=self._omodel, 
                    deps_type=Deps,
                    output_type=RefundOutput,
                    tools=[log_decision],
                ),
            ),
            ResolutionAgent(
                name="resolution_agent",
                agent=Agent(
                    model=self._omodel, 
                    deps_type=Deps,
                    output_type=ResolutionOutput,
                    tools=[log_decision],
                ),
            ),
            ResponseComposerAgent(
                name="response_agent",
                agent=Agent(
                    model=self._omodel, 
                    deps_type=Deps,
                    output_type=ResponseOutput,
                    tools=[log_decision, get_customer_profile],
                ),
            ),

            # ComplaintAgent(
            #     name="complaint_agemt",
            #     agent=Agent(
            #         model=self._omodel, 
            #         deps_type=Deps,
            #         output_type=ComplaintResult,
            #         tools=[log_decision],
            #     ),
            # ),
            # SentimentAgent(
            #     name="sentiment",
            #     agent=Agent(
            #         model=model, 
            #         deps_type=Deps,
            #         output_type=dict,
            #         tools=[log_decision],
            #     ),
            # ),
        ]

    async def handle(self, service_request: ServiceRequestMessage) -> dict:
        """Handle one customer message. Returns a result dict.

        Creates a fresh MessageHub, Blackboard, and Deps for this request.
        Resets stateful agents, subscribes all agents to the hub, then fires
        the single publish() call that triggers the entire agent cascade.
        """
        hub   = MessageHub()
        board = Blackboard()
        repo = FacadeRepos()

        policy_repo = PolicyRepository()
        policy = await policy_repo.get_policy()
        policy = Policy(**policy)

        deps  = Deps(
            hub=hub,
            repos=repo,
            board=board,
            policy=policy,

            message_id=service_request.message_id,
            customer_id=service_request.customer_id,
            order_id=service_request.order_id,

            total_tokens=0,
        )

        # =====================================================================
        # 100% CLEAN, IMPERATIVE SUBSCRIPTIONS
        # =====================================================================
        # Pass the agent methods directly to the hub. No wrappers needed!
        
        # Phase 1: Direct triggers from the initial customer request
        hub.subscribe(ServiceRequestMessage, self._agents["purchase_agent"].handle)
        hub.subscribe(ServiceRequestMessage, self._agents["profile_agent"].handle)
        # hub.subscribe(ServiceRequestMessage, self._agents["complaint_agent"].handle)
        # hub.subscribe(ServiceRequestMessage, self._agents["sentiment_agent"].handle)

        # Phase 2: Cascading downstream triggers
        hub.subscribe(PurchaseResultMessage, self._agents["refund_agent"].handle)
        hub.subscribe(ProfileResultMessage, self._agents["refund_agent"].handle)

        hub.subscribe(RefundResultMessage, self._agents["resolution_agent"].handle)
        hub.subscribe(ResolutionResultMessage, self._agents["response_agent"].handle)

        # hub.subscribe(ComplaintResultMessage, self._agents["refund_agent"].handle)
        # hub.subscribe(RefundResultMessage,    self._agents["resolution_agent"].handle)

        # =====================================================================
        # THE CASCADE EXECUTION
        # =====================================================================
        # logger.info(f"[{message.message_id}] Cascading event chain started.")
        
        # # We pass both the message and deps to the hub to kick off the domino effect
        await hub.publish(service_request, deps)
        
        # logger.info(f"[{message.message_id}] Cascading event chain complete.")

        response = board.response
        return {
            "resolved":      response.resolved if response else False,
            "response":      response.response if response else "System error",
            "actions_taken": response.actions_taken if response else [],
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

from core.llm_factory import LLMFactory
from services.customer_service import CustomerServiceHandler
from schemas.messages import ServiceRequestMessage

from agents.intake_agent import IntakeAgent


CUSTOMER_ID = "C001"   # in production: resolved from auth / session

async def main() -> None:
    handler = CustomerServiceHandler()

    # -- 1. prepare model
    factory = LLMFactory("openrouter")
    model = factory.get_model("nvidia/nemotron-3-super-120b-a12b:free")

    intakeAgent = IntakeAgent(model)

    # -- 2. main loop
    print("Agent: Hi, how can I help you today?")
    while True:
        # -- a. user chat
        user_input = input("Customer: ").strip()
        if not user_input:
            continue

        intake_result = await intakeAgent.collect(user_input, customer_id=CUSTOMER_ID)
        print(f"Agent: {intake_result.reply}")

        if not intake_result.ready:
            continue

        # -- b process service request
        # IntakeAgent has collected order_id and a complete complaint description.
        # Hand off to CustomerServiceHandler for the full resolution cascade.
        message = ServiceRequestMessage(
            message_id=str(uuid.uuid4()),
            customer_id=CUSTOMER_ID,
            order_id=intake_result.order_id,
            message=intake_result.message,
            triggered_by="intake_agent",
            timestamp=datetime.now().isoformat(),
        )
        # print(message.model_dump())

        # -- c. print results
        result = await handler.handle(message)
        print(json.dumps(result, indent=2))


asyncio.run(main())
```

---

## 12. Implementation Sequence

**Phase 1 — Database** (`db/`)

Run `schema.sql`. Verify all tables and data. Test `Database.get_pool()` returns the same
pool object on repeated calls — confirming singleton behaviour. Test each repository method
directly: `get_order` with mismatched `customer_id` returns `None`, `get_history` with
`limit=10` never exceeds 10 rows, `PolicyRepository.get()` returns a valid `Policy` object.

**Phase 2 — Schemas** (`schemas/`)

Instantiate each schema with dummy data, round-trip `model_dump()` / `model_validate()`.
All message schemas carry `triggered_by` and `timestamp`. `ServiceRequestMessage` additionally
carries `message_id`, `customer_id`, `order_id`, `message`. Confirm `IntakeResult`
round-trips with both `ready=False` (order_id and message are None) and `ready=True`
(all fields populated).

**Phase 3 — MessageHub** (`core/message_hub.py`)

Verify fan-out with a standalone script: three dummy async handlers subscribed to the same
message type, one `publish()` call, confirm all three fire. Confirm handlers for other message
types are not called.

**Phase 4 — Core** (`core/`)

Build `Blackboard` and `Deps`. Confirm `Blackboard.is_complete()` is `False` until `response`
is set.

**Phase 5 — Tools** (`tools/`)

Test each tool directly with a live `RepoFacade`: confirm `get_order_summary` returns the
error dict for a non-existent order, `get_recent_complaints` never returns more than 10 rows.

**Phase 6 — RefundAgent** (`agents/`)

Build first — no LLM. Test `_check()` directly against all branches: purchase not verified,
outside window, auto-approved type, standard eligible. Manually trigger `handle()` with one
board field missing — confirm silent return. Set both fields and trigger again — confirm
`RefundResultMessage` is published exactly once.

**Phase 7 — Phase 1 resolution agents** (`agents/`)

Build in order: `ComplaintAgent` → `PurchaseAgent` → `ProfileAgent` → `SentimentAgent`.
For each: subscribe to a live hub with deps, call `hub.publish(ServiceRequestMessage)`,
confirm output posted to `deps.board` and lean message published to hub.

**Phase 8 — Phase 2 resolution agents** (`agents/`)

Build in order: `ResolutionAgent` → `ResponseComposerAgent`. Confirm gate exits silently when
findings missing. With all required findings set, confirm agent activates, calls LLM, posts
finding and publishes exactly once.

**Phase 9 — CustomerServiceHandler** (`service/`)

Test with a fully formed `ServiceRequestMessage` (bypassing intake). Confirm: the 4 handlers for
`ServiceRequestMessage` start concurrently, `log_decision` lines appear for all LLM agents,
`RefundAgent` produces no token lines, `ResponseOutput` is the final output with `resolved=True`.

**Phase 10 — IntakeAgent** (`agents/`)

Test `collect()` in isolation with a sequence of simulated customer turns. Confirm
`ready=False` until both `order_id` and a complete complaint are present. Confirm
`ready=True` populates `order_id` and `message`. Confirm `_history` grows correctly
across turns — the LLM sees the full thread on every call.

**Phase 11 — Integration** (`main.py`)

Run end-to-end with a multi-turn chat. Confirm: `IntakeAgent` asks one question at a time,
`ready=True` is only signalled when both `order_id` and complaint are collected,
`CustomerServiceHandler` fires the cascade exactly once on the summarised `message`, and
the final `ResponseOutput` is printed with `resolved=True`.

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