"""
Optional LLM observability integrations — both are opt-in via environment variables.

WHY TRACING?
------------
When an LLM pipeline breaks, raw print() or log.info() isn't enough to understand
what happened. Tracing gives you a time-sequenced view of every LLM call in a run:
  - What prompt was sent?
  - What did the model return?
  - How many tokens were used?
  - Which call was slow or failed?
  - How do runs compare to each other?

This file wires up two complementary tools:

Langfuse (production tracing)
------------------------------
Set LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY (+ optionally LANGFUSE_HOST).
litellm has built-in Langfuse support: setting litellm.success_callback = ["langfuse"]
is all you need. From that point on, every litellm.acompletion call is automatically
logged to Langfuse as a "generation" (one LLM call).

Pass run_metadata={"trace_id": run_id, "trace_name": "..."} to LLMClient
to group all calls from one pipeline run under a single Langfuse trace.
This makes it easy to compare "did this version of the prompt do better than last night's?"

Phoenix (local debugging / evaluation)
---------------------------------------
Set PHOENIX_TRACING=true.
Phoenix is Arize's open-source LLM observability tool. It starts a local server
and captures traces via OpenTelemetry (the industry-standard tracing protocol).
Open http://localhost:6006 in a browser while the pipeline runs to see a live
flame-graph of every LLM call.
Requires: pip install arize-phoenix openinference-instrumentation-litellm

FAIL-SAFE DESIGN
-----------------
Neither backend raises on misconfiguration — setup failures are logged as
warnings and the pipeline continues without tracing. This is intentional:
tracing is observability infrastructure, not business logic. It should never
be the reason a report fails to run.
"""

import logging
import os
import uuid

import litellm
import structlog

log = structlog.get_logger(__name__)


def generate_run_id() -> str:
    """
    Return a UUID string to identify one pipeline run across all its LLM calls.

    uuid.uuid4() generates a random 128-bit identifier. The str() converts it
    to the standard "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" string form.

    This is passed to LLMClient(run_metadata={"trace_id": run_id, ...}) so
    that every LLM call in the run carries the same trace_id. Langfuse uses
    this to group calls into a single trace.
    """
    return str(uuid.uuid4())


def setup_langfuse() -> bool:
    """
    Enable litellm's Langfuse callback if credentials are present.

    HOW LITELLM CALLBACKS WORK
    ---------------------------
    litellm maintains lists of callback names as module-level attributes:
      litellm.success_callback = ["langfuse"]   — called after each successful completion
      litellm.failure_callback = ["langfuse"]   — called after each failed completion

    When "langfuse" is in either list, litellm imports its Langfuse integration
    and sends the call metadata to the Langfuse API after every LLM call.
    This is instrumentation at the library level — no changes to LLMClient needed.

    We read the existing callback lists with getattr(..., []) to avoid overwriting
    other callbacks that might already be registered (e.g. if another tool also
    set a callback). We only append "langfuse" if it's not already present.

    Returns True if Langfuse tracing is active, False otherwise.
    """
    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        log.debug("langfuse_skipped", reason="LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set")
        return False

    try:
        # Read existing callbacks so we don't overwrite them.
        # getattr with [] default handles the case where litellm initializes
        # these attributes as None rather than [].
        existing_success = list(getattr(litellm, "success_callback", []) or [])
        existing_failure = list(getattr(litellm, "failure_callback", []) or [])

        # Only append if not already registered — idempotent.
        if "langfuse" not in existing_success:
            litellm.success_callback = existing_success + ["langfuse"]
        if "langfuse" not in existing_failure:
            litellm.failure_callback = existing_failure + ["langfuse"]

        host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")
        log.info("langfuse_enabled", host=host)
        return True

    except Exception as exc:
        log.warning("langfuse_setup_failed", error=str(exc))
        return False


def setup_phoenix() -> bool:
    """
    Start a local Phoenix server and auto-instrument litellm via OpenTelemetry.

    HOW OPENTELEMETRY AUTO-INSTRUMENTATION WORKS
    ----------------------------------------------
    OpenTelemetry (OTEL) is the industry-standard observability framework.
    It defines Spans (single operations with start/end timestamps) and Traces
    (trees of Spans representing a complete request/operation).

    LiteLLMInstrumentor().instrument() monkey-patches the litellm library so
    that every litellm call automatically:
      1. Creates an OTEL Span with the call's attributes (model, prompt, tokens)
      2. Sends it to the configured exporter (Phoenix's OTLP endpoint)

    This gives you the same zero-code-change benefit as Langfuse's callback
    mechanism, but using the open standard OTEL protocol instead.

    WHY SEPARATE ImportError HANDLING
    -----------------------------------
    Phoenix and its dependencies are optional — not in requirements.txt.
    If the user hasn't run "pip install arize-phoenix ...", the import will fail.
    We catch ImportError separately from general Exception so we can give a
    clear actionable error message ("pip install X") rather than a cryptic
    AttributeError or similar.

    Returns True if Phoenix tracing is active, False otherwise.
    """
    if not os.environ.get("PHOENIX_TRACING"):
        log.debug("phoenix_skipped", reason="PHOENIX_TRACING not set")
        return False

    try:
        # These imports are inside the function body, not at the top of the file,
        # because they're optional. If they were at the top, the whole module
        # would fail to import for any user who hasn't installed the packages.
        import phoenix as px
        from openinference.instrumentation.litellm import LiteLLMInstrumentor
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        # px.launch_app() starts the local Phoenix web server and returns
        # a session object with the server's URL.
        session = px.launch_app()
        # The OTLP trace endpoint is at /v1/traces under the Phoenix base URL.
        endpoint = session.url.rstrip("/") + "/v1/traces"

        # Wire up the OTEL pipeline:
        #   TracerProvider    — the factory that creates Tracer objects
        #   SimpleSpanProcessor — sends each Span to the exporter immediately
        #   OTLPSpanExporter  — sends Spans to the Phoenix server over HTTP
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        # Register this provider as the global OTEL tracer so all instrumented
        # libraries (like litellm after .instrument()) use it.
        trace.set_tracer_provider(provider)

        # Patch litellm to create OTEL spans for every call.
        LiteLLMInstrumentor().instrument()

        log.info("phoenix_enabled", ui=session.url)
        return True

    except ImportError:
        # ImportError means the packages aren't installed — give a clear fix.
        log.warning(
            "phoenix_setup_failed",
            reason="Required packages not installed",
            hint="pip install arize-phoenix openinference-instrumentation-litellm",
        )
        return False
    except Exception as exc:
        # Any other error (server port conflict, etc.) — log and continue.
        log.warning("phoenix_setup_failed", error=str(exc))
        return False


def setup_tracing() -> dict[str, bool]:
    """
    Configure all available tracing backends.

    Returns a dict showing which backends are active, e.g.:
        {"langfuse": True, "phoenix": False}

    The CLI uses this to print which backends are active at startup,
    so the user knows where to look for traces.
    """
    return {
        "langfuse": setup_langfuse(),
        "phoenix": setup_phoenix(),
    }
