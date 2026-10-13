from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from sqlalchemy.orm import Session

from app.api.routes import health as api_health
from app.api.routes import router
from app.api.auth_routes import router as auth_router
from app.core.config import get_settings
from app.core.database import SessionLocal, get_db, init_db
from app.core.observability import configure_tracing, shutdown_tracing
from app.core.performance import PerformanceMiddleware
from app.schemas import HealthResponse
from app.services.seed import normalize_legacy_ticket_statuses, seed_database
from app.services.auth import ensure_bootstrap_accounts


settings = get_settings()
configure_tracing(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    with SessionLocal() as session:
        seed_database(session)
        normalize_legacy_ticket_statuses(session)
        ensure_bootstrap_accounts(session)
    try:
        yield
    finally:
        shutdown_tracing()


app = FastAPI(
    title=settings.app_name,
    description="Multi-agent customer support operations copilot",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
app.add_middleware(PerformanceMiddleware, settings=settings)
app.include_router(router, prefix=settings.api_prefix)
app.include_router(auth_router, prefix=settings.api_prefix)


@app.exception_handler(RequestValidationError)
async def sanitized_validation_error(request, error):
    # Pydantic's default response includes raw input (including passwords).
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": list(item["loc"]), "type": item["type"], "msg": "请求参数不符合要求"}
        for item in error.errors()
    ]})


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"name": settings.app_name, "docs": "/docs", "health": "/health"}


@app.get("/health", response_model=HealthResponse, tags=["system"])
def root_health(session: Session = Depends(get_db)) -> HealthResponse:
    """Standard container readiness alias for the versioned health endpoint."""
    return api_health(settings=settings, session=session)
