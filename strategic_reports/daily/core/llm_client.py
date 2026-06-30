"""
Async, provider-agnostic LLM client.

Key libraries:
  litellm    — translates a single API surface into calls to any LLM provider.
               One model string ("anthropic/claude-sonnet-4-6", "gpt-4o",
               "ollama_chat/llama3.1:70b") is all you change to switch backends.
  instructor — patches the litellm client to parse LLM responses directly into
               Pydantic models. Handles retries with validation errors as
               feedback, JSON extraction from markdown fences, etc.
  tenacity   — retry library with composable stop/wait/before_sleep strategies.
               Much cleaner than the original "while not success / assert count < 5"
               pattern.
  structlog  — structured logging: key=value pairs instead of f-strings.
               Grep-able, machine-parseable, and works with log aggregation tools.

Why async?
  LLM API calls are I/O-bound — the process spends most of its time waiting
  for the network, not doing CPU work. Async lets the event loop switch to
  other coroutines (other topic pipelines) while waiting, without threads.
  litellm.acompletion is the async variant of litellm.completion.
"""

import logging
from typing import TypeVar, Type

import instructor
import litellm
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log
from pydantic import BaseModel

from .models import TokenUsage

# structlog logger — use log.info(), log.debug(), log.warning(), etc.
# __name__ scopes the logger to this module ("core.llm_client") so log output
# shows where it came from.
log = structlog.get_logger(__name__)

# litellm prints a lot of internal debug output by default; suppress it so our
# structlog output stays readable.
litellm.suppress_debug_info = True

# TypeVar constrains T to be some subclass of BaseModel.
# This lets complete_structured be generic: it returns whatever Pydantic type
# you pass in as response_model, not just "BaseModel".
# Usage:  result, usage = await client.complete_structured(prompt, ArticleSummaryBatch, ...)
#         result  ← is typed as ArticleSummaryBatch, not just BaseModel
T = TypeVar("T", bound=BaseModel)

