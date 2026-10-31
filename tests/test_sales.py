import copy
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from bank_tools.service import fixture_hash
from eval.oracle import content_hash
from harness.api import create_app
from harness.config import Settings
from harness.sales import Draft, Exercise, Grade, Profile, Receipt, Review, Submission
from tests.test_improvements import candidate_run, sample_run


@pytest.fixture
def setup(tmp_path):
    run = sample_run()
    results = tmp_path / "results"
    results.mkdir()
    (results / "index.json").write_text(json.dumps({"schema_version": 1, "runs": [run]}))
    (results / "baseline.json").write_text(json.dumps(run))
    app = create_app(
        Settings(
            db_path=str(tmp_path / "db.sqlite3"),
            results_dir=results,
            admin_token="admin-test",
            staff_password="staff-test",
            llm_backend="rules",
        )
    )
    return app, app.state.sales


def draft(sales):
    return sales.draft(
        Draft(
            run_id="baseline",
            action_id="next_step",
            trigger="quote",
            text="Anh/chị còn băn khoăn gì để em giải thích thêm?",
        )
    )


def test_approval_activation_rollback_and_session_pinning(setup):
    app, sales = setup
    value = draft(sales)
    receipt = Receipt(reviewer="Lead", notes="Đã đối chiếu")
    with pytest.raises(HTTPException):
        sales.activate(value["id"], receipt)
    queued = sales.queue(value["id"])
    sales.evaluate = lambda version: (sample_run(), candidate_run())
    sales.run_next()
    assert sales.get("job", queued["id"])["passed"]
    assert sales.get("playbook", value["id"])["status"] == "awaiting_review"
    sales.review(value["id"], Review(**receipt.model_dump(), approve=True))
    old = app.state.harness.new_session("hoa")
    sales.activate(value["id"], receipt)
    new = app.state.harness.new_session("hoa")
    db = sales.db
    assert db.one("SELECT version FROM session_playbooks WHERE session_id=?", (old["session_id"],))["version"] == "v1"
    assert (
        db.one("SELECT version FROM session_playbooks WHERE session_id=?", (new["session_id"],))["version"]
        == value["id"]
    )
    assert sales.activate(value["id"], receipt)["unchanged"]
    assert len(sales.rows("activation")) == 1
    sales.activate("v1", receipt, rollback=True)
    assert db.setting("playbook") == "v1"
    assert len(sales.rows("activation")) == 2
    with pytest.raises(HTTPException):
        sales.review(value["id"], Review(**receipt.model_dump(), approve=True))


def test_no_improvement_or_errors_cannot_be_approved(setup):
    _, sales = setup
    value = draft(sales)
    sales.queue(value["id"])
    candidate = candidate_run()
    candidate["summary"] = copy.deepcopy(sample_run()["summary"])
    sales.evaluate = lambda _: (sample_run(), candidate)
    sales.run_next()
    assert sales.get("playbook", value["id"])["status"] == "failed"
    with pytest.raises(HTTPException):
        sales.review(value["id"], Review(reviewer="Lead", notes="Cannot", approve=True))
    sales.queue(value["id"])

    def fail(_):
        raise RuntimeError("provider secret must not escape")

    sales.evaluate = fail
    sales.run_next()
    assert all("provider secret" not in json.dumps(j) for j in sales.rows("job"))


def test_practice_publish_submit_grade_and_history(setup):
    _, sales = setup
    p = sales.put("profile", {"id": "p", **Profile(code="staff01", name="Hoa").model_dump()})
    ex = sales.exercise(
        Exercise(
            run_id="baseline",
            action_id="relevant_offer",
            title="Tư vấn phù hợp",
            question="Hãy đề xuất bước tiếp theo",
            required_terms=["điều kiện"],
        )
    )
    with pytest.raises(HTTPException):
        sales.submit(ex["id"], Submission(profile_id=p["id"], answer="Câu trả lời"))
    ex["status"] = "published"
    sales.put("exercise", ex)
    a = sales.submit(ex["id"], Submission(profile_id="p", answer="Em sẽ giải thích điều kiện, phí 99 triệu đồng."))
    assert a["checks"]["numeric_review_required"]
    assert not a["checks"]["missing_terms"]
    graded = sales.grade(
        a["id"],
        Grade(
            reviewer="Lead",
            notes="Đã kiểm tra",
            scores=[4] * 5,
            comments=["Đủ ý và có dẫn chứng"] * 5,
            safety_resolved=False,
        ),
    )
    assert graded["grade"]["total"] == 100
    assert not graded["grade"]["passed"]
    with pytest.raises(HTTPException):
        sales.grade(a["id"], Grade(reviewer="Lead", notes="Lại", scores=[4] * 5, comments=["Đủ"] * 5))
    second = sales.submit(ex["id"], Submission(profile_id="p", answer="Em sẽ kiểm tra điều kiện."))
    sales.grade(
        second["id"],
        Grade(
            reviewer="Lead",
            notes="Đã kiểm tra",
            scores=[3, 4, 3, 4, 3],
            comments=["Đủ ý và có dẫn chứng"] * 5,
            safety_resolved=True,
        ),
    )
    assert len(sales.rows("attempt")) == 2
    assert sales.get("attempt", second["id"])["grade"]["passed"]


