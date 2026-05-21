# import asyncio
# from collections import defaultdict
# from typing import Callable
# from pydantic import BaseModel

# class Dispatcher:
#     """Pure Observer fan-out. Zero domain knowledge.

#     subscribe(event_type, handler):
#         Register an async handler for an event type.
#         Handler signature: async def handler(event: BaseModel) -> None
#         Called once per agent per event type at startup.
#     publish(event):
#         Fan out to all registered handlers for type(event) via asyncio.gather().
#         Does not return until all handlers (and their downstream publishes) complete.
#         Handlers for other event types are never called.
#     """
#     def __init__(self):
#         self._subscribers: dict[type, list[Callable]] = defaultdict(list)
#     def subscribe(self, event_type: type, handler: Callable) -> None:
#         self._subscribers[event_type].append(handler)
#     async def publish(self, event: BaseModel) -> None:
#         handlers = self._subscribers.get(type(event), [])
#         if handlers:
#             await asyncio.gather(*[h(event) for h in handlers])


# ===== imperative version
import asyncio
from pydantic import BaseModel
from typing import Callable, Any
from collections import defaultdict

from schemas.agent_param import AgentParam

class MessageHub:
    def __init__(self):
        # Maps event message types directly to the raw agent handle methods
        self._subscribers: dict[type, list[Callable]] = defaultdict(list)

    def subscribe(self, message_type: type, handler: Callable) -> None:
        """Register an agent method directly."""
        self._subscribers[message_type].append(handler)

    async def publish(self, message: BaseModel, deps: Any) -> None:
        """Broadcast event to subscribers, automatically packaging data into AgentParam."""
        handlers = self._subscribers.get(type(message), [])
        if not handlers:
            return

        # Automatically package the message and request deps for every handler
        param = AgentParam(message=message, deps=deps)
        
        # Fire all agents concurrently via the native event loop
        await asyncio.gather(*[handler(param) for handler in handlers])