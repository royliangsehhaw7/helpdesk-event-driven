
from typing import Any
from pydantic_ai import Agent

from db.repositories.facade_repo import FacadeRepos, PolicyRepository

from core import Deps, LLMFactory, MessageHub, Blackboard, logger
from agents import PurchaseAgent, ProfileAgent, ComplaintAgent, SentimentAgent

from schemas.messages import ServiceRequestMessage
from schemas.outputs import ProfileOutput, PurchaseOutput

from tools.agent_logger import log_decision
from tools.customer_tools import get_customer_profile
from tools.order_tools import (
    get_order_summary, 
    get_order_line_items,
    get_order_total, 
    get_customer_order_count,
)
from tools.complaint_tools import get_recent_complaints, get_complaint_count


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
        self._omodel = factory.get_model(model="")

        self._agents = self._build_agents()


    def _build_agents(self) -> list:
        """Construct all agents once. Agent instances are stateless across
        requests — all mutable state lives in Deps, which is per-request."""
        return [
            ProfileAgent(
                name="customer_history",
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
                name="purchase_verification",
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
            # RefundEligibilityAgent(
            #     name="resolution",
            #     agent=Agent(
            #         model=make_model(PROVIDER), deps_type=Deps,
            #         output_type=ResolutionResult,
            #         tools=[log_decision],
            #     ),
            # ),
            # ResolutionAgent(
            #     name="resolution",
            #     agent=Agent(
            #         model=make_model(PROVIDER), deps_type=Deps,
            #         output_type=ResolutionResult,
            #         tools=[log_decision],
            #     ),
            # ),
            # ResponseComposerAgent(
            #     name="response_composer",
            #     agent=Agent(
            #         model=make_model(PROVIDER), deps_type=Deps,
            #         output_type=CustomerResponse,
            #         tools=[log_decision, get_customer_profile],
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

        # Reset stateful agents and build the subscription dictionary.
        # Each agent appends its handler to the list for its contract type.
        # Nothing runs here — the hub is just wired up.
        for agent in self._agents:
            agent.reset()
            agent.subscribe(hub, deps)

        # Single publish call triggers the entire cascade.
        # Does not return until deps.board.response is set.
        logger.info(f"[{service_request.message_id}] cascade start")
        await hub.publish(service_request)
        logger.info(f"[{service_request.message_id}] cascade complete")

        response = deps.board.response
        return {
            "resolved":      response.resolved      if response else False,
            "response":      response.response       if response else "System error",
            "actions_taken": response.actions_taken  if response else [],
            "total_tokens":  deps.total_tokens,
        }