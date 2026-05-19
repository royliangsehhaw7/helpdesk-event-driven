from agents.base_agent import BaseAgent
from core.message_hub import MessageHub
from core.deps import Deps

from schemas.contracts.customer_message import CustomerMessageContract
from schemas.contracts.complaint_result import ComplaintTypeContract

class ComplaintClassificationAgent(BaseAgent):

    def subscribe(self, hub: MessageHub, deps: Deps) -> None:
        async def handler(event):
            await self.handle(event, deps)
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

    async def handle(self, event: CustomerMessageContract, deps: Deps) -> None:
        result = await self._agent.run(
            f"Classify this complaint: {event.message}",
            deps=deps,
            instructions=self.get_instruction(),
        )
        finding: ComplaintTypeContract = result.output
        deps.board.complaint_type = finding
        await deps.hub.publish(finding)