import json
from datetime import datetime
from pydantic_ai import Agent

from agents.base_agent import BaseAgent
from schemas.agent_param import AgentParam
from schemas.outputs import ResolutionOutput
from schemas.messages import ResolutionResultMessage

class ResolutionAgent(BaseAgent):
    def __init__(self, name: str, agent: Agent):
        # 1. Run the BaseAgent constructor to assign self._name and self._agent
        super().__init__(name=name, agent=agent)
        
        # 2. Intercept the newly assigned self._agent and register the instructions
        @self._agent.system_prompt
        def assign_system_instructions(ctx) -> str:
            return self.get_instruction()

    def get_instruction(self) -> str:
        return """
            You are the ResolutionAgent.
            Determine the best resolution options for this customer request.

            You receive a full context of all findings from the board. Reason over all of them.

            Build options from: "refund" | "replacement" | "complaint_filing" | "escalation"
            - "refund"            if refund.eligible is True
            - "replacement"       if product_category is in replacement_eligible_categories
            - "complaint_filing"  if customer requested it OR severity is "high"
            - "escalation"        if previous_complaints >= complaint_escalation_threshold
                                  OR severity is "high" and refund is not eligible

            Set recommended to the single best option given all context.
            Set escalate_to_human True if "escalation" is in options.

            Call log_decision once. Return a ResolutionOutput.
        """

    async def handle(self, param: AgentParam) -> None:
        # if (param.deps.board.refund is None
        #         or param.deps.board.profile is None
        #         or param.deps.board.complaint is None):
        #     return  # not all dependencies on board yet — exit silently
        if (param.deps.board.refund is None
                or param.deps.board.profile is None):
            return  # not all dependencies on board yet — exit silently

        context = json.dumps({
            "purchase":   param.deps.board.purchase.model_dump()  if param.deps.board.purchase  else {},
            "profile":    param.deps.board.profile.model_dump(),
            # "complaint":  param.deps.board.complaint.model_dump(),
            "refund":     param.deps.board.refund.model_dump(),
            "policy": {
                "replacement_eligible_categories":
                    param.deps.policy.replacement_eligible_categories,
                "complaint_escalation_threshold":
                    param.deps.policy.complaint_escalation_threshold,
            },
        }, indent=2)

        result = await self._agent.run(
            f"Determine resolution options:\n{context}",
            deps=param.deps,
        )
        finding: ResolutionOutput = result.output
        param.deps.board.resolution = finding

        await param.deps.hub.publish(ResolutionResultMessage(
            triggered_by="resolution_agent",
            timestamp=datetime.now().isoformat(),
        ),
        deps = param.deps)