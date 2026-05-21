from datetime import datetime

from pydantic_ai import Agent
from agents.base_agent import BaseAgent

from schemas.agent_param import AgentParam
from schemas.messages import ServiceRequestMessage, PurchaseResultMessage
from schemas.outputs.purchase_output import PurchaseOutput


class PurchaseAgent(BaseAgent):
    def __init__(self, name: str, agent: Agent):
        # 1. Run the BaseAgent constructor to assign self._name and self._agent
        super().__init__(name=name, agent=agent)
        
        # 2. Intercept the newly assigned self._agent and register the instructions
        @self._agent.system_prompt
        def assign_system_instructions(ctx) -> str:
            return self.get_instruction()    

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

    async def handle(self, param: AgentParam) -> None:
        result = await self._agent.run(
            f"""
                Verify purchase for order {param.deps.order_id}
                by customer {param.deps.customer_id}.
                Customer message: {param.message.message}
            """,
            deps=param.deps,
        )
        # 1. write full rich output to blackboard
        finding: PurchaseOutput = result.output
        param.deps.board.purchase = finding

        # 2. publish lean message to hub
        await param.deps.hub.publish(PurchaseResultMessage(
            triggered_by="purchase_agent",
            timestamp=datetime.now().isoformat(),
        ),
        deps = param.deps)