# tenacity's before_sleep_log requires a stdlib logger, not a structlog logger.
# We create one here at module level so tenacity can log retry attempts.
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

    instructor_mode controls how structured output is requested:
      - instructor.Mode.TOOLS    (default) — uses function/tool calling API.
                                             Requires model support. Best for
                                             OpenAI, Anthropic, and Ollama models
                                             that advertise tool use.
      - instructor.Mode.JSON     — injects schema into system prompt; expects
                                   raw JSON back. Works with most Ollama models
                                   that don't support tool calling.
      - instructor.Mode.MD_JSON  — like JSON but extracts from ```json``` fences.

    api_base and api_key are forwarded directly to litellm on every call:
      - api_base overrides the endpoint URL (e.g. "http://my-server:11434").
        litellm also reads OLLAMA_API_BASE from the environment automatically,
        but passing it explicitly here ensures it applies to every call even if
        the env var isn't set in the worker process.
      - api_key sets the Authorization header. Standard Ollama doesn't require
        one, but hosted or proxied instances (e.g. behind nginx with basic auth,
        or a gateway like LiteLLM proxy) typically do.
    """

    def __init__(
        self,
        model: str,
        temperature: float = 0.1,
        run_metadata: dict | None = None,
        instructor_mode: instructor.Mode = instructor.Mode.TOOLS,
        api_base: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature

        # run_metadata is forwarded to every LLM call as litellm "metadata".
        # Langfuse reads it to group calls from one pipeline run under a
        # single trace. Keys "trace_id" and "trace_name" are Langfuse-specific.
        self._run_metadata = run_metadata or {}

        # Only store non-None values; we spread these into every litellm call
        # below using conditional ** unpacking so we never send api_base=None
        # to a provider that doesn't expect it.
        self._api_base = api_base
        self._api_key = api_key

        # instructor.from_litellm patches litellm.acompletion so that the
        # normal chat.completions.create() call also accepts a response_model
        # argument and returns a parsed Pydantic instance.
        self._instructor = instructor.from_litellm(litellm.acompletion, mode=instructor_mode)

        # Accumulates token usage across all calls on this client instance.
        self._total_usage = TokenUsage()

    @property
    def total_usage(self) -> TokenUsage:
        """Read-only view of cumulative token usage across all calls."""
        return self._total_usage

    def _parse_usage(self, completion) -> TokenUsage:
        """
        Extract token counts from a litellm completion response.

        The bare try/except is intentional here: token usage fields are not
        guaranteed by all providers or in all error states, so we return a
        zeroed TokenUsage rather than crashing the whole pipeline over missing
        accounting data.
        """
        try:
            u = completion.usage
            return TokenUsage(
                prompt_tokens=u.prompt_tokens,
                completion_tokens=u.completion_tokens,
                total_tokens=u.total_tokens,
            )
        except Exception:
            return TokenUsage()

    # @retry from tenacity decorates the method below.
    # stop_after_attempt(5)         — give up after 5 total tries
    # wait_exponential(min=2, max=30) — wait 2s, 4s, 8s, 16s, 30s between tries
    # before_sleep_log(...)         — log a WARNING before each sleep so we
    #                                 know a retry is happening
    # reraise=True                  — after all retries exhausted, re-raise the
    #                                 last exception (don't swallow it)
    #
    # tenacity detects that this is an async function and uses AsyncRetrying
    # internally, so await works correctly inside the decorated method.
    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        before_sleep=before_sleep_log(_TENACITY_LOGGER, logging.WARNING),
        reraise=True,
    )
    async def complete_structured(
        self,
        prompt: str,
        response_model: Type[T],    # the Pydantic class to parse into
        system: str | None = None,
    ) -> tuple[T, TokenUsage]:
        """
        Call the LLM and parse the response into a Pydantic model via instructor.

        instructor handles:
          - Injecting the JSON schema into the request
          - Extracting JSON from markdown code fences if needed
          - Retrying with validation errors as feedback if Pydantic rejects the output

        Returns a tuple of (parsed_model_instance, token_usage_for_this_call).
        The caller accumulates token_usage across calls if needed.
        """
        # Build the messages list in the OpenAI chat format:
        # system turn (optional) → sets the LLM's persona/role
        # user turn              → the actual request
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        log.debug("llm_call_start", model=self.model, schema=response_model.__name__)

        # create_with_completion returns BOTH the parsed Pydantic model AND
        # the raw litellm completion object. We need the raw object to extract
        # token usage counts.
        result, completion = await self._instructor.chat.completions.create_with_completion(
            model=self.model,
            response_model=response_model,
            messages=messages,
            temperature=self.temperature,
            # Spread optional kwargs only when set — avoids sending api_base=None
            # or api_key=None to providers that don't expect them.
            **({"api_base": self._api_base} if self._api_base else {}),
            **({"api_key": self._api_key} if self._api_key else {}),
            **({"metadata": self._run_metadata} if self._run_metadata else {}),
        )

        usage = self._parse_usage(completion)
        # Immutable addition: creates a new TokenUsage rather than mutating.
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
        """
        Call the LLM and return the raw text response (no Pydantic parsing).

        Use this for cases where the output is genuinely free-form and can't
        be usefully constrained by a schema — e.g., long-form narrative text.
        For anything with predictable structure, prefer complete_structured.
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        log.debug("llm_call_start", model=self.model, schema="text")

        # Call litellm directly (without instructor) since we want raw text.
        completion = await litellm.acompletion(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            **({"api_base": self._api_base} if self._api_base else {}),
            **({"api_key": self._api_key} if self._api_key else {}),
            **({"metadata": self._run_metadata} if self._run_metadata else {}),
        )

        # completion.choices[0].message.content is the standard OpenAI-style
        # response field; litellm normalizes all providers to this format.
        text = completion.choices[0].message.content
        usage = self._parse_usage(completion)
        self._total_usage = self._total_usage + usage

        log.debug("llm_call_complete", model=self.model, total_tokens=usage.total_tokens)

        return text, usage
