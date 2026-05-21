import json

from agents.base_agent import BaseAgent

from schemas.agent_param import AgentParam
from schemas.outputs.response_output import ResponseOutput

class ResponseComposerAgent(BaseAgent):

    def get_instruction(self) -> str:
        return """
            You are the ResponseComposerAgent.
            Compose a warm, clear, professional response to the customer.

            Use get_customer_profile to retrieve the customer's name for personalisation.

            Guidelines:
            - Address the customer by name
            - Acknowledge their specific complaint — reference what they described
            - State the resolution clearly — what happens and when
            - If escalating, explain what the customer should expect
            - Match tone to sentiment:
                "angry" or "frustrated"  → empathetic and apologetic opening
                "neutral"                → professional and direct
                "satisfied"              → warm and efficient
            - Plain language only. No corporate jargon.
            - Under 150 words.

            Call log_decision once. Return a ResponseOutput.
        """

    async def handle(self, param: AgentParam) -> None:
        context = json.dumps({
            "profile":    param.deps.board.profile.model_dump()   if param.deps.board.profile   else {},
            "complaint":  param.deps.board.complaint.model_dump() if param.deps.board.complaint else {},
            "purchase":   param.deps.board.purchase.model_dump()  if param.deps.board.purchase  else {},
            "refund":     param.deps.board.refund.model_dump()    if param.deps.board.refund    else {},
            "resolution": param.deps.board.resolution.model_dump(),
        }, indent=2)

        result = await self._agent.run(
            f"Compose a customer response:\n{context}",
            deps=param.deps,
            instructions=self.get_instruction(),
        )
        finding: ResponseOutput = result.output
        param.deps.board.response = finding