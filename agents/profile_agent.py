from datetime import datetime

from agents.base_agent import BaseAgent
from core.message_hub import MessageHub
from core.deps import Deps

from schemas.messages import ServiceRequestMessage, ProfileResultMessage
from schemas.outputs.profile_output import ProfileOutput


class ProfileAgent(BaseAgent):

    def subscribe(self, hub: MessageHub, deps: Deps) -> None:
        async def handler(message):
            await self.handle(message, deps)
            
        hub.subscribe(ServiceRequestMessage, handler)

    def get_instruction(self) -> str:
        return """
            You are the ProfileAgent in a customer service system.
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

            Call log_decision once. Return a ProfileOutput.
        """

    async def handle(self, message: ServiceRequestMessage, deps: Deps) -> None:
        result = await self._agent.run(
            f"""
                Build profile for customer {deps.customer_id}.
                Message context: {message.message} 
            """,
            deps=deps,
            instructions=self.get_instruction(),
        )
        finding: ProfileOutput = result.output
        deps.board.profile = finding

        await deps.hub.publish(ProfileResultMessage(
            triggered_by="profile_agent",
            timestamp=datetime.now().isoformat(),
        ))