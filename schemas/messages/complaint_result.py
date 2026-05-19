from pydantic import BaseModel

class ComplaintResultContract(BaseModel):
    message_id: str
    complaint_type: str               # "packaging"|"product"|"delivery"|"billing"
    severity: str                     # "low"|"medium"|"high"
    keywords: list[str]
    wants_refund: bool
    wants_replacement: bool
    wants_complaint_filed: bool