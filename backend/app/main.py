import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.app.api.calendar import router as calendar_router
from backend.app.api.events import router as events_router
from backend.app.api.evidences import router as evidences_router
from backend.app.api.reminders import router as reminders_router
from backend.app.api.review import router as review_router
from backend.app.api.share import router as share_router
from backend.app.api.source_items import router as source_items_router
from backend.app.api.user_preferences import router as user_preferences_router
from backend.app.core.config import settings
from backend.app.db.database import initialize_database


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title="Planbox API",
    version="0.1.0",
    description="AI activity opportunity manager API",
    lifespan=lifespan,
)

# CORS 白名单来自配置。生产环境必须通过 CORS_ALLOW_ORIGINS 显式指定，
# 否则小程序与线上前端会被浏览器直接拒绝。
allow_origins = settings.cors_allow_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 单次请求体上限。不设限的话一个大文件就能把容器磁盘写满。
_max_body = settings.max_upload_bytes


@app.middleware("http")
async def limit_request_size(request: Request, call_next):
    """上传前先拦 Content-Length。

    拒绝时必须把请求体读完再响应，否则客户端还在往 socket 写数据，
    服务端直接回包会触发 TCP RST，客户端看到的是连接被重置而非 413。
    """
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit():
        if int(content_length) > _max_body:
            limit_mb = _max_body // (1024 * 1024)
            await _drain_body(request)
            return JSONResponse(
                status_code=413,
                content={"detail": f"请求体超过上限 {limit_mb} MB"},
            )
    return await call_next(request)


async def _drain_body(request: Request) -> None:
    """丢弃请求体，避免 RST。大小已由调用方确认可控。"""
    try:
        async for _ in request.stream():
            pass
    except Exception:
        # 客户端可能已经断开，这里无需处理
        pass


@app.exception_handler(413)
async def payload_too_large(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=413, content={"detail": "请求体过大"})


app.include_router(share_router, prefix="/api/share", tags=["share"])
app.include_router(source_items_router, prefix="/api/source-items", tags=["source-items"])
app.include_router(review_router, prefix="/api/review-queue", tags=["review-queue"])
app.include_router(events_router, prefix="/api/events", tags=["events"])
app.include_router(evidences_router, prefix="/api/evidences", tags=["evidences"])
app.include_router(reminders_router, prefix="/api/reminders", tags=["reminders"])
app.include_router(
    user_preferences_router, prefix="/api/user-preferences", tags=["user-preferences"]
)
app.include_router(calendar_router, prefix="/api/calendar", tags=["calendar"])


@app.get("/api/health")
def health_check() -> dict[str, object]:
    """健康检查。同时暴露运行环境信息，便于公网验证时确认配置生效。"""
    return {
        "status": "ok",
        "env": {
            "port": os.environ.get("PORT", "unset"),
            "llm_provider": settings.llm_provider,
            "data_dir": str(settings.data_dir),
            "db_path": str(settings.database_path),
            "cors_origins": allow_origins,
            "max_upload_mb": settings.max_upload_bytes // (1024 * 1024),
        },
    }


@app.get("/api/version")
def version() -> dict[str, str]:
    return {"version": "0.2.0", "api": "planbox"}