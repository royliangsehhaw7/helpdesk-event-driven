from datetime import datetime

from agents.base_agent import BaseAgent
from core.message_hub import MessageHub
from core.deps import Deps

from schemas.outputs.complaint_output import ComplaintOutput
from schemas.messages import ServiceRequestMessage, ComplaintResultMessage 


class ComplaintAgent(BaseAgent):

    def subscribe(self, hub: MessageHub, deps: Deps) -> None:
        async def handler(message):
            await self.handle(message, deps)
        hub.subscribe(ServiceRequestMessage, handler)

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
            instructions=self.get_instruction(),
        )
        finding: ComplaintOutput = result.output
        deps.board.complaint = finding

        await deps.hub.publish(ComplaintResultMessage(
            triggered_by="complaint_agent",
            timestamp=datetime.now().isoformat(),
        ))