def test_api_auth_csrf_recovery_and_validation(setup):
    app, sales = setup
    value = draft(sales)
    job = sales.queue(value["id"])
    job["status"] = "running"
    sales.put("job", job)
    with TestClient(app) as client:
        assert client.get("/sales/overview").status_code == 401
        token = client.post("/staff/login", json={"password": "staff-test"}).json()["csrf_token"]
        assert sales.get("job", job["id"])["status"] == "failed"
        assert client.post("/sales/practice/profiles", json={"code": "a", "name": "A"}).status_code == 403
        headers = {"X-CSRF-Token": token}
        assert (
            client.post("/sales/practice/profiles", headers=headers, json={"code": "a", "name": "A"}).status_code == 200
        )
        assert (
            client.post("/sales/practice/profiles", headers=headers, json={"code": "a", "name": "A"}).status_code == 409
        )
        body = {"run_id": "baseline", "action_id": "next_step", "trigger": "quote", "text": "Lãi suất 9%"}
        assert client.post("/sales/playbooks", headers=headers, json=body).status_code == 422
        assert (
            client.post(
                "/admin/playbook", headers={"Authorization": "Bearer admin-test"}, json={"version": value["id"]}
            ).status_code
            == 409
        )
        assert client.get("/sales/overview").json()["profiles"]


def test_real_sandbox_evaluation_preserves_live_database(tmp_path):
    root = Path(__file__).resolve().parents[1]
    results = tmp_path / "results"
    results.mkdir()
    source = root / "eval/results/run-20261031-043822-6aaaff.json"
    current = json.loads(source.read_text())
    current["metadata"]["oracle_hash"] = content_hash(root / "eval/oracle_fixtures/bank.json")
    current["metadata"]["scenario_hash"] = content_hash(root / "simulators/scenarios/dev")
    current["metadata"]["memory_oracle_hash"] = content_hash(root / "eval/oracle_fixtures/memory")
    current["metadata"]["server"]["fixture_hash"] = fixture_hash()
    (results / source.name).write_text(json.dumps(current))
    (results / "index.json").write_text(json.dumps({"schema_version": 1, "runs": [current]}))
    app = create_app(
        Settings(db_path=str(tmp_path / "live.sqlite3"), results_dir=results, admin_token="test", llm_backend="rules")
    )
    sales = app.state.sales
    run = json.loads((results / "run-20261031-043822-6aaaff.json").read_text())
    value = sales.draft(
        Draft(run_id=run["run_id"], action_id="next_step", trigger="quote", text="Anh/chị muốn trao đổi thêm không?")
    )
    session = app.state.harness.new_session("hoa")
    before = sales.db.all("SELECT * FROM sessions")
    baseline, candidate = sales.evaluate(value)
    assert candidate["status"] == "completed"
    assert candidate["results"]
    assert baseline["run_id"] == run["run_id"]
    assert sales.db.all("SELECT * FROM sessions") == before
    assert app.state.harness.session(session["session_id"])
    assert sales.db.setting("playbook") == "v1"


def test_stage_fallback_and_objection_priority_respect_guards(setup):
    app, sales = setup
    version = sales.draft(
        Draft(
            run_id="baseline", action_id="next_step", trigger="discover", text="Em sẽ tiếp nối thông tin đã trao đổi."
        )
    )
    sales.db.set("playbook", version["id"])
    sid = app.state.harness.new_session("hoa")["session_id"]
    engine = app.state.harness.engine
    state = {
        "session": {"id": sid, "verified": True},
        "plan": {"stage": "discover", "objection": None, "missing": ["income"]},
        "tool_error": False,
        "unsuitable": False,
    }
    state.update(engine.select_cards(state))
    assert state["cards"][0]["text"] == version["text"]
    assert version["text"] in engine.rules_draft(state)
    state["tool_error"] = True
    assert version["text"] not in engine.rules_draft(state)
    state["plan"]["objection"] = "need_spouse"
    assert engine.select_cards(state)["cards"][0]["id"] == "family-discussion"


def test_detected_wrong_numbers_cannot_be_marked_passed(setup):
    _, sales = setup
    sales.put("profile", {"id": "p", "code": "p", "name": "P"})
    exercise = sales.exercise(Exercise(run_id="baseline", action_id="relevant_offer", title="Test", question="Test"))
    exercise.update(status="published", checks_context={"available": True, "allowed": [], "known_terms": []})
    sales.put("exercise", exercise)
    attempt = sales.submit(exercise["id"], Submission(profile_id="p", answer="Lãi suất là 99% một năm."))
    assert attempt["checks"]["unsupported_numeric_claims"]
    result = sales.grade(
        attempt["id"],
        Grade(reviewer="Lead", notes="Rà soát", scores=[4] * 5, comments=["Đủ ý"] * 5, safety_resolved=True),
    )
    assert not result["grade"]["passed"]
