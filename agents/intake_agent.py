import logging
from pydantic_ai import Agent

from schemas.intake_result import IntakeResult

logger = logging.getLogger(__name__)

class IntakeAgent:
    """Conversational intake agent. Sits in front of CustomerServiceHandler.

    Holds a multi-turn conversation with the customer until it has collected
    the two things the resolution cascade requires:

        - order_id     — extracted from what the customer says
        - message      — a complete, coherent description of the complaint

    customer_id is already known from the session and is not collected here.

    On each turn, collect() returns an IntakeResult:
        ready=False → still gathering; show reply to the customer and wait
        ready=True  → hand off to CustomerServiceHandler with order_id + message

    The agent never touches MessageHub, Deps, or Blackboard. It is entirely
    outside the observer cascade and has no knowledge of it.
    """

    def __init__(self, model) -> None:
        self._agent   = Agent(
            model=model,
            output_type=IntakeResult,
        )
        self._history = []   # pydantic-ai message history — grows across turns

    def _get_instruction(self, customer_id: str) -> str:
        return f"""
            You are a customer service intake agent for an e-commerce platform.
            The customer's ID is {customer_id}. Do not ask for it.

            Your only job is to gather enough information to handle the customer's
            complaint. You need exactly two things:

            1. order_id  — the order reference number (format: ORD-XXXX).
                           Ask for it if the customer has not provided it.
            2. message   — a complete, self-contained description of the complaint
                           that covers: what happened, which product or order,
                           and what outcome the customer is seeking (refund,
                           replacement, complaint filed, etc.).

            Rules:
            - Ask for one thing at a time. Never ask two questions in one reply.
            - Be warm, concise, and professional. Do not use jargon.
            - Once you have both order_id and a complete complaint description,
              set ready=True. Summarise the full complaint into message in plain
              language (2-4 sentences). Set reply to a brief acknowledgement
              that you are looking into it now.
            - If you do not yet have both, set ready=False and set reply to
              your next question. Leave order_id and message as null.
            - Never make up or assume an order_id. If the customer is vague
              ("my last order", "order from last week"), ask them to confirm
              the order number.
            - Do not attempt to resolve the complaint yourself. Do not offer
              refunds, decisions, or outcomes. Your job ends when ready=True.
        """

    async def collect(self, user_input: str, customer_id: str) -> IntakeResult:
        """Process one customer turn. Returns IntakeResult.

        Call repeatedly until result.ready is True, then hand off to
        CustomerServiceHandler using result.order_id and result.message.
        """
        logger.info(f"[intake] customer={customer_id} input='{user_input[:60]}...'")

        result = await self._agent.run(
            user_input,
            message_history=self._history,
            instructions=self._get_instruction(customer_id),
        )

        self._history = result.all_messages()

        logger.info(
            f"[intake] ready={result.output.ready} "
            f"order_id={result.output.order_id}"
        )

        return result.output