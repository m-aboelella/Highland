from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .constants import DEFAULT_PORTS, SERVICE_PRODUCTS
from .store import JsonStore
from .systems import SYSTEMS


def create_app(service: str, store: JsonStore | None = None) -> FastAPI:
    if service != "catalog" and service not in SYSTEMS:
        raise ValueError(f"Unknown service: {service}")

    product = "Highland mock service catalog" if service == "catalog" else SERVICE_PRODUCTS[service]
    app = FastAPI(
        title=product,
        version="0.1.0",
        description=f"Synthetic {service} system for the Highland educational project.",
    )
    if store is not None:
        app.state.store = store
    elif service != "catalog":
        app.state.store = JsonStore(service)

    @app.middleware("http")
    async def failure_injection(request: Request, call_next: Any) -> Any:
        failure = request.headers.get("x-mock-failure")
        if failure:
            try:
                response_status = int(failure)
            except ValueError:
                response_status = 500
            response_status = min(max(response_status, 400), 599)
            return JSONResponse(
                status_code=response_status,
                content={
                    "error": "injected_failure",
                    "message": (
                        f"Synthetic HTTP {response_status} response requested by the caller."
                    ),
                },
            )
        latency = request.headers.get("x-mock-latency-ms")
        if latency:
            try:
                milliseconds = min(max(int(latency), 0), 5000)
            except ValueError:
                milliseconds = 0
            await asyncio.sleep(milliseconds / 1000)
        response = await call_next(request)
        response.headers["X-Mock-System"] = service
        return response

    @app.get("/health")
    def health() -> dict[str, Any]:
        result: dict[str, Any] = {"status": "ok", "service": service, "product": product}
        if service != "catalog":
            result["dataset"] = app.state.store.read()["meta"]
        return result

    if service == "catalog":
        _add_catalog_route(app)
    else:
        SYSTEMS[service].register_routes(app)
    return app


def _add_catalog_route(app: FastAPI) -> None:
    @app.get("/")
    def catalog() -> dict[str, Any]:
        return {
            "name": "Highland mock enterprise",
            "company": "Summit Software",
            "as_of": "2026-07-29T12:00:00Z",
            "services": [
                {
                    "id": service,
                    "product": SERVICE_PRODUCTS[service],
                    "base_url": f"http://localhost:{DEFAULT_PORTS[service]}",
                }
                for service in SYSTEMS
            ],
        }
