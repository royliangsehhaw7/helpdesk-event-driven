
from agents.base_agent import BaseAgent
from core import Deps, MessageHub, Blackboard

from schemas.contracts.customer_message import CustomerMessageContract
from schemas.contracts.profile_result import CustomerProfileContract


class SentimentAgent(BaseAgent):

    def subscribe(self, hub: MessageHub, deps: Deps) -> None:
        self._pending_score = None
        self._pending_label = None

        async def on_message(event):
            await self.handle(event, deps)

        async def on_profile(event):
            # if sentiment finished before profile was posted, apply now
            if self._pending_score is not None:
                event.sentiment_score = self._pending_score
                event.sentiment_label = self._pending_label
                deps.board.profile = event

        hub.subscribe(CustomerMessageContract, on_message)
        hub.subscribe(CustomerProfileContract, on_profile)

    def get_instruction(self) -> str:
        return """
            You are the SentimentAgent.
            Score the emotional tone of the customer message — tone only, not the complaint.

            sentiment_score from -1.0 to 1.0:
              < -0.5          very angry
              -0.5 to -0.1   frustrated
              -0.1 to  0.1   neutral
              > 0.1           satisfied

            sentiment_label:
              "angry"       score < -0.5
              "frustrated"  -0.5 <= score < -0.1
              "neutral"     -0.1 <= score <= 0.1
              "satisfied"   score > 0.1

            Call log_decision once.
            Return {"sentiment_score": float, "sentiment_label": str}.
        """

    async def handle(self, event: CustomerMessageContract, deps: Deps) -> None:
        result = await self._agent.run(
            f"Score the sentiment of this message: {event.message}",
            deps=deps,
            instructions=self.get_instruction(),
        )
        score = result.output
        if deps.board.profile is not None:
            deps.board.profile.sentiment_score = score["sentiment_score"]
            deps.board.profile.sentiment_label = score["sentiment_label"]
        else:
            # profile not yet posted — hold until on_profile fires
            self._pending_score = score["sentiment_score"]
            self._pending_label = score["sentiment_label"]