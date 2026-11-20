"""促销分群提取：按排除标签校验候选顾客，生成可解释的交付名单与分群报表。

业务口径：
- 先按分群规则（区域、消费门槛）圈出候选顾客，候选数即本轮「核实范围」。
- 候选顾客再按排除标签判定：命中排除标签 -> 明确排除；
  判定标签尚未送达 -> 待判（不能视为已通过）；两者都不进交付名单。
- 只有标签齐全且未命中排除的顾客才进入本次实际交付名单；
  分群报表只根据这份交付名单汇总人数、区域分布与 LTV 总额。
- 每次提交提取都会落一份不可变快照，标签补齐后重新提取生成新一轮，
  旧轮次仍可按原样重放复查。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from app.fixtures import PROMOTION_COHORT_USERS
from app.store import store

MODULE = "cohort_rule"
RUN_TABLE = "_cohort_extract_runs"
REPORT_TABLE = "_cohort_reports"
TAG_TABLE = "_cohort_tag_overlay"

DEFAULT_RULE: dict[str, Any] = {
    "region": "华东",
    "min_ltv": 1000.0,
    "exclude_tags": ["促销排除"],
}

DELIVERED = "已交付"
EXCLUDED = "明确排除"
PENDING = "待判"


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _normalise_tags(raw: Any) -> list[str]:
    """排除标签支持列表或逗号分隔字符串，统一成去空后的标签列表。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = raw.replace("，", ",").split(",")
    else:
        parts = list(raw)
    return [str(part).strip() for part in parts if str(part).strip()]


