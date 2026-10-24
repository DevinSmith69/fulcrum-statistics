"""OpenTelemetry → Application Insights for the services (AI_PROMPT_DESIGN §2 "tracing").

`configure_telemetry()` is called once at service start-up. With `APPLICATIONINSIGHTS_CONNECTION_STRING`
set (Bicep sets it on every Container App) it installs the Azure Monitor exporter and turns on Agent
Framework instrumentation, so every workflow run, executor and agent (LLM) call becomes a span. Without it,
it does nothing: tests and offline runs never touch the network. `FULCRUM_OTEL_CONSOLE=1` prints spans to
the console instead (handy to see the agent spans locally).

Prompt and response text are not recorded unless `ENABLE_SENSITIVE_DATA=true`.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("fulcrum.telemetry")

_configured = False


def configure_telemetry(service_name: str) -> bool:
    """Set up tracing for `service_name` (sim | engine | agents | api). Returns True if tracing is on."""
    global _configured
    if _configured:
        return True
    conn = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    console = os.getenv("FULCRUM_OTEL_CONSOLE", "0") == "1"
    if not conn and not console:
        return False
    os.environ.setdefault("OTEL_SERVICE_NAME", f"fulcrum-{service_name}")
    try:
        if conn:
            from azure.monitor.opentelemetry import configure_azure_monitor

            configure_azure_monitor(connection_string=conn)
        else:
            from opentelemetry import trace
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

            provider = TracerProvider(resource=Resource.create({"service.name": f"fulcrum-{service_name}"}))
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
            trace.set_tracer_provider(provider)
        from agent_framework.observability import enable_instrumentation

        enable_instrumentation()
    except ImportError as e:  # the `azure` extra is not installed
        log.warning("tracing requested but unavailable (%s); install with `uv sync --extra azure`", e)
        return False
    _configured = True
    log.info("tracing on for fulcrum-%s (%s)", service_name, "Azure Monitor" if conn else "console")
    return True


def current_trace_id() -> str | None:
    """The active OpenTelemetry trace id as 32 hex chars, or None when nothing is being traced."""
    try:
        from opentelemetry import trace
    except ImportError:
        return None
    ctx = trace.get_current_span().get_span_context()
    return f"{ctx.trace_id:032x}" if ctx.is_valid else None
