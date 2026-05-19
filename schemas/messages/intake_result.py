from pydantic import BaseModel

class IntakeResultMessage(BaseModel):
    """
    Output contract for IntakeAgent.

    ready=False — still gathering. reply is the next question to ask the customer.
                  order_id and message are None.
    ready=True  — enough context collected. reply is a brief handoff acknowledgement.
                  order_id and message are fully populated.
    """
    ready:    bool
    reply:    str                   # always set — shown to customer every turn
    order_id: str | None = None
    message:  str | None = None     # full summarised complaint, set when ready=True
