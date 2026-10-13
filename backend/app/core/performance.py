from __future__ import annotations

import asyncio
from time import perf_counter

from fastapi import Request
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from app.core.config import Settings, get_settings
from app.services.performance import PerformanceRegistry, get_performance_registry


class PerformanceMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        settings: Settings | None = None,
        registry: PerformanceRegistry | None = None,
    ) -> None:
        super().__init__(app)
        self.settings = settings or get_settings()
        self.registry = registry or get_performance_registry()
        self._semaphore = asyncio.Semaphore(self.settings.max_concurrent_requests)

    async def dispatch(self, request: Request, call_next) -> Response:
        excluded = request.url.path in {"/health", "/api/v1/health"}
        rate_remaining: int | None = None
        if self.settings.rate_limit_enabled and not excluded:
            identity = request.client.host if request.client else "unknown"
            allowed, rate_remaining, retry_after = await run_in_threadpool(
                self.registry.check_rate_limit, identity
            )
            if not allowed:
                await run_in_threadpool(self.registry.record_guard_rejection, "rate_limit")
                return JSONResponse(
                    {"detail": "请求过于频繁，请稍后重试"},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )

        try:
            await asyncio.wait_for(
                self._semaphore.acquire(),
                timeout=self.settings.request_queue_timeout_seconds,
            )
        except TimeoutError:
            await run_in_threadpool(self.registry.record_guard_rejection, "concurrency")
            return JSONResponse(
                {"detail": "服务繁忙，请稍后重试"},
                status_code=503,
                headers={"Retry-After": "1"},
            )

        started = perf_counter()
        try:
            response = await call_next(request)
        finally:
            self._semaphore.release()
        duration_ms = (perf_counter() - started) * 1000
        route = getattr(request.scope.get("route"), "path", request.url.path)
        if not excluded:
            await run_in_threadpool(
                self.registry.record_http,
                route,
                request.method,
                response.status_code,
                duration_ms,
            )
        response.headers["X-Response-Time-Ms"] = f"{duration_ms:.3f}"
        response.headers["X-Concurrency-Limit"] = str(self.settings.max_concurrent_requests)
        if rate_remaining is not None:
            response.headers["X-RateLimit-Limit"] = str(
                self.settings.rate_limit_requests_per_minute
            )
            response.headers["X-RateLimit-Remaining"] = str(rate_remaining)
        return response
