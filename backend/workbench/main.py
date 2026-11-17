"""HTTP 入口，业务逻辑由独立模块承担。"""
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError
from .config import Settings
from .db import database
from .security import Problem
from . import routes_auth, routes_catalog, routes_runs, routes_agent, routes_operations, routes_node_files

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, *, frontend_directory: Path | None = None):
    settings = settings or Settings()
    engine, factory = database(settings)

    @asynccontextmanager
    async def lifespan(_):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        yield
        engine.dispose()

    app = FastAPI(title="自动化程序工作台", version="0.1.0", lifespan=lifespan,
                  docs_url=settings.base_path+"/api/docs", openapi_url=settings.base_path+"/api/openapi.json")
    app.state.settings, app.state.engine, app.state.factory = settings, engine, factory

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = secrets.token_hex(12)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(Problem)
    async def problem_handler(request, exc):
        return JSONResponse(status_code=exc.status, content={"code": exc.code, "message": exc.message,
                            "request_id": request.state.request_id})

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, exc):
        # 不回显输入原文，避免把密码/连接材料带入错误响应和日志。
        return JSONResponse(status_code=422, content={"code": "validation_error", "message": "请求字段格式或范围不正确",
                            "request_id": request.state.request_id})

    @app.exception_handler(IntegrityError)
    async def integrity_handler(request, exc):
        return JSONResponse(status_code=409, content={"code": "conflict", "message": "资源冲突，请刷新后重试",
                            "request_id": request.state.request_id})

    @app.exception_handler(Exception)
    async def internal_handler(request, exc):
        logger.error("request_failed request_id=%s exception_type=%s", request.state.request_id, type(exc).__name__)
        return JSONResponse(status_code=500, content={"code": "internal_error", "message": "服务暂不可用，请联系维护人员",
                            "request_id": request.state.request_id})

    prefix = settings.base_path + "/api/v1"
    for router in (routes_auth.router, routes_catalog.router, routes_runs.router, routes_agent.router, routes_operations.router,
                   routes_node_files.router):
        app.include_router(router, prefix=prefix)
    static_root = frontend_directory or Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if (static_root / "assets").is_dir():
        app.mount(settings.base_path+"/assets", StaticFiles(directory=static_root/"assets"), name="assets")
    if (static_root / "index.html").is_file():
        @app.get(settings.base_path+"/", include_in_schema=False)
        def index():
            return FileResponse(static_root / "index.html")
    return app
