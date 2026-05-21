from datetime import datetime
from pydantic_ai import Agent

from agents.base_agent import BaseAgent

from schemas.agent_param import AgentParam
from schemas.outputs.complaint_output import ComplaintOutput
from schemas.messages import ServiceRequestMessage, ComplaintResultMessage 


class ComplaintAgent(BaseAgent):
    def __init__(self, name: str, agent: Agent):
        # 1. Run the BaseAgent constructor to assign self._name and self._agent
        super().__init__(name=name, agent=agent)
        
        # 2. Intercept the newly assigned self._agent and register the instructions
        @self._agent.system_prompt
        def assign_system_instructions(ctx) -> str:
            return self.get_instruction()
    
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

    async def handle(self, param: AgentParam) -> None:
        result = await self._agent.run(
            f"Classify this complaint: {param.message.message}",
            deps=param.deps
        )
        finding: ComplaintOutput = result.output
        param.deps.board.complaint = finding

        await param.deps.hub.publish(ComplaintResultMessage(
            triggered_by="complaint_agent",
            timestamp=datetime.now().isoformat(),
        ),
        deps = param.deps)