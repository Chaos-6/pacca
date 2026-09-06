"""
OpenTelemetry tracer setup and span utilities for PACCA.

This module is the single place that owns OTel configuration. Agents and
routes import `get_tracer()` and `record_span_error()` from here —
they never interact with the OTel SDK directly. This isolation means:
  - Swapping the OTel exporter (Langfuse → Jaeger → Tempo) is a change
    in this one file
  - Disabling tracing entirely (e.g. in unit tests) is a single flag
  - The rest of the codebase never has to know whether OTel is active

Teaching note — what is a tracer and what is a span?

  A TRACER is a factory that creates spans. You get one per module:
    tracer = get_tracer("pacca.agents.decision")

  A SPAN is a named unit of work with a start time, end time, and
  key-value attributes. You open one when work begins, close it when
  work ends. The OTel SDK records the timing automatically.

    with tracer.start_as_current_span("llm_call") as span:
        span.set_attribute("agent.name", "DecisionAgent")
        result = await call_llm(...)
        span.set_attribute("confidence_score", result.confidence)

  Spans automatically nest. If a parent span is active when you open
  a child span, the SDK links them — building the full trace tree:

    http_request
      └── authorization_submit
            ├── rag_query [45ms]
            └── agent_decision [320ms]
                  └── anthropic_api_call [315ms]

  This tree is what you see in a trace viewer like Langfuse or Jaeger.

Teaching note — why a no-op tracer fallback?

  In unit tests and environments without OTel configured, we return a
  no-op tracer that accepts all the same method calls but does nothing.
  This means agent code doesn't need any `if tracing_enabled:` guards —
  it always calls `tracer.start_as_current_span(...)` and the tracer
  decides whether to record it. Clean separation of concerns.
"""

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.trace import NonRecordingSpan, Span, StatusCode

from .logging import get_logger

logger = get_logger(__name__)

# Module-level flag — set to True once configure_tracing() has run
_tracing_configured = False


def configure_tracing(
    service_name: str = "pacca",
    endpoint: str | None = None,
    enabled: bool = True,
) -> None:
    """
    Configure the global OpenTelemetry TracerProvider.

    This should be called once at application startup (in FastAPI's lifespan
    or in the app factory). Subsequent calls are no-ops.

    Args:
        service_name: The service name reported in traces. Shows up in
                      Langfuse/Jaeger as the source of the trace.
        endpoint:     OTLP HTTP endpoint for a remote collector, e.g.
                      "http://localhost:4318". If None, traces are printed
                      to the console (useful for development).
        enabled:      If False, installs a no-op provider. All tracing calls
                      become no-ops. Useful for unit tests.
    """
    global _tracing_configured
    if _tracing_configured:
        return
    _tracing_configured = True

    if not enabled:
        # Install a no-op provider — all tracing calls silently do nothing.
        # This is the correct pattern for test environments.
        trace.set_tracer_provider(trace.NoOpTracerProvider())
        logger.debug("otel_tracing_disabled")
        return

    # Resource identifies this service in the trace backend
    resource = Resource(attributes={SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)

    if endpoint:
        # Production: export spans to a remote collector via OTLP/HTTP.
        # Compatible with Langfuse, Jaeger, Grafana Tempo, AWS X-Ray (via
        # the ADOT collector), and any other OTel-compatible backend.
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
            # BatchSpanProcessor buffers spans and exports them in batches —
            # much more efficient than exporting each span individually.
            provider.add_span_processor(BatchSpanProcessor(exporter))
            logger.info("otel_exporter_configured", endpoint=endpoint)
        except ImportError:
            logger.warning(
                "otel_otlp_exporter_unavailable",
                detail="opentelemetry-exporter-otlp-proto-http not installed; "
                "falling back to console exporter",
            )
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    else:
        # Development: print spans to console.
        # SimpleSpanProcessor exports each span immediately as it completes.
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        logger.debug("otel_console_exporter_configured")

    trace.set_tracer_provider(provider)
    logger.info("otel_tracing_configured", service_name=service_name)


def get_tracer(name: str) -> trace.Tracer:
    """
    Get a tracer for the given module/component name.

    Usage:
        tracer = get_tracer(__name__)

    The name typically matches the module: "pacca.agents.decision_agent".
    This appears in the trace backend alongside the span name, helping
    you identify which component produced each span.

    Args:
        name: Component name, typically __name__

    Returns:
        An OTel Tracer instance (or a no-op tracer if tracing is disabled)
    """
    return trace.get_tracer(name)


def record_span_error(span: Span, error: Exception) -> None:
    """
    Record an exception on an active span and mark it as failed.

    This is a convenience wrapper around OTel's exception recording API.
    Calling this ensures the span shows as an error in the trace backend,
    identified by exception type only -- see the PHI note below.

    Usage:
        with tracer.start_as_current_span("llm_call") as span:
            try:
                result = await call_llm(...)
            except Exception as e:
                record_span_error(span, e)
                raise

    THREAT-03 (PHI containment): only the exception *type* is recorded. Spans are
    exported to a third-party trace backend outside the audit store, and an exception
    raised while handling a clinical case can carry PHI in its message and stack
    trace. ``record_exception`` attaches both, so it is deliberately not called here.
    The full error text is retained in the audit record, which never leaves the
    HIPAA-scoped store.

    Args:
        span:  The active span to record the error on
        error: The exception that occurred
    """
    error_type = type(error).__name__
    span.add_event("exception", {"exception.type": error_type})
    span.set_status(StatusCode.ERROR, error_type)


def get_current_trace_id() -> str | None:
    """
    Get the trace ID of the currently active span, as a hex string.

    This is useful for including the trace ID in audit log records,
    so you can correlate a compliance audit record with a distributed trace.

    Returns:
        Hex trace ID string (e.g. "1a2b3c4d...") or None if no active span
    """
    span = trace.get_current_span()
    if isinstance(span, NonRecordingSpan):
        return None
    ctx = span.get_span_context()
    if ctx and ctx.trace_id:
        return format(ctx.trace_id, "032x")
    return None
