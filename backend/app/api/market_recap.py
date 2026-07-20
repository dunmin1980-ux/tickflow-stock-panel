"""AI 大盘复盘 API — 流式复盘 + 报告持久化。

路由前缀: /api/market-recap

端点:
  POST /analyze                AI 流式大盘复盘(NDJSON)
  GET  /reports                历史复盘列表
  POST /reports                保存一条复盘报告
  DELETE /reports/{report_id}  删除一条复盘报告
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from app.services import market_recap_reports
from app.services.market_recap import recap_market_stream

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/market-recap", tags=["market-recap"])


class AnalyzeRequest(BaseModel):
    """AI 大盘复盘请求。"""
    as_of: str | None = None  # 可选:复盘日期(YYYY-MM-DD),缺省取最新有数据日
    focus: str = ""           # 可选:用户追加的复盘关注点


@router.post("/analyze")
async def analyze_market(request: Request, req: AnalyzeRequest):
    """AI 大盘复盘 — NDJSON 流式返回。

    装配市场总览(指数/涨跌/连板/封板/板块/情绪雷达)→ 复盘提示词 →
    流式调用 LLM → 逐 chunk 以 NDJSON 推给前端(每行一个 JSON)。

    协议:
      {"type":"meta","as_of","emotion_score","emotion_label","summary"}
      {"type":"delta","content":"..."}
      {"type":"error","message":"..."}
      {"type":"done"}
    """
    from datetime import date as date_cls

    repo = request.app.state.repo
    quote_service = getattr(request.app.state, "quote_service", None)
    depth_service = getattr(request.app.state, "depth_service", None)

    as_of = None
    if req.as_of:
        try:
            as_of = date_cls.fromisoformat(req.as_of)
        except ValueError as exc:
            raise HTTPException(
                400, f"as_of 格式应为 YYYY-MM-DD,收到: {req.as_of}"
            ) from exc

    async def stream_gen():
        async for chunk in recap_market_stream(
            repo, quote_service, depth_service, as_of, req.focus
        ):
            yield chunk + "\n"

    return StreamingResponse(
        stream_gen(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ================================================================
# 报告 CRUD(历史复盘持久化)
# ================================================================

class SaveReportRequest(BaseModel):
    """保存一条 AI 大盘复盘报告。"""
    as_of: str
    focus: str = ""
    content: str
    summary: str = ""
    emotion_score: int | None = None
    emotion_label: str = ""


class MarketRecapBodyDTO(BaseModel):
    """Explicit projection for one authenticated, online-only recap body."""

    model_config = ConfigDict(extra="forbid")

    id: str
    as_of: str | None = None
    focus: str | None = None
    title: str | None = None
    content: str
    summary: str | None = None
    emotion_score: int | None = None
    emotion_label: str | None = None
    created_at: str | None = None
    data_as_of: str | None = None
    verification_status: str | None = None
    can_publish: bool | None = None
    trading_advice: bool | None = None


class MarketRecapBodyResponseDTO(BaseModel):
    report: MarketRecapBodyDTO


_MARKET_BODY_FIELDS = tuple(MarketRecapBodyDTO.model_fields)


@router.get("/reports")
def list_reports(request: Request):
    """Return metadata only; recap bodies use the single-report no-store endpoint."""
    return JSONResponse(
        {"reports": market_recap_reports.list_report_metadata()},
        headers={"Cache-Control": "private, no-store"},
    )


@router.get(
    "/reports/{report_id}",
    response_model=MarketRecapBodyResponseDTO,
    response_model_exclude_none=True,
)
def get_report(request: Request, report_id: str):
    """Read one body by exact ID without allowing persistent client caches."""
    report = next(
        (item for item in market_recap_reports.list_reports() if item.get("id") == report_id),
        None,
    )
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    projected = {field: report[field] for field in _MARKET_BODY_FIELDS if field in report}
    body = MarketRecapBodyDTO.model_validate(projected)
    return JSONResponse(
        {"report": body.model_dump(exclude_none=True)},
        headers={"Cache-Control": "private, no-store"},
    )


@router.post("/reports")
def save_report(request: Request, req: SaveReportRequest):
    """保存一条复盘报告。"""
    report = market_recap_reports.save_report({
        "as_of": req.as_of,
        "focus": req.focus,
        "content": req.content,
        "summary": req.summary,
        "emotion_score": req.emotion_score,
        "emotion_label": req.emotion_label,
    })
    # 推送到飞书(可选): 与定时复盘共用同一开关 review_push_enabled 与 _maybe_push_review。
    # 内部 try/except 静默降级, 不影响归档返回值。
    from app.jobs.daily_pipeline import _maybe_push_review
    _maybe_push_review(req.content, {
        "as_of": req.as_of,
        "emotion_label": req.emotion_label,
    })
    return {"ok": True, "report": report}


@router.delete("/reports/{report_id}")
def delete_report(request: Request, report_id: str):
    """删除一条复盘报告。"""
    ok = market_recap_reports.delete_report(report_id)
    return {"ok": ok}
