from datetime import datetime

from agents.base_agent import BaseAgent
from core.message_hub import MessageHub
from core.deps import Deps

from schemas.messages import ServiceRequestMessage, PurchaseResultMessage
from schemas.outputs.purchase_output import PurchaseOutput


class PurchaseAgent(BaseAgent):

    def subscribe(self, hub: MessageHub, deps: Deps) -> None:
        async def handler(message):
            await self.handle(message, deps)
        hub.subscribe(ServiceRequestMessage, handler)

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

    async def handle(self, message: ServiceRequestMessage, deps: Deps) -> None:
        result = await self._agent.run(
            f"Verify purchase for order {deps.order_id} "
            f"by customer {deps.customer_id}. "
            f"Customer message: {message.message}",
            deps=deps,
            instructions=self.get_instruction(),
        )
        # 1. write full rich output to blackboard
        finding: PurchaseOutput = result.output
        deps.board.purchase = finding

        # 2. publish lean message to hub
        await deps.hub.publish(PurchaseResultMessage(
            triggered_by="purchase_agent",
            timestamp=datetime.now().isoformat(),
        ))