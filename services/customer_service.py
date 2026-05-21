
from typing import Any
from pydantic_ai import Agent

from db.repositories.facade_repo import FacadeRepos, PolicyRepository

from core import (
    Deps, 
    LLMFactory, 
    MessageHub, 
    Blackboard, 
    logger
)
from agents import PurchaseAgent, ProfileAgent, RefundAgent, ResolutionAgent, ResponseComposerAgent

from schemas.messages import (
    ServiceRequestMessage, 
    PurchaseResultMessage, 
    ProfileResultMessage, 
    RefundResultMessage,
    ResolutionResultMessage
)
from schemas.outputs import ProfileOutput, PurchaseOutput, ResolutionOutput, RefundOutput, ResponseOutput
from schemas.data.policy import Policy

from tools.agent_logger import log_decision
from tools.customer_tools import (
    get_customer_profile
)
from tools.order_tools import (
    get_order_summary, 
    get_order_line_items,
    get_order_total, 
    get_customer_order_count,
)
from tools.complaint_tools import (
    get_recent_complaints, 
    get_complaint_count
)

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