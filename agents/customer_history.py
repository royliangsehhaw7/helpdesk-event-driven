from agents.base_agent import BaseAgent
from core.deps import Deps
from core.message_hub import MessageHub

from schemas.contracts.customer_message import CustomerMessageContract
from schemas.contracts.profile_result import CustomerProfileContract

class CustomerHistoryAgent(BaseAgent):

    def subscribe(self, hub: MessageHub, deps: Deps) -> None:
        async def handler(event):
            await self.handle(event, deps)
        hub.subscribe(CustomerMessageContract, handler)

    def get_instruction(self) -> str:
        return """
            You are the CustomerHistoryAgent in a customer service system.
            Build a profile of this customer from their history.

            Use your tools to:
            - Retrieve customer profile and tier (get_customer_profile)
            - Retrieve total order count (get_customer_order_count)
            - Retrieve recent complaint history (get_recent_complaints)
            - Retrieve total complaint count (get_complaint_count)

            Determine:
            - Tier (standard / premium / vip)
            - Total orders placed
            - Total previous complaints
            - is_repeat_issue — has this customer complained about the same type before?
              Infer the complaint type from the customer's message to check history.

            Initialise sentiment_score=0.0, sentiment_label="neutral".
            SentimentAgent will update these fields independently.

            Call log_decision once. Return a CustomerProfileContract.
        """

    async def handle(self, event: CustomerMessageContract, deps: Deps) -> None:
        result = await self._agent.run(
            f"Build profile for customer {deps.customer_id}. "
            f"Message context: {event.message}",
            deps=deps,
            instructions=self.get_instruction(),
        )
        finding: CustomerProfileContract = result.output
        deps.board.profile = finding
        
        await deps.hub.publish(finding)