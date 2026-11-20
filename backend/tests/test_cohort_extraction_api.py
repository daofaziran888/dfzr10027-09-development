"""促销分群提取接口测试：用最小 ASGI 驱动直接调用路由，覆盖 API 契约。"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from app.main import app


def _call(method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
    body = json.dumps(payload).encode() if payload is not None else b""
    headers = [(b"content-type", b"application/json")] if payload is not None else []
    messages: list[dict[str, Any]] = [{
        "type": "http.request", "body": body, "more_body": False,
    }]
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": headers,
        "client": ("test", 1), "server": ("test", 80),
        "scheme": "http", "root_path": "",
    }
    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    raw = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(raw) if raw else None


class TestCohortExtractionApi:
    def test_preview_returns_three_groups(self):
        status, body = _call("GET", "/api/cohort_extraction/preview")
        assert status == 200
        assert body["verification_scope"] == 3
        assert body["delivered_count"] == 1
        assert body["pending_count"] == 1
        assert body["excluded_count"] == 1
        assert [m["user_id"] for m in body["members"]] == ["E10003"]

    def test_extract_without_body_uses_default_rule(self):
        status, body = _call("POST", "/api/cohort_extraction/extract")
        assert status == 200
        assert body["ok"] is True
        entry = body["entry"]
        assert entry["delivered_count"] == 1
        assert entry["pending_count"] == 1
        assert entry["report"]["member_count"] == 1

    def test_two_rounds_and_replay_over_api(self):
        _, first = _call("POST", "/api/cohort_extraction/extract", {"label": "首轮"})
        first_id = first["entry"]["run_id"]
        assert first["entry"]["delivered_count"] == 1
        assert first["entry"]["pending_count"] == 1

        _, tagged = _call(
            "POST", "/api/cohort_extraction/tags", {"user_id": "E10002", "tag": "高价值"})
        assert tagged["ok"] is True

        _, second = _call("POST", "/api/cohort_extraction/extract", {"label": "补齐后"})
        assert second["entry"]["delivered_count"] == 2
        assert second["entry"]["pending_count"] == 0
        assert second["entry"]["report"]["total_ltv"] == 3700.0

        _, runs = _call("GET", "/api/cohort_extraction/runs")
        assert [r["delivered_count"] for r in runs["items"]] == [1, 2]
        assert [r["pending_count"] for r in runs["items"]] == [1, 0]

        status, replayed = _call("POST", f"/api/cohort_extraction/runs/{first_id}/replay")
        assert status == 200
        assert replayed["ok"] is True
        assert replayed["entry"]["replayable"] is True
        assert replayed["entry"]["stored_report"]["total_ltv"] == 1500.0
        assert replayed["entry"]["replayed_report"]["member_count"] == 1

    def test_reports_are_one_per_round(self):
        _call("POST", "/api/cohort_extraction/extract")
        _call("POST", "/api/cohort_extraction/extract")
        _, reports = _call("GET", "/api/cohort_extraction/reports")
        assert reports["total"] == 2
        # 两轮都没有补齐标签，报表口径一致
        assert [r["member_count"] for r in reports["items"]] == [1, 1]

    def test_replay_missing_run_rejected(self):
        status, body = _call("POST", "/api/cohort_extraction/runs/CEX-NONE/replay")
        assert status == 200
        assert body["ok"] is False

    def test_get_missing_run_404(self):
        status, _ = _call("GET", "/api/cohort_extraction/runs/CEX-NONE")
        assert status == 404
