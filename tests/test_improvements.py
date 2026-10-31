import copy
import json

import pytest
from fastapi.testclient import TestClient

from harness.api import create_app
from harness.config import Settings
from harness.improvements import build_plan, compare_runs


def sample_run():
    return {
        "schema_version": 1,
        "run_id": "baseline",
        "created_at": "2026-10-31T03:00:00+00:00",
        "status": "completed",
        "split": "dev",
        "backend": "rules",
        "seed": 42,
        "repetitions": 1,
        "summary": {
            "harness": {
                "price_error_rate": 0,
                "provenance_error_rate": 0,
                "missing_offer_rate": 0.5,
                "handoff_completeness": 1,
                "reask_rate": None,
                "conversion_rate": 0.5,
                "failure_rate": 0,
            }
        },
        "metadata": {
            "oracle_hash": "oracle",
            "scenario_hash": "scenarios",
            "memory_oracle_hash": None,
            "server": {"fixture_hash": "fixtures"},
        },
        "gates": {
            key: True
            for key in (
                "price_errors_zero",
                "handoff_complete",
                "no_missing_offer",
                "run_complete",
                "provenance_valid",
                "behavior_checks",
            )
        },
        "results": [
            {
                "scenario_id": "needs-offer",
                "mode": "harness",
                "repetition": 0,
                "status": "completed",
                "counters": {"missing_offers": 1, "converted": 0, "scenarios": 1},
                "turns": [
                    {
                        "session_number": 1,
                        "step": 0,
                        "input": "Tôi muốn xem phương án.",
                        "reply": "Em đã ghi nhận.",
                        "issues": ["missing_offer"],
                        "guard_events": [],
                    }
                ],
            }
        ],
    }


def candidate_run():
    run = sample_run()
    run.update(run_id="candidate", created_at="2026-10-31T04:00:00+00:00")
    run["summary"]["harness"].update(conversion_rate=1, missing_offer_rate=0)
    return run


def test_plan_uses_harness_evidence_and_does_not_invent_missing_metrics():
    run = sample_run()
    other = copy.deepcopy(run["results"][0])
    other.update(scenario_id="control-only", mode="no_memory")
    run["results"].append(other)
    actions = {action["id"]: action for action in build_plan(run)["actions"]}
    assert set(actions) == {"relevant_offer", "next_step"}
    assert actions["relevant_offer"]["affected_cases"] == 1
    assert actions["relevant_offer"]["evidence"][0]["scenario_id"] == "needs-offer"
    assert actions["relevant_offer"]["evidence"][0]["reply"] == "Em đã ghi nhận."
    assert actions["next_step"]["priority"] == "experiment"
    assert actions["next_step"]["target"] is None


@pytest.mark.parametrize("reason", ["unsuitable", "public_tools_only", "tool_failed"])
def test_correct_guards_are_not_treated_as_sales_opportunities(reason):
    run = sample_run()
    run["results"][0]["turns"][0]["guard_events"] = [{"reason": reason}]
    action = next(action for action in build_plan(run)["actions"] if action["id"] == "next_step")
    assert action["evidence"] == []
    assert action["affected_cases"] == 0


@pytest.mark.parametrize("change", [{"split": "heldout"}, {"status": "incomplete"}, {"schema_version": 2}])
def test_plan_does_not_tune_incomplete_or_heldout_runs(change):
    run = sample_run()
    run.update(change)
    assert build_plan(run)["eligible"] is False
    assert build_plan(run)["actions"] == []


def test_all_metrics_missing_or_good_do_not_create_actions():
    run = candidate_run()
    assert build_plan(run)["actions"] == []
    run["summary"]["harness"] = {"conversion_rate": None, "price_error_rate": float("nan")}
    assert build_plan(run)["actions"] == []


def test_plan_prioritizes_accuracy_and_links_to_the_flagged_turn():
    run = sample_run()
    run["summary"]["harness"].update(
        price_error_rate=0.1, provenance_error_rate=0.2, reask_rate=0.5, handoff_completeness=0.5
    )
    row = run["results"][0]
    row["counters"].update(
        price_errors=1, provenance_errors=1, redundant_questions=1, handoff_present=1, handoff_required=2
    )
    row["turns"].insert(
        0,
        {
            "session_number": 1,
            "step": 0,
            "input": "Cho tôi xem điều kiện.",
            "reply": "Câu trả lời cần kiểm tra.",
            "issues": ["unexpected_numeric_claim:99:%", "untraced_numeric_claim", "reasked:income"],
            "guard_events": [],
        },
    )
    actions = build_plan(run)["actions"]
    assert [action["id"] for action in actions] == [
        "quote_accuracy",
        "source_accuracy",
        "relevant_offer",
        "remember_needs",
        "handoff_context",
        "next_step",
    ]
    assert actions[0]["priority"] == actions[1]["priority"] == "high"
    assert actions[0]["evidence"][0]["reply"] == "Câu trả lời cần kiểm tra."
    assert actions[1]["evidence"][0]["reply"] == "Câu trả lời cần kiểm tra."
    assert actions[3]["evidence"][0]["reply"] == "Câu trả lời cần kiểm tra."
    assert actions[4]["target"] == 1


