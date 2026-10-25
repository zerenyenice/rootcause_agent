"""``/health`` and ``/version`` endpoints."""

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import Annotated, Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import rootcause_api
from rootcause_api.envelope import Envelope, Meta, ok
from rootcause_api.middleware import request_id_of
from rootcause_core.logging import get_logger
from rootcause_core.settings import Settings

logger = get_logger(__name__)

PROBE_TIMEOUT_SECONDS = 2
GIT_SHA_VARIABLE = "ROOTCAUSE_GIT_SHA"
OPENVIKING_HEALTH_PATH = "/health"
_PLAIN_POSTGRES_SCHEME = "postgresql://"
_ASYNC_POSTGRES_SCHEME = "postgresql+asyncpg://"

CheckResult = Literal["ok", "fail", "skipped"]

router = APIRouter()


class HealthReport(BaseModel):
    status: Literal["ok", "degraded"]
    checks: dict[str, CheckResult]
    version: str


class VersionInfo(BaseModel):
    version: str
    git_sha: str


def get_app_settings(request: Request) -> Settings:
    """Settings the app was created with (stored on ``app.state``)."""
    settings: Settings = request.app.state.settings
    return settings


AppSettings = Annotated[Settings, Depends(get_app_settings)]


def _async_database_url(url: str) -> str:
    if url.startswith(_PLAIN_POSTGRES_SCHEME):
        return _ASYNC_POSTGRES_SCHEME + url.removeprefix(_PLAIN_POSTGRES_SCHEME)
    return url


async def _select_one(url: str) -> None:
    engine = create_async_engine(url, connect_args={"timeout": PROBE_TIMEOUT_SECONDS})
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


async def check_postgres(settings: Settings) -> str:
    """``ok`` when ``SELECT 1`` succeeds within the timeout, else ``fail``."""
    try:
        url = _async_database_url(settings.database_url.get_secret_value())
        await asyncio.wait_for(_select_one(url), PROBE_TIMEOUT_SECONDS)
    except Exception as error:
        # Type only: driver messages can embed the connection string.
        logger.warning("health_check_failed", check="postgres", error_type=type(error).__name__)
        return "fail"
    return "ok"


def _openviking_target(base: str) -> str:
    if urlsplit(base).path.strip("/"):
        return base
    return base.rstrip("/") + OPENVIKING_HEALTH_PATH


async def check_openviking(settings: Settings) -> str:
    """``skipped`` when unconfigured, ``ok`` on a 2xx answer, else ``fail``."""
    if settings.openviking_url is None:
        return "skipped"
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
            response = await client.get(_openviking_target(str(settings.openviking_url)))
            response.raise_for_status()
    except Exception as error:
        logger.warning("health_check_failed", check="openviking", error_type=type(error).__name__)
        return "fail"
    return "ok"


async def _run_probe(
    name: str, probe: Callable[[Settings], Awaitable[str]], settings: Settings
) -> CheckResult:
    """Run one probe; any exception or unexpected value degrades to ``fail``."""
    try:
        result = await probe(settings)
    except Exception as error:
        logger.warning("health_probe_raised", check=name, error_type=type(error).__name__)
        return "fail"
    if result == "ok":
        return "ok"
    if result == "skipped":
        return "skipped"
    return "fail"


@router.get("/health", response_model=Envelope[HealthReport])
async def health(request: Request, settings: AppSettings) -> Envelope[HealthReport]:
    """Report dependency status; always 200 so probes can read the detail."""
    postgres, openviking = await asyncio.gather(
        _run_probe("postgres", check_postgres, settings),
        _run_probe("openviking", check_openviking, settings),
    )
    checks = {"postgres": postgres, "openviking": openviking}
    status: Literal["ok", "degraded"] = "degraded" if "fail" in checks.values() else "ok"
    report = HealthReport(status=status, checks=checks, version=rootcause_api.__version__)
    return ok(report, Meta(request_id=request_id_of(request)))


@router.get("/version", response_model=Envelope[VersionInfo])
async def version(request: Request) -> Envelope[VersionInfo]:
    info = VersionInfo(
        version=rootcause_api.__version__,
        git_sha=os.environ.get(GIT_SHA_VARIABLE, "unknown"),
    )
    return ok(info, Meta(request_id=request_id_of(request)))
