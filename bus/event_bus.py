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


# ===== imperative version
import asyncio

class EventBus:
    def __init__(self):
        # A plain, standard dictionary.
        # Key: A Python class type (e.g., UserRegisteredEvent)
        # Value: A list containing function objects
        self._subscribers = {}

    def subscribe(self, event_type, handler):
        # Check if this event type is already a key in our dictionary
        if event_type not in self._subscribers:
            # If it's not there, create an empty list for it
            self._subscribers[event_type] = []
        
        # Grab the list for this event type and append the function to it
        self._subscribers[event_type].append(handler)

    async def publish(self, event):
        # Get the actual class type of the incoming object
        # Equivalent to event.GetType() in C#
        event_type = type(event)
        
        # Look up the list of subscriber functions for this specific type
        # If no one subscribed, default to an empty list []
        handlers = self._subscribers.get(event_type, [])
        
        # If the list is empty, there is nothing to do. Exit early.
        if not handlers:
            return

        # Build a list to hold our unstarted tasks (like cold C# Tasks)
        tasks_to_run = []
        
        # Loop through every subscriber function one by one
        for handler in handlers:
            # Call the async function passing the event data.
            # Crucial: This does NOT execute the function yet!
            # It just creates a "coroutine" object (an unstarted task).
            coroutine_task = handler(event)
            
            # Put that unstarted task into our tracking list
            tasks_to_run.append(coroutine_task)

        # Hand the entire list of tasks to the asyncio engine.
        # Equivalent to: await Task.WhenAll(tasks_to_run)
        # This is where the single thread begins executing them.
        await asyncio.gather(*tasks_to_run)