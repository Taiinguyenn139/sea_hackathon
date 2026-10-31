"""Versioned sales playbooks, isolated evaluation and reviewed practice attempts."""

import hashlib
import json
import re
import secrets
import tempfile
import threading
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, StrictInt, field_validator

from harness.improvements import build_plan, compare_runs
from harness.schemas import Input

ROOT = Path(__file__).resolve().parent.parent
RUBRIC = ["Hiểu nhu cầu", "Chính xác và phù hợp", "Giải thích giá trị", "Xử lý băn khoăn", "Bước tiếp theo"]
LEVELS = [
    "Chưa thể hiện hoặc sai",
    "Có đề cập nhưng thiếu trọng tâm",
    "Đáp ứng một phần, cần bổ sung",
    "Đáp ứng rõ ràng, còn thiếu nhỏ",
    "Đầy đủ, phù hợp bối cảnh và có dẫn chứng",
]


def now():
    return datetime.now(UTC).isoformat()


def uid():
    return secrets.token_hex(12)


def book(db, version):
    row = db.one("SELECT data FROM sales_objects WHERE kind='playbook' AND id=?", (version,))
    if row:
        return json.loads(row["data"])["content"]
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", version):
        raise HTTPException(422, "Phiên bản không hợp lệ.")
    path = ROOT / "harness/playbooks" / (version + ".yaml")
    if not path.is_file():
        raise HTTPException(404, "Không tìm thấy phiên bản kịch bản.")
    return yaml.safe_load(path.read_text())


class Draft(Input):
    run_id: str = Field(max_length=100)
    action_id: str = Field(max_length=100)
    base_version: str = Field(default="v1", max_length=100)
    trigger: Literal["rate_high", "need_spouse", "discover", "quote", "objection", "followup", "apply"]
    text: str = Field(min_length=1, max_length=4000)


class Receipt(Input):
    reviewer: str = Field(min_length=1, max_length=100)
    notes: str = Field(min_length=1, max_length=2000)

    @field_validator("reviewer", "notes")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("Nội dung không được để trống")
        return value.strip()


class Review(Receipt):
    approve: bool


class Profile(Input):
    code: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    name: str = Field(min_length=1, max_length=100)


class Exercise(Input):
    run_id: str = Field(max_length=100)
    action_id: str = Field(max_length=100)
    evidence_index: int = Field(default=0, ge=0, le=4)
    title: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=2000)
    required_terms: list[str] = Field(default_factory=list, max_length=10)


class Submission(Input):
    profile_id: str = Field(max_length=100)
    answer: str = Field(min_length=1, max_length=8000)


class Grade(Receipt):
    scores: list[StrictInt] = Field(min_length=5, max_length=5)
    comments: list[str] = Field(min_length=5, max_length=5)
    safety_resolved: bool = False