def test_comparison_distinguishes_improvement_regression_and_missing_denominators():
    baseline, candidate = sample_run(), candidate_run()
    report = compare_runs(baseline, candidate)
    assert report["outcome"] == "improved"
    assert next(row for row in report["metrics"] if row["key"] == "conversion_rate")["delta"] == 0.5
    candidate["summary"]["harness"]["price_error_rate"] = 0.1
    assert compare_runs(baseline, candidate)["outcome"] == "regressed"
    candidate["summary"]["harness"]["price_error_rate"] = None
    assert compare_runs(baseline, candidate)["outcome"] == "insufficient"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda run: run.update(seed=7),
        lambda run: run.update(split="heldout"),
        lambda run: run.update(status="incomplete"),
        lambda run: run.update(created_at="2026-10-31T02:00:00+00:00"),
        lambda run: run["metadata"].update(scenario_hash="other"),
        lambda run: run["metadata"].update(oracle_hash="other"),
        lambda run: run["metadata"].update(memory_oracle_hash="other"),
        lambda run: run["metadata"]["server"].update(fixture_hash="other"),
    ],
)
def test_comparison_rejects_unmatched_conditions(mutation):
    candidate = candidate_run()
    mutation(candidate)
    report = compare_runs(sample_run(), candidate)
    assert report["comparable"] is False
    assert report["metrics"] == []


def test_comparison_requires_quality_gates_and_detects_behavior_regressions():
    candidate = candidate_run()
    candidate["gates"] = {}
    assert compare_runs(sample_run(), candidate)["outcome"] == "insufficient"
    candidate = candidate_run()
    candidate["summary"]["harness"]["failure_rate"] = 0.1
    assert compare_runs(sample_run(), candidate)["outcome"] == "regressed"


def test_improvement_api_auth_validation_and_durable_state(tmp_path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    baseline, candidate = sample_run(), candidate_run()
    heldout = {**sample_run(), "run_id": "heldout", "split": "heldout"}
    runs = [baseline, candidate, heldout]
    (results_dir / "index.json").write_text(json.dumps({"schema_version": 1, "runs": runs}))
    for run in runs:
        (results_dir / f"{run['run_id']}.json").write_text(json.dumps(run))
    settings = Settings(
        db_path=str(tmp_path / "db.sqlite3"),
        results_dir=results_dir,
        admin_token="admin-test",
        staff_password="staff-test",
        llm_backend="rules",
    )
    url = "/eval/runs/baseline/improvements"
    edit_url = url + "/relevant_offer"
    body = {"status": "ready_for_evaluation", "script": "Bản tư vấn đã sửa.", "notes": "Đã cập nhật playbook."}
    with TestClient(create_app(settings)) as client:
        assert client.get(url).status_code == 401
        assert client.patch(edit_url, json=body).status_code == 401
        login = client.post("/staff/login", json={"password": "staff-test"}).json()
        assert client.patch(edit_url, json=body).status_code == 403
        headers = {"X-CSRF-Token": login["csrf_token"]}
        saved = client.patch(edit_url, headers=headers, json=body)
        assert saved.status_code == 200
        action = saved.json()["actions"][0]
        assert action["script"] == body["script"]
        assert action["updated_at"]
        assert client.patch(edit_url, headers=headers, json={**body, "status": "applied"}).status_code == 422
        assert client.patch(edit_url, headers=headers, json={**body, "script": "   "}).status_code == 422
        assert client.patch(url + "/unknown", headers=headers, json=body).status_code == 404
        assert client.patch("/eval/runs/heldout/improvements/next_step", headers=headers, json=body).status_code == 409
        assert client.get("/eval/runs/baseline/comparison?candidate_id=candidate").json()["outcome"] == "improved"
        assert client.get("/eval/runs/baseline/comparison?candidate_id=missing").status_code == 404
    with TestClient(create_app(settings)) as reopened:
        reopened.post("/staff/login", json={"password": "staff-test"})
        action = reopened.get(url).json()["actions"][0]
        assert action["status"] == body["status"]
        assert action["notes"] == body["notes"]
        assert action["script"] == body["script"]
