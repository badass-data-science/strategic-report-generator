import logging
from typing import TypeVar, Type

import instructor
import litellm
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log
from pydantic import BaseModel

from .models import TokenUsage

log = structlog.get_logger(__name__)

litellm.suppress_debug_info = True

T = TypeVar("T", bound=BaseModel)

_TENACITY_LOGGER = logging.getLogger(__name__)


class LLMClient:
    """
    Async, provider-agnostic LLM client built on litellm + instructor.

    Pass any litellm-compatible model string:
      - Ollama:     "ollama_chat/llama3.1:70b"
      - Claude:     "anthropic/claude-sonnet-4-6"
      - OpenAI:     "gpt-4o"

    Both methods are async so they can be awaited directly inside an
    asyncio pipeline without blocking the event loop.
    """

    def __init__(
        self,
        model: str,
        temperature: float = 0.1,
        run_metadata: dict | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self._run_metadata = run_metadata or {}
        self._instructor = instructor.from_litellm(litellm.acompletion)
        self._total_usage = TokenUsage()

    @property
    def total_usage(self) -> TokenUsage:
        return self._total_usage

    def _parse_usage(self, completion) -> TokenUsage:
        try:
            u = completion.usage
            return TokenUsage(
                prompt_tokens=u.prompt_tokens,
                completion_tokens=u.completion_tokens,
                total_tokens=u.total_tokens,
            )
        except Exception:
            return TokenUsage()

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        before_sleep=before_sleep_log(_TENACITY_LOGGER, logging.WARNING),
        reraise=True,
    )
    async def complete_structured(
        self,
        prompt: str,
        response_model: Type[T],
        system: str | None = None,
    ) -> tuple[T, TokenUsage]:
        """Call the LLM and parse the response into a Pydantic model via instructor."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        log.debug("llm_call_start", model=self.model, schema=response_model.__name__)

        result, completion = await self._instructor.chat.completions.create_with_completion(
            model=self.model,
            response_model=response_model,
            messages=messages,
            temperature=self.temperature,
            **({"metadata": self._run_metadata} if self._run_metadata else {}),
        )

        usage = self._parse_usage(completion)
        self._total_usage = self._total_usage + usage

        log.debug(
            "llm_call_complete",
            model=self.model,
            schema=response_model.__name__,
            total_tokens=usage.total_tokens,
        )

        return result, usage

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        before_sleep=before_sleep_log(_TENACITY_LOGGER, logging.WARNING),
        reraise=True,
    )
    async def complete_text(
        self,
        prompt: str,
        system: str | None = None,
    ) -> tuple[str, TokenUsage]:
        """Call the LLM and return the raw text response."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        log.debug("llm_call_start", model=self.model, schema="text")

        completion = await litellm.acompletion(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            **({"metadata": self._run_metadata} if self._run_metadata else {}),
        )

        text = completion.choices[0].message.content
        usage = self._parse_usage(completion)
        self._total_usage = self._total_usage + usage

        log.debug("llm_call_complete", model=self.model, total_tokens=usage.total_tokens)

        return text, usage
