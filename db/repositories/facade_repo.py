
from .complaint_repo import ComplaintRepository
from .order_repo import OrderRepository
from .customer_repo import CustomerRepository
from .policy_repo import PolicyRepository

class FacadeRepos:
    def __init__(self):
        # Explicit, imperative instantiation of your repository classes
        self.customer = CustomerRepository()
        self.order = OrderRepository()
        self.complaint = ComplaintRepository()
        self.policy = PolicyRepository()