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

class CustomerServiceHandler:
    """
    Resolves a customer message end-to-end using the Observer fan-out pattern.

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
                    model=model, 
                    deps_type=Deps,
                    output_type=PurchaseResult,
                    tools=[log_decision, 
                           get_order_summary,
                           get_order_line_items, 
                           get_order_total],
                ),
            ),
            CustomerHistoryAgent(
                name="customer_history",
                agent=Agent(
                    model=model, 
                    deps_type=Deps,
                    output_type=ProfileResult,
                    tools=[log_decision, 
                           get_customer_profile,
                           get_customer_order_count,
                           get_recent_complaints, 
                           get_complaint_count],
                ),
            ),
            ComplaintClassificationAgent(
                name="complaint_classification",
                agent=Agent(
                    model=model, 
                    deps_type=Deps,
                    output_type=ComplaintResult,
                    tools=[log_decision],
                ),
            ),
            SentimentAgent(
                name="sentiment",
                agent=Agent(
                    model=model, 
                    deps_type=Deps,
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
        hub   = MessageHub()
        board = Blackboard()
        deps  = Deps(
            repo=self._repo,
            hub=hub,
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
            agent.subscribe(hub, deps)

        # Single publish call triggers the entire cascade.
        # Does not return until deps.board.response is set.
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