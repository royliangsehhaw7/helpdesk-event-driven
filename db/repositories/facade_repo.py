from typing import field

from dataclasses import dataclass
from .complaint_repo import ComplaintRepository
from .order_repo import OrderRepository
from .customer_repo import CustomerRepository
from .policy_repo import PolicyRepository

@dataclass
class FacadeRepos():
    customer:  CustomerRepository  = field(default_factory=CustomerRepository)
    order:     OrderRepository     = field(default_factory=OrderRepository)
    complaint: ComplaintRepository = field(default_factory=ComplaintRepository)
    policy:    PolicyRepository    = field(default_factory=PolicyRepository)