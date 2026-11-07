"""Bounded Prometheus metrics with normalized route labels."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


class ApiMetrics:
    """Own one isolated registry so app factories never collide."""

    def __init__(self) -> None:
        self._registry = CollectorRegistry(auto_describe=True)
        self._requests = Counter(
            "hmt_crypto_http_requests_total",
            "Completed control API requests.",
            ("method", "route", "status"),
            registry=self._registry,
        )
        self._latency = Histogram(
            "hmt_crypto_http_request_duration_seconds",
            "Control API request latency.",
            ("method", "route"),
            buckets=_LATENCY_BUCKETS,
            registry=self._registry,
        )

    def observe(self, *, method: str, route: str, status: int, elapsed: float) -> None:
        """Record one request using an already-normalized route template."""
        if (
            type(method) is not str
            or method not in {"GET", "POST", "DELETE"}
            or type(route) is not str
            or not route.startswith("/")
            or len(route) > 128
            or type(status) is not int
            or not 100 <= status <= 599
            or type(elapsed) is not float
            or elapsed < 0
        ):
            return
        self._requests.labels(method=method, route=route, status=str(status)).inc()
        self._latency.labels(method=method, route=route).observe(elapsed)

    def render(self) -> bytes:
        """Render only this process-owned registry in Prometheus text format."""
        return generate_latest(self._registry)


__all__ = ["ApiMetrics"]
