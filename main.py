import asyncio
import json
import uuid
from datetime import datetime

from core.llm_factory import LLMFactory
from services.customer_service import CustomerServiceHandler
from schemas.messages import ServiceRequestMessage

from agents.intake_agent import IntakeAgent


CUSTOMER_ID = "C001"   # in production: resolved from auth / session

async def main() -> None:
    handler = CustomerServiceHandler()

    # -- 1.
    factory = LLMFactory("openrouter")
    model = factory.get_model("nvidia/nemotron-3-super-120b-a12b:free")

    intakeAgent = IntakeAgent(model)

    # -- 2.
    # print("Agent: Hi, how can I help you today?")
    # while True:
    #     user_input = input("Customer: ").strip()
    #     if not user_input:
    #         continue

    #     intake_result = await intakeAgent.collect(user_input, customer_id=CUSTOMER_ID)
    #     print(f"Agent: {intake_result.reply}")

    #     if not intake_result.ready:
    #         continue
    #     else:
    #         break

    # -- 3.
    # IntakeAgent has collected order_id and a complete complaint description.
    # Hand off to CustomerServiceHandler for the full resolution cascade.

    message = ServiceRequestMessage(
        message_id=str(uuid.uuid4()),
        customer_id=CUSTOMER_ID,
        order_id="ORD-1001",
        message="Customers package came partially opened and all products inside are WET. customer is requesting a refund",
        triggered_by="intake_agent",
        timestamp=datetime.now().isoformat(),
    )

    print(message.model_dump())

    result = await handler.handle(message)
    print(json.dumps(result, indent=2))


asyncio.run(main())