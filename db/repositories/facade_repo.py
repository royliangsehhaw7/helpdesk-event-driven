from pydantic import Field
from dataclasses import dataclass

from .complaint_repo import ComplaintRepository
from .order_repo import OrderRepository
from .customer_repo import CustomerRepository
from .policy_repo import PolicyRepository

@dataclass
class FacadeRepos():
    customer:  CustomerRepository  = Field(default_factory=CustomerRepository)
    order:     OrderRepository     = Field(default_factory=OrderRepository)
    complaint: ComplaintRepository = Field(default_factory=ComplaintRepository)
    policy:    PolicyRepository    = Field(default_factory=PolicyRepository)