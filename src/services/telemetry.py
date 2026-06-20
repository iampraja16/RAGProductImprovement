import logging
from typing import Optional
from src.config import settings

logger = logging.getLogger(__name__)

class DummySpanContext:
    def __enter__(self): return self
    def __exit__(self, *exc): pass
    def set_attribute(self, *args, **kwargs): pass
    def set_status(self, *args, **kwargs): pass
    def record_exception(self, *args, **kwargs): pass

class DummyTracer:
    def start_as_current_span(self, name, *args, **kwargs):
        return DummySpanContext()

_tracer = None

def get_tracer(name: str = __name__):
    global _tracer
    if _tracer is not None:
        return _tracer

    if not settings.otel_enabled:
        logger.info("OpenTelemetry is disabled. Using DummyTracer.")
        _tracer = DummyTracer()
        return _tracer

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create({"service.name": settings.otel_service_name})
        provider = TracerProvider(resource=resource)
        
        if settings.otel_exporter_endpoint:
            exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_endpoint)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer(name)
        logger.info(f"OpenTelemetry enabled. Exporting to {settings.otel_exporter_endpoint}")
        return _tracer
    except ImportError:
        logger.warning("opentelemetry library not installed. Using DummyTracer.")
        _tracer = DummyTracer()
        return _tracer

tracer = get_tracer("emr-agent")
