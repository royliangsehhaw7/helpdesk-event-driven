import asyncio
import json
import uuid
from datetime import datetime

from db.connection import Database
# from db.repositories.facade import RepoFacade
# from db.repositories.policy_repo import PolicyRepository

from core.llm_factory import LLMFactory
from schemas.contracts.customer_message import CustomerMessageContract

from agents.intake_agent import IntakeAgent
# from service.handler import CustomerServiceHandler

CUSTOMER_ID = "C001"   # in production: resolved from auth / session


async def main() -> None:
    handler = None

    try:
        await chat()
    finally:
        pass


async def chat() -> None:
    """
    Intake loop. Runs until the customer's complaint is fully resolved.

    IntakeAgent holds a multi-turn conversation, accumulating context across
    turns via its own _history. When it signals ready=True, a CustomerMessage
    is constructed from the collected context and handed to CustomerServiceHandler.
    The resolution cascade fires once on the complete, summarised complaint.
    """
    
    factory = LLMFactory("openrouter")
    model = factory.get_model(model="nvidia/nemotron-3-super-120b-a12b:free")

    intake = IntakeAgent(model)

    print("Agent: Hi, how can I help you today?")
    while True:
        user_input = input("Customer: ").strip()
        if not user_input:
            continue

        intake_result = await intake.collect(user_input, customer_id=CUSTOMER_ID)
        print(f"Agent: {intake_result.reply}")

        if not intake_result.ready:
            continue


        # IntakeAgent has collected order_id and a complete complaint description.
        # Hand off to CustomerServiceHandler for the full resolution cascade.
        # message = CustomerMessage(
        #     message_id=str(uuid.uuid4()),
        #     customer_id=CUSTOMER_ID,
        #     order_id=intake_result.order_id,
        #     message=intake_result.message,
        #     timestamp=datetime.now().isoformat(),
        # )

        # result = await handler.handle(message)
        # print(json.dumps(result, indent=2))
        break


asyncio.run(main())