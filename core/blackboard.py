from dataclasses import dataclass
from schemas.outputs import (
    PurchaseOutput, 
    ProfileOutput, 
    ComplaintOutput, 
    RefundOutput,
    ResolutionOutput,
    ResponseOutput
)

@dataclass
class Blackboard:
    """
    Result accumulator for one request lifecycle.

    Agents write here after their LLM call completes.
    Downstream agents read here to check gate conditions.
    One instance per CustomerResponseContract. Discarded when complete.
    """

    purchase:           PurchaseOutput   | None = None
    profile:            ProfileOutput    | None = None
    complaint_type:     ComplaintOutput  | None = None
    refund_eligibility: RefundOutput     | None = None
    resolution:         ResolutionOutput | None = None
    response:           ResponseOutput   | None = None


    def is_complete(self) -> bool:
        return self.response is not None
    