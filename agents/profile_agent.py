from datetime import datetime
from pydantic_ai import Agent

from agents.base_agent import BaseAgent

from schemas.agent_param import AgentParam
from schemas.outputs.profile_output import ProfileOutput
from schemas.messages import ServiceRequestMessage, ProfileResultMessage


class ProfileAgent(BaseAgent):
    def __init__(self, name: str, agent: Agent):
        # 1. Run the BaseAgent constructor to assign self._name and self._agent
        super().__init__(name=name, agent=agent)
        
        # 2. Intercept the newly assigned self._agent and register the instructions
        @self._agent.system_prompt
        def assign_system_instructions(ctx) -> str:
            return self.get_instruction()    

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

    async def handle(self, param: AgentParam) -> None:
        result = await self._agent.run(
            f"""
                Build profile for customer {param.deps.customer_id}.
                Message context: {param.message.message} 
            """,
            deps=param.deps,
        )
        finding: ProfileOutput = result.output
        param.deps.board.profile = finding

        await param.deps.hub.publish(ProfileResultMessage(
            triggered_by="profile_agent",
            timestamp=datetime.now().isoformat(),
        ),
        deps = param.deps)