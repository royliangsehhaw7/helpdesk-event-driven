from dataclasses import dataclass
from schemas.messages import (
    PurchaseResultContract, 
    ProfileResultContract,
    ComplaintResultContract,
    RefundResultContract,
    ResolutionResultContract,
    CustomerResponseContract
)

@dataclass
class Blackboard:
    """
    Result accumulator for one request lifecycle.

    Agents write here after their LLM call completes.
    Downstream agents read here to check gate conditions.
    One instance per CustomerResponseContract. Discarded when complete.
    """

    purchase:           PurchaseResultContract   | None = None
    profile:            ProfileResultContract    | None = None
    complaint_type:     ComplaintResultContract  | None = None
    refund_eligibility: RefundResultContract     | None = None
    resolution:         ResolutionResultContract | None = None
    response:           CustomerResponseContract | None = None


    def is_complete(self) -> bool:
        return self.response is not None
    