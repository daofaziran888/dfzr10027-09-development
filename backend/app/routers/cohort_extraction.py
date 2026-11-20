"""促销分群提取接口：按排除标签校验顾客，生成可解释的交付名单与分群报表。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.schemas import ActionResult
from app.services.cohort_extraction import CohortExtractionService

router = APIRouter(prefix="/api/cohort_extraction", tags=["促销分群提取"])

service = CohortExtractionService()


class ExtractRequest(BaseModel):
    """提交一次提取：规则可选，缺省使用华东 + 消费门槛 + 默认排除标签。"""

    region: str | None = None
    min_ltv: float | None = None
    exclude_tags: list[str] | str | None = None
    label: str | None = None


class TagBackfillRequest(BaseModel):
    """补齐某位顾客迟到的判定标签。"""

    user_id: str = Field(min_length=1)
    tag: str = Field(min_length=1)


@router.get("/preview")
def preview(
    region: str | None = None,
    min_ltv: float | None = None,
    exclude_tags: str | None = None,
) -> dict[str, Any]:
    """试算当前规则下的 交付 / 待判 / 明确排除 分组，不落快照。"""
    rule: dict[str, Any] = {}
    if region is not None:
        rule["region"] = region
    if min_ltv is not None:
        rule["min_ltv"] = min_ltv
    if exclude_tags is not None:
        rule["exclude_tags"] = exclude_tags
    return service.evaluate(rule)


@router.post("/extract", response_model=ActionResult)
def submit_extraction(payload: ExtractRequest | None = None) -> ActionResult:
    """提交提取：返回可交付名单、待判人数，并把交付名单交给分群报表汇总。"""
    payload = payload or ExtractRequest()
    rule: dict[str, Any] = {}
    if payload.region is not None:
        rule["region"] = payload.region
    if payload.min_ltv is not None:
        rule["min_ltv"] = payload.min_ltv
    if payload.exclude_tags is not None:
        rule["exclude_tags"] = payload.exclude_tags
    run = service.submit_extraction(rule, label=payload.label)
    return ActionResult(ok=True, message=f"提取完成：第{run['round']}轮", entry=run)


@router.post("/tags", response_model=ActionResult)
def backfill_tag(payload: TagBackfillRequest) -> ActionResult:
    """登记迟到标签；补齐后重新提交提取即可让待判顾客进入复核。"""
    entry = service.backfill_tag(payload.user_id, payload.tag)
    return ActionResult(ok=True, message=f"已补齐顾客 {payload.user_id} 的判定标签", entry=entry)


@router.get("/reports")
def list_reports() -> dict[str, Any]:
    """按轮次列出分群报表；每份报表只汇总该轮实际交付的名单。"""
    items = service.list_reports()
    return {"items": items, "total": len(items)}


@router.get("/runs")
def list_runs() -> dict[str, Any]:
    """按提交顺序列出全部提取轮次，便于对照两轮名单与报表计数。"""
    items = service.list_runs()
    return {"items": items, "total": len(items)}


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    """读取单轮提取快照；不存在时给出可读的错误说明。"""
    run = service.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"提取轮次 {run_id} 不存在")
    return run


@router.post("/runs/{run_id}/replay", response_model=ActionResult)
def replay_run(run_id: str) -> ActionResult:
    """按旧快照重放报表，确认已交付名单与当时的报表计数一致。"""
    result = service.replay(run_id)
    if result is None:
        return ActionResult(ok=False, message=f"提取轮次 {run_id} 不存在，无法重放")
    message = "旧名单重放一致" if result["replayable"] else "旧名单重放与存档报表不一致"
    return ActionResult(ok=result["replayable"], message=message, entry=result)
