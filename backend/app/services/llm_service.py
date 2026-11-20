"""Thin wrapper around the Azure OpenAI chat API.

Everything else in the app depends on the small `LLMClient` protocol, so the provider
can be replaced (or faked in tests) without touching the rest of the code.
"""

from functools import lru_cache
from typing import Protocol

import openai
from openai import AzureOpenAI

from app.config import Settings, get_settings

# gpt-5 style models spend part of this budget on hidden reasoning before answering.
DEFAULT_MAX_TOKENS = 6000
REQUEST_TIMEOUT_SECONDS = 90


class LLMError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class LLMClient(Protocol):
    def complete(self, messages: list[dict], *, json_output: bool = False,
                 max_tokens: int = DEFAULT_MAX_TOKENS) -> str: ...


class AzureLLM:
    def __init__(self, settings: Settings):
        self._secret = settings.genai_platform_api_key
        self._deployment = settings.vision_deployment_name
        self._client = AzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=self._secret,
            api_version=settings.azure_openai_api_version,
            timeout=REQUEST_TIMEOUT_SECONDS,
            max_retries=2,
        )

    def complete(self, messages: list[dict], *, json_output: bool = False,
                 max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
        kwargs: dict = {"max_completion_tokens": max_tokens, "reasoning_effort": "low"}
        if json_output:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            response = self._client.chat.completions.create(
                model=self._deployment, messages=messages, **kwargs)
        except openai.AuthenticationError as exc:
            raise LLMError("The LLM rejected the API key.", 502) from exc
        except openai.RateLimitError as exc:
            raise LLMError("The LLM is rate limited. Try again shortly.", 503) from exc
        except openai.APITimeoutError as exc:
            raise LLMError("The LLM request timed out.", 504) from exc
        except openai.APIConnectionError as exc:
            raise LLMError("Could not reach the LLM service.", 502) from exc
        except openai.APIStatusError as exc:
            raise LLMError(self._clean(f"The LLM returned an error ({exc.status_code}): {exc.message}")) from exc

        choice = response.choices[0]
        content = (choice.message.content or "").strip()
        if not content:
            reason = "was cut off" if choice.finish_reason == "length" else "was empty"
            raise LLMError(f"The LLM response {reason}.")
        return content

    def _clean(self, text: str) -> str:
        return text.replace(self._secret, "***")[:300] if self._secret else text[:300]


@lru_cache
def get_llm() -> LLMClient:
    settings = get_settings()
    if not (settings.genai_platform_api_key and settings.azure_openai_endpoint
            and settings.vision_deployment_name):
        raise LLMError(
            "The LLM is not configured. Set GENAI_PLATFORM_API_KEY, AZURE_OPENAI_ENDPOINT and "
            "VISION_DEPLOYMENT_NAME in backend/.env.", 503)
    return AzureLLM(settings)
