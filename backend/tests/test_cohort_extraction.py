"""促销分群提取测试：排除标签校验、待判不进名单、两轮提取与旧数据重放。"""
from __future__ import annotations

import pytest

from app.services.cohort_extraction import CohortExtractionService
from app.services.pipeline_orchestrator import run_cohort_extract_pipeline


@pytest.fixture()
def service():
    return CohortExtractionService()


class TestExclusionEvaluation:
    """三名都已过消费门槛的华东顾客：一个排除、一个待判、一个符合条件。"""

    def test_candidate_scope_is_three(self, service):
        result = service.evaluate()
        assert result["verification_scope"] == 3
        assert result["candidate_count"] == 3

    def test_excluded_member_is_tag_hit(self, service):
        result = service.evaluate()
        assert result["excluded_count"] == 1
        excluded = result["excluded_members"][0]
        assert excluded["user_id"] == "E10001"
        assert excluded["判定结果"] == "明确排除"
        assert "促销排除" in excluded["入选原因"]

    def test_missing_tag_is_pending_not_passed(self, service):
        result = service.evaluate()
        assert result["pending_count"] == 1
        pending = result["pending_members"][0]
        assert pending["user_id"] == "E10002"
        assert pending["判定结果"] == "待判"
        # 待判顾客不能出现在交付名单里
        assert pending["user_id"] not in {m["user_id"] for m in result["members"]}

    def test_only_tagged_non_excluded_is_delivered(self, service):
        result = service.evaluate()
        assert result["delivered_count"] == 1
        assert [m["user_id"] for m in result["members"]] == ["E10003"]
        member = result["members"][0]
        assert member["判定结果"] == "已交付"
        assert "未命中排除标签" in member["入选原因"]

    def test_report_counts_only_delivered(self, service):
        result = service.evaluate()
        report = result["report"]
        assert report["member_count"] == 1
        assert report["region_distribution"] == {"华东": 1}
        assert report["total_ltv"] == 1500.0

    def test_usable_when_list_non_empty(self, service):
        assert service.evaluate()["usable"] is True

    def test_no_usable_list_when_all_candidates_blocked(self, service):
        # 把唯一符合条件的人也排除掉：交付名单为空，但不等于全部通过
        result = service.evaluate({"exclude_tags": ["促销排除", "高价值"]})
        assert result["delivered_count"] == 0
        assert result["usable"] is False
        assert result["pending_count"] == 1
        assert result["excluded_count"] == 2
        assert result["report"]["member_count"] == 0
        assert result["report"]["total_ltv"] == 0.0


class TestTwoRounds:
    """待判顾客标签补齐后再次提交提取，两轮名单与报表计数都要能对上。"""

    def test_round1_then_backfill_then_round2(self, service):
        first = service.submit_extraction(label="首轮")
        assert first["round"] == 1
        assert first["delivered_count"] == 1
        assert first["pending_count"] == 1
        assert first["excluded_count"] == 1
        assert first["report"]["total_ltv"] == 1500.0

        service.backfill_tag("E10002", "高价值")
        second = service.submit_extraction(label="补齐后")
        assert second["round"] == 2
        assert second["delivered_count"] == 2
        assert second["pending_count"] == 0
        assert second["excluded_count"] == 1
        assert {m["user_id"] for m in second["members"]} == {"E10002", "E10003"}
        assert second["report"]["member_count"] == 2
        assert second["report"]["total_ltv"] == 3700.0

        runs = service.list_runs()
        assert [r["delivered_count"] for r in runs] == [1, 2]
        assert [r["pending_count"] for r in runs] == [1, 0]

    def test_backfilled_exclude_tag_stays_excluded(self, service):
        service.backfill_tag("E10002", "促销排除")
        result = service.submit_extraction()
        assert {m["user_id"] for m in result["members"]} == {"E10003"}
        assert result["pending_count"] == 0
        assert result["excluded_count"] == 2


class TestReplay:
    """旧轮次快照不因后续补齐标签而变化，可原样重放复查。"""

    def test_replay_old_run_matches_stored_report(self, service):
        first = service.submit_extraction(label="首轮")
        round1_members = [m["user_id"] for m in first["members"]]

        service.backfill_tag("E10002", "高价值")
        service.submit_extraction(label="补齐后")

        replay = service.replay(first["run_id"])
        assert replay is not None
        assert replay["replayable"] is True
        assert replay["matches_stored"] is True
        assert [m["user_id"] for m in replay["members"]] == round1_members
        assert replay["delivered_count"] == 1
        assert replay["replayed_report"] == replay["stored_report"]
        assert replay["replayed_report"]["total_ltv"] == 1500.0

    def test_replay_unknown_run_returns_none(self, service):
        assert service.replay("CEX-NOT-EXIST") is None


class TestPipelineExclusion:
    """原有分群提取管道也应拒绝待判与明确排除的顾客。"""

    def test_pipeline_with_exclude_tags(self):
        result = run_cohort_extract_pipeline(
            {"region": "华东", "min_ltv": 900.0, "exclude_tags": ["高价值"]}
        )
        # COHORT_USERS 里华东且过 900 门槛的是 U10001/U10003/U10005
        assert result["verification_scope"] == 3
        assert result["delivered_count"] == 1
        assert result["excluded_count"] == 2
        assert result["pending_count"] == 0
        assert {u["user_id"] for u in result["users"]} == {"U10005"}
        # 明确排除的高价值顾客不能进入交付名单
        assert "U10001" not in {u["user_id"] for u in result["users"]}

    def test_pipeline_report_only_counts_delivered(self):
        result = run_cohort_extract_pipeline(
            {"region": "华东", "min_ltv": 900.0, "exclude_tags": ["高价值"]}
        )
        assert result["report"]["member_count"] == 1
        assert result["report"]["total_ltv"] == 950.0
