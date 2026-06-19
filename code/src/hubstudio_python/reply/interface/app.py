"""FastAPI 应用：前端 / Chrome 插件调用。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from hubstudio_python.config import bootstrap_rag_env
from hubstudio_python.reply.sql.connection import init_db
from hubstudio_python.reply.sql.creator_state import (
    CreatorShopState,
    get_creator_shop_state,
    upsert_creator_shop_state,
)
from hubstudio_python.reply.sql.config import db_config_from_env
from hubstudio_python.reply.service.generate import generate_reply, result_to_dict
from hubstudio_python.reply.service.trace import log_reply_summary


class ReplyBody(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    shop: str
    creator_id: str | None = Field(default=None, alias="creatorId")
    creator_name: str | None = Field(default=None, alias="creatorName")
    creator_type: str | None = Field(default=None, alias="creatorType")
    creator_progress: list[str] | str | None = Field(default=None, alias="creatorProgress")
    creator_emotion: str | None = Field(default=None, alias="creatorEmotion")
    context_text: str = Field(default="", alias="contextText")
    monthly_gmv: float | None = Field(default=None, alias="monthlyGmv")
    avg_video_views: int | None = Field(default=None, alias="avgVideoViews")
    video_count: int | None = Field(default=None, alias="videoCount")
    other_creator_conditions: list[str] | None = Field(default=None, alias="otherCreatorConditions")
    options_base: dict[str, Any] | None = Field(default=None, alias="optionsBase")
    affiliate_center_refused: bool | None = Field(default=None, alias="affiliateCenterRefused")
    product_id: str | None = Field(default=None, alias="productId")
    campaign_id: str | None = Field(default=None, alias="campaignId")


class IntentClassifyBody(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    shop: str
    creator_type: str = Field(default="GEN", alias="creatorType")
    creator_progress: list[str] | str | None = Field(default=None, alias="creatorProgress")
    creator_emotion: str | None = Field(default=None, alias="creatorEmotion")
    creator_reply_frequency: str | None = Field(default=None, alias="creatorReplyFrequency")
    context_text: str = Field(default="", alias="contextText")
    message: str | None = Field(default=None, description="可选；不传则从 contextText 取最后一条达人消息")
    use_llm: bool | None = Field(default=None, alias="useLlm")


class CreatorStateBody(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    creator_id: str = Field(alias="creatorId")
    shop: str
    creator_name: str = Field(default="", alias="creatorName")
    creator_type: str = Field(default="GEN", alias="creatorType")
    creator_progress: list[str] | str = Field(default="GEN", alias="creatorProgress")
    creator_emotion: str = Field(default="GEN", alias="creatorEmotion")
    monthly_gmv: float | None = Field(default=None, alias="monthlyGmv")
    other_creator_conditions: list[str] = Field(default_factory=list, alias="otherCreatorConditions")
    shop_rejected: bool = Field(default=False, alias="shopRejected")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    bootstrap_rag_env()
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Chrome Reply RAG API",
        version="0.1.0",
        lifespan=_lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        cfg = db_config_from_env()
        return {
            "ok": True,
            "service": "reply",
            "db_enabled": cfg.enabled,
            "db_path": str(cfg.sqlite_path()) if cfg.enabled else None,
        }

    @app.post("/api/reply")
    @app.post("/v1/reply")
    def post_reply(
        body: ReplyBody,
        trace: bool = Query(default=False, description="为 true 时返回完整调试字段（steps、matched_chunks 等）"),
    ) -> dict[str, Any]:
        payload = body.model_dump(by_alias=True, exclude_none=True)
        if body.options_base:
            payload["optionsBase"] = body.options_base
        if trace:
            payload["trace"] = True
        try:
            result = generate_reply(payload)
            log_reply_summary(
                shop=result.shop,
                latest_creator_message=result.latest_creator_message,
                intent_category=result.intent_category,
                intent_confidence=result.intent_confidence,
                intent_matched_by=result.intent_matched_by,
                matched_chunks=len(result.matched_chunks or []),
                reply=result.reply,
                error=result.error,
                silent=result.silent,
            )
            if trace:
                out = result_to_dict(result)
                out["ok"] = result.ok
                return out
            out: dict[str, Any] = {"ok": result.ok, "reply": result.reply}
            if result.withdraw:
                out["withdraw"] = True
                out["reason"] = result.error or "affiliate_center_refused"
            elif not (result.reply or "").strip() and (result.error or result.silent):
                out["reason"] = result.error or "silent_empty"
            return out
        except Exception as exc:
            import logging

            logging.getLogger("hubstudio.reply").exception("POST /api/reply failed")
            raise HTTPException(status_code=500, detail=str(exc) or exc.__class__.__name__) from exc

    @app.post("/api/intent")
    @app.post("/v1/intent")
    def post_intent(body: IntentClassifyBody) -> dict[str, Any]:
        from hubstudio_python.reply.service.intent_classify import classify_creator_intent

        payload = body.model_dump(by_alias=True, exclude_none=True)
        try:
            result = classify_creator_intent(payload)
            return result.to_api_dict()
        except Exception as exc:
            import logging

            logging.getLogger("hubstudio.reply").exception("POST /api/intent failed")
            raise HTTPException(status_code=500, detail=str(exc) or exc.__class__.__name__) from exc

    @app.get("/api/creators/{creator_id}")
    def get_creator(creator_id: str, shop: str = Query(...)) -> dict[str, Any]:
        state = get_creator_shop_state(creator_id, shop)
        if state is None:
            raise HTTPException(status_code=404, detail="creator shop state not found")
        return {"ok": True, "state": state.to_dict()}

    @app.put("/api/creators/{creator_id}")
    def put_creator(creator_id: str, body: CreatorStateBody) -> dict[str, Any]:
        if body.creator_id != creator_id:
            raise HTTPException(status_code=400, detail="creatorId mismatch")
        prog = body.creator_progress
        state = CreatorShopState(
            creator_id=creator_id,
            shop=body.shop,
            creator_name=body.creator_name,
            creator_type=body.creator_type,
            creator_progress=prog,
            creator_emotion=body.creator_emotion,
            monthly_gmv=body.monthly_gmv,
            other_creator_conditions=list(body.other_creator_conditions),
            shop_rejected=body.shop_rejected,
        )
        upsert_creator_shop_state(state)
        return {"ok": True, "state": state.to_dict()}

    return app


def serve_fastapi(*, host: str = "127.0.0.1", port: int = 8765, trace: bool = True) -> None:
    import os

    import uvicorn

    if trace:
        os.environ.setdefault("HUBSTUDIO_REPLY_TRACE", "1")
    else:
        os.environ["HUBSTUDIO_REPLY_TRACE"] = "0"

    uvicorn.run(
        "hubstudio_python.reply.interface.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=False,
    )