class Sales:
    def __init__(self, db, settings, load_run):
        self.db, self.settings, self.load_run = db, settings, load_run
        self.worker_lock = threading.Lock()
        self.stopping = threading.Event()

    def rows(self, kind):
        return [
            json.loads(row["data"])
            for row in self.db.all("SELECT data FROM sales_objects WHERE kind=? ORDER BY rowid DESC", (kind,))
        ]

    def get(self, kind, id):
        row = self.db.one("SELECT data FROM sales_objects WHERE kind=? AND id=?", (kind, id))
        if not row:
            raise HTTPException(404, "Không tìm thấy dữ liệu.")
        return json.loads(row["data"])

    def put(self, kind, value):
        self.db.execute(
            "INSERT INTO sales_objects VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data",
            (kind, value["id"], json.dumps(value, ensure_ascii=False)),
        )
        return value

    def source(self, run_id, action_id):
        run = self.load_run(run_id)
        plan = build_plan(run)
        action = next((a for a in plan["actions"] if a["id"] == action_id), None)
        if not plan["eligible"] or not action:
            raise HTTPException(422, "Cần đề xuất hợp lệ từ lượt dev đã hoàn tất.")
        return run, action

    def draft(self, body):
        run, action = self.source(body.run_id, body.action_id)
        if body.action_id not in ("next_step", "relevant_offer"):
            raise HTTPException(422, "Đề xuất này cần task sửa code/tool/memory, không chỉ thay lời tư vấn.")
        text = body.text.strip()
        if not text or re.search(r"\d|\{fact:|chắc chắn.*duyệt|cam kết.*duyệt", text, re.IGNORECASE):
            raise HTTPException(422, "Kịch bản không được chứa số liệu, placeholder hoặc cam kết phê duyệt.")
        content = book(self.db, body.base_version)
        base_content = json.loads(json.dumps(content))
        content = json.loads(json.dumps(content))
        id = "sales_" + uid()
        content["version"] = id
        card = {"id": id, "text": text, "tools": []}
        if body.trigger in ("rate_high", "need_spouse"):
            content.setdefault("cards", {})[body.trigger] = {"retail": [card]}
        else:
            content.setdefault("stages", {})[body.trigger] = {"retail": [card]}
        return self.put(
            "playbook",
            {
                "id": id,
                "base_version": body.base_version,
                "run_id": run["run_id"],
                "action_id": action["id"],
                "metric": action["metric"],
                "trigger": body.trigger,
                "text": text,
                "content": content,
                "base_content": base_content,
                "hash": hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest(),
                "status": "draft",
                "created_at": now(),
            },
        )

    def queue(self, id):
        value = self.get("playbook", id)
        if value["status"] not in ("draft", "failed", "rejected"):
            raise HTTPException(409, "Phiên bản đã được đánh giá hoặc đang chạy đánh giá.")
        job = self.put("job", {"id": uid(), "version": id, "status": "queued", "created_at": now()})
        value.update(status="evaluating", job_id=job["id"])
        self.put("playbook", value)
        return job

    def review(self, id, body):
        value = self.get("playbook", id)
        if value["status"] != "awaiting_review":
            raise HTTPException(409, "Phiên bản chưa đủ điều kiện duyệt.")
        job = self.get("job", value["job_id"])
        if job.get("hash") != value["hash"] or not job.get("passed"):
            raise HTTPException(409, "Kết quả đánh giá không khớp bản sửa.")
        value.update(
            status="approved" if body.approve else "rejected",
            review={**body.model_dump(), "at": now(), "hash": value["hash"], "job_id": job["id"]},
        )
        return self.put("playbook", value)

    def activate(self, id, body, rollback=False):
        value = None if id == "v1" else self.get("playbook", id)
        if value and (value["status"] != "approved" or value.get("review", {}).get("hash") != value["hash"]):
            raise HTTPException(409, "Chỉ kích hoạt bản đã được duyệt đúng nội dung.")
        history = self.rows("activation")
        if rollback and id != "v1" and not any(row["version"] == id for row in history):
            raise HTTPException(409, "Chỉ quay lại phiên bản đã áp dụng.")
        if id == "v1" and not rollback:
            raise HTTPException(409, "Dùng thao tác quay lại bản gốc.")
        previous = self.db.setting("playbook", "v1")
        if previous == id:
            return {"version": id, "unchanged": True}
        with self.db.transaction():
            self.db.set("playbook", id)
            self.put(
                "activation",
                {
                    "id": uid(),
                    "version": id,
                    "previous": previous,
                    "rollback": rollback,
                    "at": now(),
                    **body.model_dump(),
                },
            )
        return {"version": id}

    def exercise(self, body):
        run, action = self.source(body.run_id, body.action_id)
        if body.evidence_index >= len(action["evidence"]):
            raise HTTPException(422, "Chọn dẫn chứng hội thoại có sẵn.")
        evidence = action["evidence"][body.evidence_index]
        return self.put(
            "exercise",
            {
                "id": uid(),
                **body.model_dump(),
                "status": "draft",
                "context": evidence,
                "steps": action["steps"],
                "rubric": RUBRIC,
                "levels": LEVELS,
                "created_at": now(),
                "checks_context": self.check_context(evidence, run),
            },
        )

    def check_context(self, evidence, run):
        from eval.kpis import SLOT_TERMS
        from eval.oracle import Oracle, content_hash, read_document

        meta = run.get("metadata", {})
        if meta.get("oracle_hash") != content_hash(ROOT / "eval/oracle_fixtures/bank.json") or meta.get(
            "scenario_hash"
        ) != content_hash(ROOT / "simulators/scenarios/dev"):
            return {"allowed": [], "known_terms": [], "available": False}

        path = ROOT / "simulators/scenarios/dev" / (evidence["scenario_id"] + ".json")
        if not path.is_file():
            return {"allowed": [], "known_terms": [], "available": False}
        scenario = read_document(path)
        call = scenario["sessions"][evidence["session_number"] - 1]
        oracle = Oracle(ROOT / "eval/oracle_fixtures/bank.json")
        allowed = oracle.allowed_claims(
            scenario["product"],
            call.get("as_of", "2026-10-31"),
            call.get("channel", "web"),
            scenario.get("customer_claims", []),
        )
        expected = oracle.expected_values(
            scenario["product"],
            call.get("as_of", "2026-10-31"),
            call.get("channel", "web"),
            scenario.get("loan"),
            card_income=scenario.get("card_income"),
        )
        allowed.update(
            ("VND", v)
            for k, v in expected.items()
            if k.startswith("installment_") or k in ("total_payment", "card_limit_max")
        )
        known = set()
        for ci, session in enumerate(scenario["sessions"], 1):
            if ci > evidence["session_number"]:
                break
            for ti, step in enumerate(session["steps"]):
                if ci == evidence["session_number"] and ti > evidence["step"]:
                    break
                known.update(step.get("reveals", []))
        return {
            "available": True,
            "allowed": [[u, str(v)] for u, v in allowed],
            "known_terms": [t for k in known for t in SLOT_TERMS.get(k, ())],
        }

    def submit(self, id, body):
        exercise = self.get("exercise", id)
        self.get("profile", body.profile_id)
        if exercise["status"] != "published" or not body.answer.strip():
            raise HTTPException(422, "Bài chưa phát hành hoặc câu trả lời trống.")
        from eval.oracle import numeric_claims

        claims = numeric_claims(body.answer)
        context = exercise.get("checks_context", {})
        from decimal import Decimal

        allowed = {(unit, Decimal(value)) for unit, value in context.get("allowed", [])}
        unsupported = [
            [unit, str(value)] for unit, value in claims if context.get("available") and (unit, value) not in allowed
        ]
        repeated = [
            term for term in context.get("known_terms", []) if "?" in body.answer and term in body.answer.casefold()
        ]
        missing = [term for term in exercise["required_terms"] if term.casefold() not in body.answer.casefold()]
        return self.put(
            "attempt",
            {
                "id": uid(),
                "exercise_id": id,
                **body.model_dump(),
                "created_at": now(),
                "status": "submitted",
                "rubric": RUBRIC,
                "checks": {
                    "numeric_review_required": bool(claims),
                    "unsupported_numeric_claims": unsupported,
                    "oracle_available": context.get("available", False),
                    "missing_terms": missing,
                    "repeated_question_signals": repeated,
                    "message": "Checklist hỗ trợ người chấm; không tự kết luận đúng/sai ngữ nghĩa.",
                },
            },
        )

    def grade(self, id, body):
        attempt = self.get("attempt", id)
        if attempt["status"] != "submitted":
            raise HTTPException(409, "Lần nộp đã chấm; nộp lại để giữ lịch sử.")
        if any(isinstance(score, bool) or not 0 <= score <= 4 for score in body.scores) or any(
            not c.strip() for c in body.comments
        ):
            raise HTTPException(422, "Mỗi tiêu chí cần điểm từ 0–4 và nhận xét có dẫn chứng.")
        total = sum(body.scores) * 5
        attempt.update(
            status="graded",
            grade={
                **body.model_dump(),
                "total": total,
                "at": now(),
                "passed": total >= 75
                and body.safety_resolved
                and not attempt["checks"].get("unsupported_numeric_claims"),
            },
        )
        return self.put("attempt", attempt)

    def run_next(self):
        if not self.worker_lock.acquire(blocking=False):
            return
        try:
            with self.db.lock:
                queued = [j for j in reversed(self.rows("job")) if j["status"] == "queued"]
                if not queued:
                    return
                job = queued[0]
                job.update(status="running", started_at=now())
                self.put("job", job)
                version = self.get("playbook", job["version"])
            try:
                baseline, candidate = self.evaluate(version)
                report = compare_runs(baseline, candidate)
                target = next(row for row in report["metrics"] if row["key"] == version["metric"])
                passed = report["outcome"] == "improved" and target["direction"] == "improved"
                job.update(
                    status="completed",
                    hash=version["hash"],
                    baseline=baseline,
                    candidate=candidate,
                    comparison=report,
                    passed=passed,
                )
                version["status"] = "awaiting_review" if passed else "failed"
            except Exception:  # noqa: BLE001 - job boundary records failure without provider secrets
                # Provider exceptions may contain sensitive request details.
                job.update(
                    status="failed",
                    passed=False,
                    error="Đánh giá bị lỗi hoặc dữ liệu gốc không còn khớp. Kiểm tra cấu hình và chạy lại.",
                )
                version["status"] = "failed"
            with self.db.lock:
                job["finished_at"] = now()
                self.put("job", job)
                self.put("playbook", version)
        finally:
            self.worker_lock.release()

    def evaluate(self, version):
        from fastapi.testclient import TestClient

        from bank_tools.service import fixture_hash
        from eval.oracle import Oracle, content_hash
        from eval.runner import Api, load_memory_contracts, load_scenarios, run_case, summarize
        from harness.api import create_app

        baseline = self.load_run(version["run_id"])
        oracle = Oracle(ROOT / "eval/oracle_fixtures/bank.json")
        scenarios = load_scenarios(ROOT / "simulators/scenarios/dev")
        meta = baseline["metadata"]
        if (
            meta["oracle_hash"] != oracle.hash
            or meta["scenario_hash"] != content_hash(ROOT / "simulators/scenarios/dev")
            or meta["server"]["fixture_hash"] != fixture_hash()
            or baseline["backend"] != ("llm" if self.settings.llm_backend == "openai" else "rules")
            or meta["server"].get("model")
            != (self.settings.openai_model if self.settings.llm_backend == "openai" else None)
            or meta["server"]["playbook"] != version["base_version"]
        ):
            raise ValueError("Baseline conditions changed")
        memory_dir = ROOT / "eval/oracle_fixtures/memory"
        contracts = load_memory_contracts(memory_dir, scenarios) if meta.get("memory_oracle_hash") else {}
        if contracts and content_hash(memory_dir) != meta["memory_oracle_hash"]:
            raise ValueError("Memory oracle changed")
        # TestClient dispatches directly into a separate app: no real server, external
        # inbox, production connection or lifespan worker is used in this sandbox.
        with tempfile.TemporaryDirectory(prefix="sales-eval-") as folder:
            sandbox = create_app(
                replace(
                    self.settings,
                    db_path=str(Path(folder) / "eval.sqlite3"),
                    results_dir=Path(folder) / "results",
                    admin_token=secrets.token_urlsafe(32),
                    telegram_token="",
                    telegram_secret="",
                    release_gate="",
                    environment="development",
                    sales_sandbox=True,
                    ai_specialist_enabled=False,  # Existing playbook oracle evaluates the original human handoff.
                )
            )
            target_db = sandbox.state.harness.db
            for value in self.rows("playbook"):
                target_db.execute("INSERT INTO sales_objects VALUES('playbook',?,?)", (value["id"], json.dumps(value)))
            client = TestClient(sandbox)
            self_outer = self

            class LocalApi(Api):
                def call(self, path, data=None, token=None):
                    if self_outer.stopping.is_set():
                        raise RuntimeError("Evaluation interrupted")
                    headers = {"Authorization": "Bearer " + (token or self.admin_token)}
                    response = client.request("GET" if data is None else "POST", path, json=data, headers=headers)
                    if response.status_code >= 400:
                        raise RuntimeError("Sandbox request failed")
                    return response.json()

            api = LocalApi("http://localhost", sandbox.state.harness.settings.admin_token)
            try:
                results = [
                    run_case(
                        api,
                        scenario,
                        mode,
                        repetition,
                        baseline["seed"] + repetition,
                        oracle,
                        version["id"],
                        contracts.get(scenario["scenario_id"]),
                    )
                    for scenario in scenarios
                    for repetition in range(baseline["repetitions"])
                    for mode in ("no_memory", "full_history", "harness")
                ]
                status, summary, counters = summarize(results, len(scenarios) * baseline["repetitions"] * 3)
                c = counters["harness"]
                candidate = {
                    **baseline,
                    "run_id": "candidate_" + uid(),
                    "created_at": now(),
                    "status": status,
                    "summary": summary,
                    "counters": counters,
                    "results": results,
                    "metadata": {**meta, "server": {**meta["server"], "playbook": version["id"]}},
                    "gates": {
                        "run_complete": status == "completed",
                        "price_errors_zero": c["price_errors"] == 0 and c["numeric_claims"] > 0,
                        "handoff_complete": c["handoff_required"] > 0 and c["handoff_present"] == c["handoff_required"],
                        "no_missing_offer": c["expected_offers"] > 0 and c["missing_offers"] == 0,
                        "provenance_valid": c["traced_replies"] > 0 and c["provenance_errors"] == 0,
                        "behavior_checks": c["failures"] == 0,
                    },
                }
                return baseline, candidate
            finally:
                client.close()
                sandbox.state.harness.engine.cp_conn.close()
                target_db.conn.close()


