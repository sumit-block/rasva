"""Uniform, human-readable error responses: {"error": {"code", "message"}}."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from rasva.jobs.repository import JobNotFound
from rasva.jobs.states import InvalidTransition
from rasva.services.assets import AssetError, AssetNotFound, QuotaExceeded
from rasva.services.projects import ProjectNotFound
from rasva.services.settings_store import SettingError

log = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _resp(status: int, code: str, message: str, **extra: object) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, **extra}}, status_code=status)


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(_: Request, exc: ApiError) -> JSONResponse:
        return _resp(exc.status, exc.code, exc.message)

    @app.exception_handler(ProjectNotFound)
    async def _p404(_: Request, exc: ProjectNotFound) -> JSONResponse:
        return _resp(404, "project_not_found", "That project does not exist.")

    @app.exception_handler(AssetNotFound)
    async def _a404(_: Request, exc: AssetNotFound) -> JSONResponse:
        return _resp(404, "asset_not_found", "That file does not exist.")

    @app.exception_handler(JobNotFound)
    async def _j404(_: Request, exc: JobNotFound) -> JSONResponse:
        return _resp(404, "job_not_found", "That job does not exist.")

    @app.exception_handler(AssetError)
    async def _asset(_: Request, exc: AssetError) -> JSONResponse:
        return _resp(422, "invalid_file", str(exc))

    @app.exception_handler(QuotaExceeded)
    async def _quota(_: Request, exc: QuotaExceeded) -> JSONResponse:
        return _resp(507, "storage_full", " ".join(exc.reasons), reasons=exc.reasons)

    @app.exception_handler(SettingError)
    async def _setting(_: Request, exc: SettingError) -> JSONResponse:
        return _resp(422, "invalid_setting", str(exc))

    @app.exception_handler(InvalidTransition)
    async def _conflict(_: Request, exc: InvalidTransition) -> JSONResponse:
        return _resp(
            409, "invalid_state", f"This job is already {exc.current.value.replace('_', ' ')}."
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        msg = first.get("msg", "Invalid request")
        return _resp(422, "invalid_request", f"{where}: {msg}" if where else msg)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _resp(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", extra={"path": request.url.path})
        return _resp(
            500, "internal_error", "Something went wrong inside Rasva. Your work is saved."
        )
