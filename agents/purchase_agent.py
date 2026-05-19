
from .base_agent import BaseAgent
from core.deps import Deps
from core.message_hub import MessageHub
from schemas.messages import CustomerMessageContract, ServiceRequestContract

class PurchaseAgent(BaseAgent):

    def subscribe(self, hub: MessageHub, deps: Deps) -> None:
        async def handler(event):
            await self.handle(event, deps)

        hub.subscribe(CustomerMessageContract, handler)


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

            Call log_decision once. 
            Return a PurchaseVerifiedContract.
        """


    async def handle(self, event: ServiceRequestContract, deps: Deps) -> None:
        result = await self._agent.run(f"""
                Verify purchase for order {deps.order_id} by customer {deps.customer_id}
                Customer message: {event.message}
            """,
            deps=deps,
            instructions=self.get_instruction(),
        )
        finding: ServiceRequestContract = result.output
        deps.board.purchase = finding
        
        # -- rightfully we should be publising a contract 
        await deps.hub.publish(finding)

