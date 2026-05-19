
from .base_agent import BaseAgent
from core.deps import Deps
from core.message_hub import MessageHub
from schemas.contracts.customer_message import CustomerMessageContract
from schemas.contracts.purchase_result import PurchaseVerifiedContract

class PurchaseVerificationAgent(BaseAgent):

    def subscribe(self, hub: MessageHub, deps: Deps) -> None:
        async def handler(event):
            await self.handle(event, deps)

        hub.subscribe(CustomerMessageContract, handler)


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

            Call log_decision once. 
            Return a PurchaseVerifiedContract.
        """


    async def handle(self, event: CustomerMessageContract, deps: Deps) -> None:
        result = await self._agent.run(f"""
            Verify purchase for order {deps.order_id}
            by customer {deps.customer_id}
            Customer message: {event.message}
            """,
            deps=deps,
            instructions=self.get_instruction(),
        )
        finding: PurchaseVerifiedContract = result.output
        deps.board.purchase = finding
        
        await deps.hub.publish(finding)
