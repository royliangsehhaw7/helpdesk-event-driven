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

    try:
        # -- 1.
        factory = LLMFactory("openrouter")
        model = factory.get_model("nvidia/nemotron-3-super-120b-a12b:free")

        intakeAgent = IntakeAgent(model)
        intake_result = await intakeAgent.collect()

        # -- 2.
        service_request = ServiceRequestMessage(
            message_id=str(uuid.uuid4()),
            customer_id=CUSTOMER_ID,
            order_id=intake_result.order_id,
            message=intake_result.message,
            timestamp=datetime.now().isoformat()
        )

        # == 3.
        result = await handler.handle(service_request)

        # -- 4.
        print(json.dumps(result, indent=2))
    finally:
        pass


asyncio.run(main())