import os
from dotenv import load_dotenv

from pydantic_ai.models.google import GoogleModel, GoogleModelSettings
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider


load_dotenv()

class LLMFactory():
    def __init__(self, organization: str ):
        self._organization = organization

    def get_model(self, model: str):
        if self._organization == "gemini":
            return self._get_google_model(model)
        else:
            return self._get_openrouter_model(model)


    # -- private methods
    def _get_google_model(name):
        model = GoogleModel(
            model_name = name,
            provider = GoogleProvider(
                api_key=os.getenv("GEMINI_API_KEY")
            ),
            settings = GoogleModelSettings(
                temperature=0.15
            )
        )
        return model

    def _get_openrouter_model(self, name):
        model = OpenRouterModel(
            model_name=name,
            provider=OpenRouterProvider(
                api_key=os.getenv('OPENROUTER_API_KEY'),
                app_url="https://openrouter.ai/api/v1"
            ),
            settings=OpenRouterModelSettings(
                tool_choice='auto',
                temperature=0.15
            )
        )
        return model
