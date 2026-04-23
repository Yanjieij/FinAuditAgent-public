"""OpenTelemetry 接入骨架。

**为什么再加 OTel**（Langfuse 已经记了 trace）：
    - Langfuse 擅长**LLM 视角**的 trace（prompt、token、成本）
    - OTel 擅长**系统视角**的 trace（HTTP / DB / 自定义 span），和 Jaeger / Tempo / Grafana
      的生态能打通
    - 金融场景通常公司已有 OTel 基建，LLM trace 归 LLM 观测平台，系统 trace 归 APM，两边互补

本文件给最小可运行骨架：拉 tracer → 装 FastAPI → 给节点封装一个 with_span helper。
"""

from __future__ import annotations

import os
from contextlib import contextmanager


def init_otel(service_name: str = "fin-audit-agent") -> None:
    """幂等初始化 OTel Tracer。

    不设 OTEL_EXPORTER_OTLP_ENDPOINT 时用 console exporter（开发）。
    """
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
        )
    except ImportError:
        return  # 未装 OTel 就 no-op

    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name})
    )
    exporter: "object"
    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        # 生产：OTLP
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore
            OTLPSpanExporter,
        )
        exporter = OTLPSpanExporter()
    else:
        exporter = ConsoleSpanExporter()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)


@contextmanager
def with_span(name: str, **attributes):
    """context manager 包一段代码为一个 span。"""
    try:
        from opentelemetry import trace
    except ImportError:
        yield
        return

    tracer = trace.get_tracer("fin-audit-agent")
    with tracer.start_as_current_span(name) as span:
        for k, v in attributes.items():
            span.set_attribute(k, v)
        yield span
