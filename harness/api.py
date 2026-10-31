"""HTTP boundary: customer capabilities, staff cookies and admin bearer are separate."""

import asyncio
import json
import re
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager, suppress
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import Field

from bank_tools.service import FIXTURES
from harness.config import Settings
from harness.db import Database
from harness.identity import get_profile
from harness.schemas import (
    AdminSessionInput,
    CompleteInput,
    HandoffInput,
    Input,
    LoginInput,
    MessageInput,
    SessionInput,
    VerifyInput,
)
from harness.service import Harness, token_hash


class ClockInput(Input):
    as_of: date


class FaultInput(Input):
    tool: str
    mode: str
    count: int = Field(ge=0, le=100)


class PlaybookInput(Input):
    version: str = Field(max_length=100)


class ReconcileInput(Input):
    status: str
    provider_message_id: str | None = None
    allow_retry: bool = False


class LinkInput(Input):
    customer_id: str
    external_id: str = Field(min_length=1, max_length=100)


def create_app(settings=None):
    settings = settings or Settings()
    db = Database(settings.db_path)
    harness = Harness(db, settings)

    @asynccontextmanager
    async def lifespan(app):
        from harness.channels.telegram import Dispatcher

        app.state.dispatcher = Dispatcher(harness)
        app.state.worker_error = None
        recovered = False

        def preflight():
            # Candidate-local recovery is reversible by the release snapshot. It must
            # never invoke an LLM/provider or consume the external inbox before promotion.
            for row in db.all("SELECT customer_id FROM customer_sequences"):
                harness.engine.memory.flush(row["customer_id"], allow_llm=False)
            for row in db.all("SELECT * FROM handoffs WHERE status IN ('pending', 'completing')"):
                session = db.one("SELECT * FROM sessions WHERE id=?", (row["session_id"],))
                summary = json.loads(row["summary"])
                checkpoint = harness.engine.graph.get_state({"configurable": {"thread_id": row["session_id"]}})
                if not session or session["status"] != "waiting_human":
                    raise RuntimeError("Handoff session state inconsistent")
                if summary.get("handoff_id") != row["id"] or checkpoint.values.get("handoff_id") != row["id"]:
                    raise RuntimeError("Handoff checkpoint missing or mismatched")
                if checkpoint.next not in (("wait_human",), ()):
                    raise RuntimeError("Handoff checkpoint cannot be recovered locally")
                if row["status"] == "pending":
                    if checkpoint.next != ("wait_human",):
                        raise RuntimeError("Pending handoff has no waiting checkpoint")
                    continue
                body = CompleteInput.model_validate(json.loads(row["receipt"])["pending_request"]).model_dump()
                if not checkpoint.next and checkpoint.values.get("completion") != body:
                    raise RuntimeError("Completed checkpoint does not match completion receipt")
                harness.engine.complete(row, body, recover=True)

        def tick():
            nonlocal recovered
            if not recovered:
                preflight()
                recovered = True
            if settings.release_ready:
                app.state.dispatcher.replay()

        recovery_errors = (RuntimeError, sqlite3.Error, ValueError, KeyError, TypeError)
        try:
            tick()
        except recovery_errors:
            app.state.worker_error = "startup_preflight_failed"

        async def worker():
            while True:
                await asyncio.sleep(2)
                try:
                    await asyncio.to_thread(tick)
                    app.state.worker_error = None
                except recovery_errors:
                    app.state.worker_error = "background_processing_failed"

        worker_task = asyncio.create_task(worker())
        try:
            yield
        finally:
            worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await worker_task

    app = FastAPI(title="Memory Harness", version="0.1.0", lifespan=lifespan)
    app.state.harness = harness
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
    )

    @app.middleware("http")
    async def size_limit(request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS") and not settings.release_ready:
            return JSONResponse({"detail": "Release awaiting promotion"}, status_code=503)
        try:
            if int(request.headers.get("content-length", "0")) > 65536:
                return JSONResponse({"detail": "Request too large"}, status_code=413)
        except ValueError:
            return JSONResponse({"detail": "Invalid content length"}, status_code=400)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    def bearer(request):
        auth = request.headers.get("Authorization", "")
        return auth[7:] if auth.startswith("Bearer ") else ""

    def admin(request: Request):
        supplied = bearer(request)
        if not settings.admin_token or not secrets.compare_digest(supplied, settings.admin_token):
            raise HTTPException(403, "Admin credential required")
        return True

    def staff(request: Request):
        supplied = bearer(request)
        if settings.admin_token and secrets.compare_digest(supplied, settings.admin_token):
            return {"csrf": "", "admin": True}
        cookie = request.cookies.get("staff_session", "")
        row = db.one("SELECT * FROM staff_sessions WHERE token_hash=? AND expires>?", (token_hash(cookie), time.time()))
        if not row:
            raise HTTPException(401, "Staff login required")
        if request.method != "GET" and not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), row["csrf"]):
            raise HTTPException(403, "CSRF verification failed")
        return row

    def owner(sid, request):
        session = harness.session(sid)
        if not secrets.compare_digest(token_hash(bearer(request)), session["token_hash"]):
            raise HTTPException(403, "Session capability required")
        return session

    def owner_or_staff(sid, request):
        session = harness.session(sid)
        if secrets.compare_digest(token_hash(bearer(request)), session["token_hash"]):
            return session
        if bearer(request) and bearer(request) != settings.admin_token and not request.cookies.get("staff_session"):
            raise HTTPException(403, "Session capability belongs to another session")
        staff(request)
        return session

    @app.get("/healthz")
    @app.get("/health")
    def health():
        if getattr(app.state, "worker_error", None):
            raise HTTPException(503, "Background worker requires attention")
        return {"status": "ok", "backend": settings.llm_backend, "demo": True, "revision": settings.revision}

    @app.get("/demo/profiles")
    def profiles():
        return {
            "profiles": [
                {
                    "id": p["user_id"],
                    "phone": p["phone"],
                    "name": p["name"],
                    "dob": p["dob"],
                    "runtime_profile": p["runtime_profile"],
                }
                for p in FIXTURES["customers"]
            ],
            "fictional": True,
        }

    @app.post("/sessions")
    def create(body: SessionInput):
        return harness.new_session(body.demo_profile, body.mode)

    @app.get("/sessions/{sid}")
    def get_session(sid: str, request: Request):
        s = owner_or_staff(sid, request)
        h = db.one("SELECT id FROM handoffs WHERE session_id=? AND status!='completed'", (sid,))
        return {
            "session_id": sid,
            "status": s["status"],
            "identity_status": "verified" if s["verified"] else "unverified",
            "customer_id": s["customer_id"] if s["verified"] else None,
            "user_id": get_profile(s["customer_id"])["user_id"] if s["verified"] else None,
            "handoff_id": h["id"] if h else None,
        }

    @app.get("/sessions/{sid}/turns")
    def turns(sid: str, request: Request, offset: int = 0, limit: int = 100):
        owner_or_staff(sid, request)
        rows = db.all(
            "SELECT id,text,reply,created_at FROM turns WHERE session_id=? ORDER BY created_at,id LIMIT ? OFFSET ?",
            (sid, min(max(limit, 1), 100), max(offset, 0)),
        )
        return {"items": rows}

    @app.get("/sessions/{sid}/tools")
    def tool_definitions(sid: str, request: Request):
        session = owner(sid, request)
        return {"tools": harness.engine.bank.definitions(session["customer_id"], sid)}

    @app.post("/sessions/{sid}/verify")
    def verify(sid: str, body: VerifyInput, request: Request):
        return harness.verify(owner(sid, request), body.dob, request.client.host if request.client else "unknown")

    @app.post("/sessions/{sid}/messages")
    def message(sid: str, body: MessageInput, request: Request, tasks: BackgroundTasks):
        owner(sid, request)
        response = harness.message(sid, body.text, body.client_message_id)
        tasks.add_task(harness.engine.memory.flush, harness.session(sid)["customer_id"])
        if hasattr(app.state, "dispatcher"):
            tasks.add_task(app.state.dispatcher.send_pending)
        return response

    @app.post("/sessions/{sid}/handoff")
    def handoff(sid: str, body: HandoffInput, request: Request):
        owner(sid, request)
        return harness.handoff(sid, body.reason)

    @app.post("/sessions/{sid}/end")
    def end(sid: str, request: Request):
        s = owner(sid, request)
        with harness.engine.lock(s["customer_id"]):
            harness.engine.memory.flush(s["customer_id"])
            if s["status"] == "waiting_human":
                raise HTTPException(409, "Complete handoff before ending session")
            db.execute("UPDATE sessions SET status='ended' WHERE id=?", (sid,))
        return {"session_id": sid, "status": "ended"}

    @app.post("/staff/login")
    def login(body: LoginInput, request: Request, response: Response):
        source = "staff:" + (request.client.host if request.client else "unknown")
        if (
            db.one("SELECT count(*) AS n FROM auth_attempts WHERE source=? AND at>?", (source, time.time() - 900))["n"]
            >= 5
        ):
            raise HTTPException(429, "Please try again later")
        if not settings.staff_password or not secrets.compare_digest(body.password, settings.staff_password):
            db.execute("INSERT INTO auth_attempts VALUES(?,?,?)", ("staff", source, time.time()))
            raise HTTPException(401, "Invalid credentials")
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        db.execute("INSERT INTO staff_sessions VALUES(?,?,?)", (token_hash(token), csrf, time.time() + 3600))
        response.set_cookie(
            "staff_session",
            token,
            httponly=True,
            secure=settings.cookie_secure or request.url.scheme == "https",
            samesite="strict",
            max_age=3600,
        )
        return {"csrf_token": csrf}

    @app.get("/staff/me")
    def me(identity: Annotated[dict, Depends(staff)]):
        return {"authenticated": True, "csrf_token": identity["csrf"]}

    @app.post("/staff/logout")
    def logout(request: Request, response: Response, identity: Annotated[dict, Depends(staff)]):
        db.execute(
            "DELETE FROM staff_sessions WHERE token_hash=?", (token_hash(request.cookies.get("staff_session", "")),)
        )
        response.delete_cookie("staff_session")
        return {"status": "logged_out"}

    @app.get("/customers/{customer}/brief", dependencies=[Depends(staff)])
    def brief(customer: str):
        profile = get_profile(customer)
        if not profile:
            raise HTTPException(404, "Customer not found")
        return harness.engine.memory.brief(profile["id"])

    @app.get("/traces/{sid}", dependencies=[Depends(staff)])
    def traces(sid: str):
        harness.session(sid)
        return {
            "items": [
                json.loads(r["data"])
                for r in db.all("SELECT data FROM traces WHERE session_id=? ORDER BY rowid", (sid,))
            ]
        }

    @app.get("/handoffs", dependencies=[Depends(staff)])
    def handoffs():
        return {"items": [harness.handoff_detail(r) for r in db.all("SELECT * FROM handoffs ORDER BY created_at DESC")]}

    @app.get("/handoffs/{hid}", dependencies=[Depends(staff)])
    def handoff_detail(hid: str):
        return harness.handoff_detail(db.one("SELECT * FROM handoffs WHERE id=?", (hid,)))

    @app.post("/handoffs/{hid}/complete", dependencies=[Depends(staff)])
    def complete(hid: str, body: CompleteInput):
        h = db.one("SELECT * FROM handoffs WHERE id=?", (hid,))
        if not h:
            raise HTTPException(404, "Handoff not found")
        return harness.engine.complete(h, body.model_dump())

    @app.get("/callbacks", dependencies=[Depends(staff)])
    def callbacks():
        return {"items": db.all("SELECT * FROM callbacks ORDER BY due_at")}

    @app.post("/callbacks/{cid}/complete", dependencies=[Depends(staff)])
    def callback_complete(cid: str):
        if not db.one("SELECT id FROM callbacks WHERE id=?", (cid,)):
            raise HTTPException(404, "Callback not found")
        db.execute("UPDATE callbacks SET status='completed' WHERE id=?", (cid,))
        return {"id": cid, "status": "completed"}

    def manifest():
        file = settings.results_dir / "index.json"
        if not file.exists():
            return {"schema_version": 1, "runs": []}
        try:
            value = json.loads(file.read_text())
            if value.get("schema_version") != 1 or not isinstance(value.get("runs"), list):
                raise ValueError()
            return value
        except (ValueError, OSError):
            raise HTTPException(503, "Evaluation manifest invalid")

    @app.get("/eval/runs", dependencies=[Depends(staff)])
    def runs():
        return manifest()

    @app.get("/eval/runs/{rid}", dependencies=[Depends(staff)])
    def run_detail(rid: str):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", rid) or rid not in {r["run_id"] for r in manifest()["runs"]}:
            raise HTTPException(404, "Run not found")
        path = settings.results_dir / (rid + ".json")
        if path.is_symlink() or not path.is_file():
            raise HTTPException(404, "Run file not found")
        try:
            return json.loads(path.read_text())
        except (ValueError, OSError):
            raise HTTPException(503, "Run result invalid")

    @app.post("/admin/sessions", dependencies=[Depends(admin)])
    def admin_session(body: AdminSessionInput):
        return harness.new_session(body.demo_profile, body.mode, body.channel, "eval:" + body.demo_profile)

    @app.post("/admin/reset", dependencies=[Depends(admin)])
    def reset():
        return harness.reset()

    @app.post("/admin/clock", dependencies=[Depends(admin)])
    def clock(body: ClockInput):
        db.set("clock", body.as_of.isoformat())
        return {"as_of": body.as_of.isoformat()}

    @app.post("/admin/faults", dependencies=[Depends(admin)])
    def faults(body: FaultInput):
        if body.mode not in ("timeout", "error"):
            raise HTTPException(422, "Unsupported fault mode")
        db.execute("INSERT OR REPLACE INTO faults VALUES(?,?,?)", (body.tool, body.mode, body.count))
        return body.model_dump()

    @app.post("/admin/playbook", dependencies=[Depends(admin)])
    def playbook(body: PlaybookInput):
        if (
            not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", body.version)
            or not (Path("harness/playbooks") / (body.version + ".yaml")).is_file()
        ):
            raise HTTPException(422, "Unknown playbook version")
        db.set("playbook", body.version)
        return {"version": body.version}

    @app.post("/admin/identity-links/telegram", dependencies=[Depends(admin)])
    def identity_link(body: LinkInput):
        profile = get_profile(body.customer_id)
        if not profile:
            raise HTTPException(400, "Unknown demo profile")
        db.execute(
            "INSERT OR REPLACE INTO identity_links VALUES(?,?,?)", ("telegram", body.external_id, profile["id"])
        )
        return {"status": "linked"}

    @app.post("/admin/outbox/{oid}/reconcile", dependencies=[Depends(admin)])
    def reconcile(oid: str, body: ReconcileInput):
        row = db.one("SELECT * FROM outbox WHERE id=?", (oid,))
        if not row:
            raise HTTPException(404, "Outbox intent not found")
        if row["status"] not in ("unknown", "failed"):
            raise HTTPException(409, "Only unknown/failed sends can be reconciled")
        if body.status not in ("sent", "pending", "failed"):
            raise HTTPException(422, "Invalid reconciliation state")
        if body.status == "pending" and not body.allow_retry:
            raise HTTPException(422, "Explicit allow_retry required: provider may have delivered already")
        if body.status == "sent" and not body.provider_message_id:
            raise HTTPException(422, "Provider receipt required")
        db.execute(
            "UPDATE outbox SET status=?,receipt=? WHERE id=?",
            (
                body.status,
                json.dumps({"reconciled": True, "provider_message_id": body.provider_message_id, "at": time.time()}),
                oid,
            ),
        )
        return {"id": oid, "status": body.status}

    @app.get("/admin/state", dependencies=[Depends(admin)])
    def state():
        from bank_tools.service import fixture_hash

        return {
            "backend": settings.llm_backend,
            "model": settings.openai_model if settings.llm_backend != "rules" else None,
            "clock": db.setting("clock"),
            "playbook": db.setting("playbook"),
            "fixture_hash": fixture_hash(),
            "effective_config": {
                "backend": settings.llm_backend,
                "model": settings.openai_model,
                "playbook": db.setting("playbook"),
            },
            "faults": db.all("SELECT * FROM faults"),
            "memory_jobs": db.all("SELECT turn_id,status,error FROM memory_jobs WHERE status!='processed'"),
            "outbox": db.all("SELECT id,status,attempts FROM outbox"),
        }

    @app.post("/webhooks/telegram")
    async def telegram(request: Request, tasks: BackgroundTasks):
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not settings.telegram_secret or not secrets.compare_digest(secret, settings.telegram_secret):
            raise HTTPException(403, "Invalid webhook")
        raw = await request.body()
        if len(raw) > 65536:
            raise HTTPException(413, "Request too large")
        try:
            payload = json.loads(raw)
        except ValueError:
            raise HTTPException(400, "Invalid JSON")
        dispatcher = app.state.dispatcher
        dispatcher.accept(payload)
        tasks.add_task(dispatcher.replay)
        return {"ok": True}

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        root = Path("web/dist").resolve()
        candidate = (root / path).resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            return FileResponse(candidate)
        if path in ("", "chat", "staff", "benchmark") and (root / "index.html").exists():
            return FileResponse(root / "index.html")
        raise HTTPException(404, "Not found")

    return app


app = create_app()
