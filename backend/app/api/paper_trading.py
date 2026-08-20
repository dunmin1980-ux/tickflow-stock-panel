"""Authenticated Visual Workbench API for the released Option C sidecar."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.services.phase2_visual_workbench import VisualWorkbenchError

router = APIRouter(prefix="/api/paper-trading", tags=["paper-trading"])

ERROR_MESSAGES = {
    "PAPER_INPUT_MISSING": "当前日期没有已验证的离线输入, 未执行模拟盘。",
    "PAPER_INPUT_DATE_MISMATCH": "离线输入日期与请求日期不一致。",
    "PAPER_INPUT_INVALID": "离线输入未通过严格合同校验。",
    "PAPER_TEST_FIXTURE_NOT_ALLOWED": "测试行情不能用于日常模拟账户。",
    "PAPER_REFERENCE_INVALID": "冻结研究证据校验失败, 已安全停止。",
    "PAPER_STATE_UNAVAILABLE": "模拟账户状态不可安全读取。",
    "PAPER_DAILY_UNAVAILABLE": "ChenQuant Daily 校验失败。",
    "PAPER_RUN_ALREADY_LOCKED": "另一个模拟盘任务正在运行, 本次没有重复执行。",
    "PAPER_RUN_REJECTED": "模拟盘引擎拒绝了本次输入, 状态未更改。",
}

ERROR_STATUS = {
    "PAPER_INPUT_MISSING": 409,
    "PAPER_INPUT_DATE_MISMATCH": 422,
    "PAPER_INPUT_INVALID": 422,
    "PAPER_TEST_FIXTURE_NOT_ALLOWED": 422,
    "PAPER_RUN_ALREADY_LOCKED": 409,
    "PAPER_RUN_REJECTED": 409,
}


class PaperRunRequest(BaseModel):
    target_date: date | None = None


def shanghai_today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def _service(request: Request):
    service = getattr(request.app.state, "phase2_visual_workbench", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "PAPER_STATE_UNAVAILABLE",
                "message": ERROR_MESSAGES["PAPER_STATE_UNAVAILABLE"],
            },
        )
    return service


def _translate(exc: VisualWorkbenchError) -> HTTPException:
    code = str(exc)
    public_code = code if code in ERROR_MESSAGES else "PAPER_STATE_UNAVAILABLE"
    return HTTPException(
        status_code=ERROR_STATUS.get(public_code, 503),
        detail={"code": public_code, "message": ERROR_MESSAGES[public_code]},
    )


@router.get("/dashboard")
def dashboard(request: Request, target_date: date | None = None):
    try:
        return _service(request).dashboard(target_date or shanghai_today())
    except VisualWorkbenchError as exc:
        raise _translate(exc) from None


@router.post("/run")
def run(request: Request, payload: PaperRunRequest):
    try:
        return _service(request).run(payload.target_date or shanghai_today())
    except VisualWorkbenchError as exc:
        raise _translate(exc) from None
