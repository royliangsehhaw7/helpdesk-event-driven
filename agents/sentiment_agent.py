
from agents.base_agent import BaseAgent
from core.message_hub import MessageHub
from core.deps import Deps

from schemas.messages import ServiceRequestMessage, ProfileResultMessage

class SentimentAgent(BaseAgent):

    def subscribe(self, hub: MessageHub, deps: Deps) -> None:
        self._pending_score = None
        self._pending_label = None

        async def on_message(message):
            await self.handle(message, deps)

        async def on_profile(message):
            # if sentiment finished before profile was posted, apply now
            if self._pending_score is not None:
                deps.board.profile.sentiment_score = self._pending_score
                deps.board.profile.sentiment_label = self._pending_label

        hub.subscribe(ServiceRequestMessage, on_message)
        hub.subscribe(ProfileResultMessage, on_profile)

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

    async def handle(self, message: ServiceRequestMessage, deps: Deps) -> None:
        result = await self._agent.run(
            f"Score the sentiment of this message: {message.message}",
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