def _normalise_rule(rule: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(DEFAULT_RULE)
    if rule:
        merged.update({key: value for key, value in rule.items() if value is not None})
    merged["exclude_tags"] = _normalise_tags(merged.get("exclude_tags"))
    merged["min_ltv"] = _to_float(merged.get("min_ltv", 0))
    return merged


class CohortExtractionService:
    # ------------------------------------------------------------------ 标签
    def _tag_overlay(self) -> dict[str, dict[str, Any]]:
        return {row["user_id"]: row for row in store.rows(TAG_TABLE) if row.get("user_id")}

    def backfill_tag(self, user_id: str, tag: str) -> dict[str, Any]:
        """登记迟到的判定标签；同一顾客以最后一次补齐为准。"""
        rows = store.rows(TAG_TABLE)
        for row in rows:
            if row.get("user_id") == user_id:
                row.update({"tag": tag, "updated_at": datetime.now().isoformat()})
                return dict(row)
        entry = {
            "user_id": user_id,
            "tag": tag,
            "updated_at": datetime.now().isoformat(),
        }
        rows.append(entry)
        return dict(entry)

    def _effective_tag(self, user: dict[str, Any], overlay: dict[str, dict[str, Any]]) -> str | None:
        if user["user_id"] in overlay:
            return overlay[user["user_id"]].get("tag")
        return user.get("tag")

    # ------------------------------------------------------------------ 评估
    def evaluate(self, rule: dict[str, Any] | None = None) -> dict[str, Any]:
        """按规则把候选顾客分成 交付 / 明确排除 / 待判 三组，并给出判定原因。"""
        normalised = _normalise_rule(rule)
        overlay = self._tag_overlay()
        exclude_tags = set(normalised["exclude_tags"])
        region = normalised.get("region")
        min_ltv = normalised["min_ltv"]

        candidates: list[dict[str, Any]] = []
        members: list[dict[str, Any]] = []
        excluded_members: list[dict[str, Any]] = []
        pending_members: list[dict[str, Any]] = []

        for user in PROMOTION_COHORT_USERS:
            if region and user.get("region") != region:
                continue
            if _to_float(user.get("ltv")) < min_ltv:
                continue
            candidates.append(user)

            tag = self._effective_tag(user, overlay)
            has_tag = tag is not None and str(tag).strip() != ""
            row = {
                **user,
                "判定标签": tag,
            }

            if exclude_tags:
                if not has_tag:
                    row["判定结果"] = PENDING
                    row["入选原因"] = "缺少用于排除判定的标签，待标签补齐后复核"
                    pending_members.append(row)
                    continue
                if str(tag).strip() in exclude_tags:
                    row["判定结果"] = EXCLUDED
                    row["入选原因"] = f"命中排除标签「{str(tag).strip()}」"
                    excluded_members.append(row)
                    continue
                row["判定结果"] = DELIVERED
                row["入选原因"] = "已过消费门槛，且未命中排除标签"
                members.append(row)
            else:
                row["判定结果"] = DELIVERED
                row["入选原因"] = "已过消费门槛，规则未配置排除标签"
                members.append(row)

        report = self._build_report(members)
        return {
            "rule": normalised,
            "verification_scope": len(candidates),
            "candidate_count": len(candidates),
            "delivered_count": len(members),
            "pending_count": len(pending_members),
            "excluded_count": len(excluded_members),
            "usable": len(members) > 0,
            "members": members,
            "pending_members": pending_members,
            "excluded_members": excluded_members,
            "report": report,
        }

    @staticmethod
    def _build_report(members: list[dict[str, Any]]) -> dict[str, Any]:
        """分群报表只根据实际交付名单汇总，待判与明确排除不计入。"""
        total_ltv = sum(_to_float(member.get("ltv")) for member in members)
        region_dist: dict[str, int] = {}
        for member in members:
            region = str(member.get("region"))
            region_dist[region] = region_dist.get(region, 0) + 1
        return {
            "member_count": len(members),
            "region_distribution": region_dist,
            "total_ltv": round(total_ltv, 2),
            "avg_ltv": round(total_ltv / len(members), 2) if members else 0.0,
        }

    # ------------------------------------------------------------------ 提取
    def submit_extraction(
        self,
        rule: dict[str, Any] | None = None,
        label: str | None = None,
    ) -> dict[str, Any]:
        """执行一次提取：把评估结果落成不可变快照，并把交付名单交给分群报表。"""
        evaluation = self.evaluate(rule)
        runs = store.rows(RUN_TABLE)
        sequence = len(runs) + 1
        now = datetime.now()
        run = {
            "run_id": f"CEX-{now.strftime('%Y%m%d')}-{sequence:03d}",
            "round": sequence,
            "label": label or f"第{sequence}轮提取",
            "created_at": now.isoformat(),
            "status": DELIVERED if evaluation["usable"] else "无可用名单",
            "rule": evaluation["rule"],
            "verification_scope": evaluation["verification_scope"],
            "candidate_count": evaluation["candidate_count"],
            "delivered_count": evaluation["delivered_count"],
            "pending_count": evaluation["pending_count"],
            "excluded_count": evaluation["excluded_count"],
            "usable": evaluation["usable"],
            # 快照只保存当轮实际交付名单；标签后续补齐不会回写这里
            "members": deepcopy(evaluation["members"]),
            "pending_members": deepcopy(evaluation["pending_members"]),
            "excluded_members": deepcopy(evaluation["excluded_members"]),
            "report": deepcopy(evaluation["report"]),
        }
        runs.append(run)

        report_entry = {
            **deepcopy(evaluation["report"]),
            "source_run": run["run_id"],
            "round": run["round"],
            "created_at": run["created_at"],
            "pending_count": run["pending_count"],
            "excluded_count": run["excluded_count"],
            "verification_scope": run["verification_scope"],
        }
        store.rows(REPORT_TABLE).append(report_entry)
        return self._copy_run(run)

    def list_runs(self) -> list[dict[str, Any]]:
        return [self._summary(run) for run in store.rows(RUN_TABLE)]

    def list_reports(self) -> list[dict[str, Any]]:
        """分群报表按提取轮次各存一份，便于对照每轮交付名单的汇总口径。"""
        return [deepcopy(report) for report in store.rows(REPORT_TABLE)]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        for run in store.rows(RUN_TABLE):
            if run.get("run_id") == run_id:
                return self._copy_run(run)
        return None

    # ------------------------------------------------------------------ 重放
    def replay(self, run_id: str) -> dict[str, Any] | None:
        """按旧快照的交付名单重算报表，确认已交付名单仍可原样复查。"""
        run = None
        for row in store.rows(RUN_TABLE):
            if row.get("run_id") == run_id:
                run = row
                break
        if run is None:
            return None

        replayed_report = self._build_report(deepcopy(run.get("members", [])))
        stored_report = deepcopy(run.get("report", {}))
        matches = all(
            replayed_report[key] == stored_report.get(key)
            for key in ("member_count", "region_distribution", "total_ltv", "avg_ltv")
        )
        return {
            "run_id": run_id,
            "round": run.get("round"),
            "created_at": run.get("created_at"),
            "replayable": matches,
            "matches_stored": matches,
            "delivered_count": run.get("delivered_count"),
            "pending_count": run.get("pending_count"),
            "excluded_count": run.get("excluded_count"),
            "members": deepcopy(run.get("members", [])),
            "stored_report": stored_report,
            "replayed_report": replayed_report,
        }

    # ------------------------------------------------------------------ 辅助
    @staticmethod
    def _copy_run(run: dict[str, Any]) -> dict[str, Any]:
        return deepcopy(run)

    @staticmethod
    def _summary(run: dict[str, Any]) -> dict[str, Any]:
        return {
            key: deepcopy(run.get(key))
            for key in (
                "run_id", "round", "label", "created_at", "status", "rule",
                "verification_scope", "candidate_count", "delivered_count",
                "pending_count", "excluded_count", "usable", "report",
            )
        }
