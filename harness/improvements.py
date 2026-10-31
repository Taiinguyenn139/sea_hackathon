"""Evidence-based sales improvement drafts from completed development evaluations."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

# Order protects quote accuracy and service quality before optimizing conversion.
RULES: tuple[dict[str, Any], ...] = (
    {
        "id": "quote_accuracy",
        "metric": "price_error_rate",
        "label": "Lỗi giá & ưu đãi",
        "target": 0,
        "title": "Củng cố độ chính xác trước khi chào bán",
        "priority": "high",
        "reason": "Sai lãi suất, phí hoặc ưu đãi làm giảm độ tin cậy của lời tư vấn.",
        "steps": [
            "Đối chiếu các phát biểu bị đánh dấu với tool và ngày hiệu lực.",
            "Sửa luồng lấy dữ liệu; giữ điều kiện áp dụng và công bố bắt buộc trong lời chào.",
        ],
        "script": "Em sẽ kiểm tra lãi suất, phí và điều kiện đang có hiệu lực trước khi gửi phương án phù hợp cho anh/chị.",
        "prefixes": ("unexpected_numeric_claim:",),
        "counter": "price_errors",
    },
    {
        "id": "source_accuracy",
        "metric": "provenance_error_rate",
        "label": "Lỗi nguồn dữ liệu",
        "target": 0,
        "title": "Gắn đề xuất với nguồn dữ liệu còn hiệu lực",
        "priority": "high",
        "reason": "Thông tin thiếu nguồn hoặc hết hiệu lực chưa đủ cơ sở để tư vấn bán hàng.",
        "steps": [
            "Kiểm tra source spans và tool receipt của câu trả lời bị đánh dấu.",
            "Làm mới dữ liệu hết hạn và chỉ dùng dữ liệu được phép trên kênh hiện tại.",
        ],
        "script": "Em sẽ xác minh lại điều kiện áp dụng trên kênh này rồi mới xác nhận ưu đãi với anh/chị.",
        "prefixes": (
            "invalid_source_span",
            "missing_fact_receipt:",
            "invalid_fact_value:",
            "render_value_mismatch:",
            "unknown_customer_claim:",
            "non_authoritative_source:",
            "invalid_fact_time:",
            "inactive_fact:",
            "wrong_promotion_channel:",
            "oracle_fact_mismatch:",
            "missing_tool_receipt:",
            "untraced_numeric_claim",
        ),
        "counter": "provenance_errors",
    },
    {
        "id": "relevant_offer",
        "metric": "missing_offer_rate",
        "label": "Thiếu đề xuất phù hợp",
        "target": 0,
        "title": "Đưa ra phương án khi đã đủ điều kiện",
        "priority": "medium",
        "reason": "Benchmark ghi nhận lượt cần đề xuất nhưng câu trả lời chưa có thông tin chào bán mong đợi.",
        "steps": [
            "Kiểm tra nhu cầu và điều kiện phù hợp ở lượt bị thiếu đề xuất.",
            "Dùng dữ liệu tool còn hiệu lực để nêu phương án, điều kiện và một bước tiếp theo tự nguyện.",
        ],
        "script": "Dựa trên nhu cầu đã trao đổi, em có thể kiểm tra phương án phù hợp và giải thích chi phí, điều kiện. Anh/chị muốn xem chi tiết không?",
        "prefixes": ("missing_offer",),
        "counter": "missing_offers",
    },
    {
        "id": "remember_needs",
        "metric": "reask_rate",
        "label": "Tỷ lệ hỏi lại",
        "target": 0,
        "title": "Tiếp nối nhu cầu, giảm câu hỏi lặp",
        "priority": "medium",
        "reason": "Hỏi lại thông tin đã biết kéo dài tư vấn và làm gián đoạn trải nghiệm khách hàng.",
        "steps": [
            "Dùng hồ sơ đã xác thực để tóm tắt nhu cầu trước khi hỏi thêm.",
            "Chỉ hỏi phần còn thiếu; xác nhận riêng thông tin đang mâu thuẫn.",
        ],
        "script": "Em đã ghi nhận nhu cầu anh/chị trao đổi trước đó. Anh/chị có thay đổi gì cần cập nhật trước khi mình tiếp tục không?",
        "prefixes": ("reasked:",),
        "counter": "redundant_questions",
    },
    {
        "id": "handoff_context",
        "metric": "handoff_completeness",
        "label": "Đầy đủ khi chuyển tiếp",
        "target": 1,
        "title": "Giữ đủ ngữ cảnh khi chuyển cho chuyên viên",
        "priority": "medium",
        "reason": "Bàn giao thiếu thông tin khiến chuyên viên khó tiếp tục đúng nhu cầu và lời hẹn.",
        "steps": [
            "Bổ sung nhu cầu, thông tin đã xác nhận và băn khoăn còn mở vào bản bàn giao.",
            "Ghi rõ lời hẹn và bước tiếp theo; kiểm tra checklist của kịch bản.",
        ],
        "script": "Em sẽ chuyển nhu cầu, các điều kiện đã trao đổi và câu hỏi còn mở cho chuyên viên để anh/chị được hỗ trợ tiếp.",
        "prefixes": (),
        "counter": "handoff_present",
    },
    {
        "id": "next_step",
        "metric": "conversion_rate",
        "label": "Tỷ lệ chuyển đổi mô phỏng",
        "target": None,
        "title": "Thử nghiệm cách xử lý băn khoăn và đề nghị bước tiếp theo",
        "priority": "experiment",
        "reason": "Một số kịch bản chưa chuyển đổi. Đây là giả thuyết để rà soát, chưa phải kết luận về nguyên nhân hay cơ hội bán bị bỏ lỡ.",
        "steps": [
            "Đọc lại băn khoăn của khách; tách tình huống từ chối đúng, chưa xác thực và lỗi công cụ.",
            "Thử lời tư vấn giải đáp băn khoăn rồi đề nghị một bước tiếp theo có sự đồng ý của khách.",
            "Chạy lại cùng bộ dev; chỉ kết luận cải thiện khi KPI tăng và các chỉ số chất lượng không giảm.",
        ],
        "script": "Anh/chị còn băn khoăn điều gì để em giải thích thêm? Khi thấy phù hợp, anh/chị muốn được hướng dẫn hồ sơ hay hẹn chuyên viên trao đổi tiếp?",
        "prefixes": (),
        "counter": "converted",
    },
)


def metric_value(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def affected(rule: dict, row: dict) -> bool:
    counters = row.get("counters", {})
    if rule["id"] == "handoff_context":
        return counters.get("handoff_present", 0) < counters.get("handoff_required", 0)
    if rule["id"] == "next_step":
        # Correct declines, authentication restrictions and outages are not sales opportunities.
        blocked = {"unsuitable", "public_tools_only", "tool_failed"}
        return (
            row.get("status") == "completed"
            and counters.get("scenarios", 0) > counters.get("converted", 0)
            and not any(
                event.get("reason") in blocked
                for turn in row.get("turns", [])
                for event in turn.get("guard_events", [])
            )
        )
    return counters.get(rule["counter"], 0) > 0


def build_plan(run: dict, saved: dict[str, dict] | None = None) -> dict:
    eligible = run.get("schema_version") == 1 and run.get("status") == "completed" and run.get("split") == "dev"
    plan: dict[str, Any] = {
        "run_id": run["run_id"],
        "eligible": eligible,
        "mode": "harness",
        "actions": [],
        "message": "Đề xuất dựa trên KPI của Memory Harness. Lời tư vấn là bản nháp để rà soát và thử nghiệm.",
    }
    if not eligible:
        plan["message"] = "Chỉ lập kế hoạch từ lượt dev đã hoàn tất; giữ riêng dữ liệu held-out."
        return plan
    metrics = run.get("summary", {}).get("harness", {})
    for rule in RULES:
        value = metric_value(metrics.get(rule["metric"]))
        target = rule["target"]
        if value is None or not 0 <= value <= 1 or value == (1 if target is None else target):
            continue
        evidence = []
        rows = [row for row in run.get("results", []) if row.get("mode") == "harness" and affected(rule, row)]
        for row in rows:
            turns = row.get("turns", [])
            matched = (
                [turn for turn in turns if any(issue.startswith(rule["prefixes"]) for issue in turn.get("issues", []))]
                if rule["prefixes"]
                else []
            )
            turn = (matched or [t for t in turns if "reply" in t][-1:] or [{}])[0]
            evidence.append(
                {
                    "scenario_id": row["scenario_id"],
                    "repetition": row.get("repetition", 0),
                    "session_number": turn.get("session_number"),
                    "step": turn.get("step"),
                    "input": turn.get("input", ""),
                    "reply": turn.get("reply", ""),
                    "issues": turn.get("issues", []),
                }
            )
        state = (saved or {}).get(rule["id"], {})
        plan["actions"].append(
            {
                **{
                    key: rule[key]
                    for key in ("id", "metric", "label", "target", "title", "priority", "reason", "steps")
                },
                "current": value,
                "suggested_script": rule["script"],
                "evidence": evidence[:5],
                "affected_cases": len(rows),
                "status": state.get("status", "draft"),
                "notes": state.get("notes", ""),
                "script": state.get("script", rule["script"]),
                "updated_at": state.get("updated_at"),
            }
        )
    if not plan["actions"]:
        plan["message"] = (
            "Chưa phát hiện KPI có thể lập đề xuất từ dữ liệu hiện có. Chỉ số thiếu mẫu không được coi là đạt."
        )
    return plan


def compare_runs(baseline: dict, candidate: dict) -> dict:
    """Only compare like-for-like later dev runs; never infer an improvement from task status."""
    reasons = []
    for run in (baseline, candidate):
        if run.get("schema_version") != 1 or run.get("split") != "dev" or run.get("status") != "completed":
            reasons.append("Chỉ so sánh hai lượt dev đã hoàn tất.")
            break
    try:
        if baseline["run_id"] == candidate["run_id"] or datetime.fromisoformat(
            candidate["created_at"]
        ) <= datetime.fromisoformat(baseline["created_at"]):
            reasons.append("Chọn lượt đánh giá chạy sau lượt gốc.")
    except (KeyError, TypeError, ValueError):
        reasons.append("Thiếu thời gian hợp lệ để xác định thứ tự lượt đánh giá.")
    for key in ("backend", "seed", "repetitions"):
        if baseline.get(key) is None or baseline.get(key) != candidate.get(key):
            reasons.append(f"Điều kiện chạy khác nhau hoặc bị thiếu: {key}.")
    before_meta, after_meta = baseline.get("metadata", {}), candidate.get("metadata", {})
    for key in ("oracle_hash", "scenario_hash", "memory_oracle_hash"):
        if (not before_meta.get(key) and key != "memory_oracle_hash") or before_meta.get(key) != after_meta.get(key):
            reasons.append(f"Dữ liệu đánh giá khác nhau hoặc bị thiếu: {key}.")
    before_fixture = before_meta.get("server", {}).get("fixture_hash")
    if not before_fixture or before_fixture != after_meta.get("server", {}).get("fixture_hash"):
        reasons.append("Dữ liệu ngân hàng khác nhau hoặc bị thiếu.")
    report: dict[str, Any] = {
        "baseline_id": baseline["run_id"],
        "candidate_id": candidate["run_id"],
        "comparable": not reasons,
        "reasons": reasons,
        "metrics": [],
        "outcome": "not_comparable",
    }
    if reasons:
        return report
    improved, regressed, missing = False, False, False
    comparison_rules = (*RULES, {"metric": "failure_rate", "label": "Tỷ lệ lỗi hành vi"})
    for rule in comparison_rules:
        key = rule["metric"]
        before = metric_value(baseline.get("summary", {}).get("harness", {}).get(key))
        after = metric_value(candidate.get("summary", {}).get("harness", {}).get(key))
        delta = round(after - before, 6) if before is not None and after is not None else None
        better = key in ("conversion_rate", "handoff_completeness")
        direction = (
            "missing"
            if delta is None
            else "unchanged"
            if delta == 0
            else ("improved" if (delta > 0) == better else "regressed")
        )
        improved |= direction == "improved"
        regressed |= direction == "regressed"
        missing |= (before is None) != (after is None)
        report["metrics"].append(
            {
                "key": key,
                "label": rule["label"],
                "before": before,
                "after": after,
                "delta": delta,
                "direction": direction,
            }
        )
    # Other quality gates and failures must not be hidden by a conversion increase.
    required_gates = (
        "price_errors_zero",
        "handoff_complete",
        "no_missing_offer",
        "run_complete",
        "provenance_valid",
        "behavior_checks",
    )
    gates = candidate.get("gates", {})
    failed_gates = sorted(
        {key for key in required_gates if gates.get(key) is not True}
        | {key for key, value in gates.items() if value is False}
    )
    report["failed_gates"] = failed_gates
    report["outcome"] = (
        "regressed"
        if regressed
        else "insufficient"
        if missing or failed_gates
        else "improved"
        if improved
        else "unchanged"
    )
    return report
