"""OpenTelemetry setup and Kafka header propagation.

A trace starts at the encounter service's HTTP request, rides the traceparent header through
Kafka (written by the outbox relay), continues here, crosses gRPC into the gateway, and returns
to the encounter service on note.drafted.
"""

import os

from opentelemetry import context, propagate, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

tracer = trace.get_tracer("note_worker")


def setup(service_name: str = "note-worker") -> None:
    if os.environ.get("OTEL_SDK_DISABLED") == "true":
        return
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.grpc import GrpcInstrumentorClient
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    GrpcInstrumentorClient().instrument()
    HTTPXClientInstrumentor().instrument()


def extract(headers: list[tuple[str, bytes]] | None) -> context.Context:
    carrier = {k: v.decode("utf-8", "replace") for k, v in (headers or []) if v is not None}
    return propagate.extract(carrier)


def inject() -> list[tuple[str, bytes]]:
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return [(k, v.encode()) for k, v in carrier.items()]