def router(sales, staff):
    routes = APIRouter(prefix="/sales", dependencies=[Depends(staff)])

    @routes.get("/overview")
    def overview():
        return {
            "active": sales.db.setting("playbook", "v1"),
            "playbooks": sales.rows("playbook"),
            "jobs": [{k: v for k, v in row.items() if k not in ("baseline", "candidate")} for row in sales.rows("job")],
            "activations": sales.rows("activation"),
            "profiles": sales.rows("profile"),
            "exercises": sales.rows("exercise"),
            "attempts": sales.rows("attempt"),
            "rubric": RUBRIC,
            "levels": LEVELS,
        }

    @routes.post("/playbooks")
    def draft(body: Draft):
        with sales.db.lock:
            return sales.draft(body)

    @routes.post("/playbooks/{id}/evaluate")
    def evaluate(id: str):
        with sales.db.lock:
            return sales.queue(id)

    @routes.post("/playbooks/{id}/review")
    def review(id: str, body: Review):
        with sales.db.lock:
            return sales.review(id, body)

    @routes.post("/playbooks/{id}/activate")
    def activate(id: str, body: Receipt):
        with sales.db.lock:
            return sales.activate(id, body)

    @routes.post("/playbooks/{id}/rollback")
    def rollback(id: str, body: Receipt):
        with sales.db.lock:
            return sales.activate(id, body, rollback=True)

    @routes.post("/practice/profiles")
    def profile(body: Profile):
        with sales.db.lock:
            if any(p["code"] == body.code for p in sales.rows("profile")):
                raise HTTPException(409, "Mã hồ sơ đã tồn tại.")
            return sales.put("profile", {"id": uid(), **body.model_dump()})

    @routes.post("/practice/exercises")
    def exercise(body: Exercise):
        return sales.exercise(body)

    @routes.post("/practice/exercises/{id}/publish")
    def publish(id: str, body: Receipt):
        with sales.db.lock:
            value = sales.get("exercise", id)
            if value["status"] != "draft":
                raise HTTPException(409, "Bài đã phát hành.")
            value.update(status="published", review={**body.model_dump(), "at": now()})
            return sales.put("exercise", value)

    @routes.post("/practice/exercises/{id}/attempts")
    def submit(id: str, body: Submission):
        return sales.submit(id, body)

    @routes.post("/practice/attempts/{id}/grade")
    def grade(id: str, body: Grade):
        with sales.db.lock:
            return sales.grade(id, body)

    return routes
