from datetime import datetime
from pydantic_ai import Agent

from agents.base_agent import BaseAgent
from schemas.agent_param import AgentParam
from schemas.outputs import RefundOutput
from schemas.messages import RefundResultMessage

class RefundAgent(BaseAgent):
       
    def get_instruction(self) -> str:
        return "Deterministic refund rules processor."

    async def handle(self, param: AgentParam) -> None:
        # One dependency not yet on board — exit silently
        if param.deps.board.purchase is None:
            return  

        # 1. Run deterministic rule evaluation 
        # (Pass None for complaint if it's currently commented out/disabled)
        finding: RefundOutput = self._check(
            purchase=param.deps.board.purchase,
            complaint=getattr(param.deps.board, 'complaint', None), 
            policy=param.deps.policy
        )
        
        # Write full rich output to the blackboard
        param.deps.board.refund = finding

        # 2. Publish lean message to hub (with deps appended to fix the signature error)
        await param.deps.hub.publish(
            RefundResultMessage(
                triggered_by="refund_agent",
                timestamp=datetime.now().isoformat(),
            ),
            param.deps  # <-- Keeps it 100% consistent with MessageHub expectations
        )

    def _check(self, purchase, complaint, policy) -> RefundOutput:
        if not purchase.verified:
            return RefundOutput(
                eligible=False,
                reason="Purchase could not be verified",
            )

        window = policy.refund_window_days
        days   = purchase.days_since_purchase or 0

        if days > window:
            return RefundOutput(
                eligible=False,
                reason=(
                    f"Purchase is {days} days old. "
                    f"Refund window is {window} days."
                ),
            )

        # Safeguard if complaint check is skipped/None
        if complaint is None:
            return RefundOutput(
                eligible=True,
                reason="Within refund window.",
                refund_amount=purchase.order_total,
            )

        auto = complaint.complaint_type in policy.auto_refund_complaint_types
        return RefundOutput(
            eligible=True,
            reason=(
                f"Auto-approved: '{complaint.complaint_type}' qualifies under policy."
                if auto else
                "Within refund window. Complaint type is eligible."
            ),
            refund_amount=purchase.order_total,
        )