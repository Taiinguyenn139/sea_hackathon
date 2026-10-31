"""Recover committed native tools and their issuing facts across process loss."""

import json

import pytest
from fastapi.testclient import TestClient

from bank_tools.service import BankTools
from harness.api import create_app
from harness.config import Settings
from harness.db import Database
from harness.service import Harness


def config(tmp_path, **kwargs):
    return Settings(
        db_path=str(tmp_path / "recovery.sqlite3"), admin_token="recovery-admin", staff_password="recovery-staff",
        environment="development", release_gate="", telegram_token="", telegram_secret="", **kwargs,
    )


def owner(tmp_path):
    settings = config(tmp_path, llm_backend="rules")
    harness = Harness(Database(settings.db_path), settings)
    created = harness.new_session("hoa")
    harness.verify(harness.session(created["session_id"]), "0900000001", "test-ip")
    return harness, created["session_id"]


def rate_args():
    return {"product": "home_loan", "amount": 2_000_000_000, "term": 240, "segment": "mass", "as_of": "2026-10-31"}


def source_rate(harness, sid, operation="source"):
    result = harness.engine.bank.call("get_rate", rate_args(), "hoa", sid, idempotency_key=operation)
    assert result["error"] is None
    return result


def test_restart_restores_only_owned_same_day_issued_rate_for_calculation_and_summary(tmp_path):
    harness, sid = owner(tmp_path)
    issued = source_rate(harness, sid)
    standard = next(fact for fact in issued["facts"] if fact["key"] == "standard_rate")
    # A fresh adapter models loss of all native Python instances at restart.
    restored = BankTools(Database(harness.db.path))
    calculated = restored.call("calc_installment", {
        "amount": 2_000_000_000, "term": 240, "rate": standard["value"],
        "rate_fact_id": standard["fact_id"], "as_of": "2026-10-31",
    }, "hoa", sid, idempotency_key="calculate")
    assert calculated["error"] is None
    summary = restored.call("send_summary", {
        "channel": "web", "fact_ids": [standard["fact_id"]], "as_of": "2026-10-31",
    }, "hoa", sid, idempotency_key="summary")
    assert summary["error"] is None and summary["data"]["status"] == "mock_sent"
    replay = restored.call("get_rate", rate_args(), "hoa", sid, idempotency_key="source")
    assert replay == issued
    assert harness.db.one("SELECT count(*) AS n FROM tool_calls WHERE tool='get_rate'")["n"] == 1
    assert not [call for call in restored.clients[(sid, "hoa")].get_tool_calls() if call["tool"] == "get_rate"]


def test_operation_key_cannot_return_a_different_request_or_customer_receipt(tmp_path):
    harness, sid = owner(tmp_path)
    issued = source_rate(harness, sid)
    changed = harness.engine.bank.call("get_rate", {**rate_args(), "amount": 1_000_000_000}, "hoa", sid, idempotency_key="source")
    assert changed["error"].startswith("conflict") and changed["facts"] == []
    other = harness.new_session("minh")
    harness.verify(harness.session(other["session_id"]), "0900000002", "another-ip")
    stolen = harness.engine.bank.call("get_rate", rate_args(), "minh", other["session_id"], idempotency_key="source")
    assert stolen["error"] and stolen["facts"] == []
    assert harness.engine.bank.call("get_rate", rate_args(), "hoa", sid, idempotency_key="source") == issued


def test_cached_private_receipt_still_checks_current_server_permission(tmp_path):
    harness, sid = owner(tmp_path)
    args = {"time": "2026-11-02T09:00:00+07:00", "as_of": "2026-10-31"}
    first = harness.engine.bank.call("schedule_callback", args, "hoa", sid, idempotency_key="callback")
    assert first["error"] is None
    harness.db.execute("UPDATE sessions SET verified=0 WHERE id=?", (sid,))
    denied = BankTools(harness.db).call("schedule_callback", args, "hoa", sid, idempotency_key="callback")
    assert denied["error"] and denied["data"] == {}
    assert len(harness.db.all("SELECT * FROM callbacks")) == 1


def test_callback_receipt_survives_loss_before_loop_ledger_without_native_reexecution(tmp_path):
    harness, sid = owner(tmp_path)
    args = {"time": "2026-11-02T09:00:00+07:00", "as_of": "2026-10-31"}
    first = harness.engine.bank.call("schedule_callback", args, "hoa", sid, idempotency_key="callback-op")
    # No agent_runs acknowledgement was written before the simulated restart.
    assert not harness.db.all("SELECT * FROM agent_runs")
    restored = BankTools(Database(harness.db.path))
    replay = restored.call("schedule_callback", args, "hoa", sid, idempotency_key="callback-op")
    assert replay == first
    assert len(harness.db.all("SELECT * FROM callbacks")) == 1
    assert len(harness.db.all("SELECT * FROM tool_calls WHERE tool='schedule_callback'")) == 1
    assert restored.clients[(sid, "hoa")].get_mock_actions()["callbacks"] == []


def test_action_audit_and_receipt_roll_back_together_on_commit_path_failure(tmp_path, monkeypatch):
    harness, sid = owner(tmp_path)
    bank = harness.engine.bank
    save = bank._save_call

    def crash(*args, **kwargs):
        save(*args, **kwargs)
        raise RuntimeError("lost process before receipt commit")

    monkeypatch.setattr(bank, "_save_call", crash)
    args = {"time": "2026-11-02T09:00:00+07:00", "as_of": "2026-10-31"}
    with pytest.raises(RuntimeError, match="before receipt commit"):
        bank.call("schedule_callback", args, "hoa", sid, idempotency_key="atomic")
    assert not harness.db.all("SELECT * FROM callbacks")
    assert not harness.db.all("SELECT * FROM agent_tool_receipts")
    assert not harness.db.all("SELECT * FROM tool_calls WHERE tool='schedule_callback'")
    monkeypatch.setattr(bank, "_save_call", save)
    assert bank.call("schedule_callback", args, "hoa", sid, idempotency_key="atomic")["error"] is None
    assert len(harness.db.all("SELECT * FROM callbacks")) == 1


def test_other_session_or_date_and_unreceipted_fact_rows_are_not_rehydrated(tmp_path):
    harness, sid = owner(tmp_path)
    issued = source_rate(harness, sid)
    fact = next(fact for fact in issued["facts"] if fact["key"] == "standard_rate")
    other = harness.new_session("hoa")
    harness.verify(harness.session(other["session_id"]), "0900000001", "test-ip")
    for target, as_of in [(other["session_id"], "2026-10-31"), (sid, "2026-11-01")]:
        # The shared customer facts table cannot grant source provenance to a
        # different session or business day.
        args = {"amount": 2_000_000_000, "term": 240, "rate": fact["value"], "rate_fact_id": fact["fact_id"], "as_of": as_of}
        result = BankTools(harness.db).call("calc_installment", args, "hoa", target, idempotency_key="calc-" + target + as_of)
        assert result["error"] and result["facts"] == []


def test_recovery_does_not_restore_a_promotion_for_another_channel(tmp_path):
    harness, sid = owner(tmp_path)
    issued = source_rate(harness, sid)
    standard = next(fact for fact in issued["facts"] if fact["key"] == "standard_rate")
    promotion = harness.engine.bank.call("get_active_promotions", {
        "product": "home_loan", "channel": "telegram", "as_of": "2026-10-31",
    }, "hoa", sid, idempotency_key="other-channel")
    rate = next(fact for fact in promotion["facts"] if fact["key"] == "promo_rate")
    restored = BankTools(Database(harness.db.path))
    calculation = restored.call("calc_installment", {
        "amount": 2_000_000_000, "term": 240, "rate": rate["value"], "rate_fact_id": rate["fact_id"],
        "promo_months": 12, "after_rate": standard["value"], "after_rate_fact_id": standard["fact_id"],
        "as_of": "2026-10-31",
    }, "hoa", sid, idempotency_key="wrong-channel-calculation")
    assert calculation["error"] and calculation["facts"] == []


def test_real_pending_tool_batch_recovers_after_restart_and_calculates_with_original_source(tmp_path, monkeypatch):
    import harness.agent.tool_loop as loop_module
    import harness.input_guard as input_module
    import harness.memory.service as memory_module

    settings = config(tmp_path, llm_backend="openai", openai_key="test-only", openai_model="primary", openai_backup_model="backup")
    monkeypatch.setattr(input_module, "json_completion", lambda *args, **kwargs: {"category": "safe"})
    memory_facts = [{"key": key, "value": value, "unit": unit, "operation": "replace", "source_text": source}
                    for key, value, unit, source in [
                        ("product", "home_loan", "", "vay mua nhà"),
                        ("amount", "2000000000", "đồng", "2 tỷ"),
                        ("term", "240", "tháng", "20 năm"),
                        ("income", "45000000", "đồng", "thu nhập 45 triệu"),
                        ("debt", "0", "đồng", "không có nợ"),
                        ("property_value", "3000000000", "đồng", "giá nhà 3 tỷ"),
                    ]]
    monkeypatch.setattr(memory_module, "json_completion", lambda *args, **kwargs: {"facts": memory_facts})
    stage = 0

    def envelope(*items):
        return {"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": key, "type": "function", "function": {"name": tool, "arguments": json.dumps(arguments)}}
            for key, tool, arguments in items
        ]}}

    def provider(_settings, messages, tools, *, deadline):
        nonlocal stage
        stage += 1
        context = json.loads(messages[0]["content"].split("BACKEND_CONTEXT_JSON:\n", 1)[1])
        if stage == 1:
            return envelope(("identity", "verify_identity", {"phone": "0900000001"}))
        if stage == 2:
            return envelope(
                ("suit", "check_suitability", {"product": "home_loan", "income": 45_000_000, "debt": 0,
                    "amount": 2_000_000_000, "term": 240, "property_value": 3_000_000_000}),
                ("rate", "get_rate", {key: value for key, value in rate_args().items() if key != "as_of"}),
                ("disclosures", "get_disclosures", {"product": "home_loan", "topics": ["rate", "installment"]}),
            )
        rate = next(fact for fact in context["facts"].values() if fact["key"] == "standard_rate")
        if stage == 3:
            return envelope(("calc", "calc_installment", {"amount": 2_000_000_000, "term": 240,
                "rate": rate["value"], "rate_fact_id": rate["id"]}))
        installment = next(fact for fact in context["facts"].values() if fact["key"] == "installment_standard")
        draft = f"Khoản trả tham khảo {{fact:{installment['id']}}}, chưa phải phê duyệt."
        return {"message": {"role": "assistant", "content": json.dumps({"draft": draft})}, "draft": draft, "handoff": False}

    monkeypatch.setattr(loop_module, "tool_completion", provider)
    save = loop_module.AgentLoop._save
    crashed = False

    def crash_after_rate(self, turn_id, sid, run):
        nonlocal crashed
        if not crashed and run["calls"] and run["calls"][-1]["tool"] == "get_rate":
            crashed = True
            raise RuntimeError("crash after native commit before loop acknowledgement")
        return save(self, turn_id, sid, run)

    monkeypatch.setattr(loop_module.AgentLoop, "_save", crash_after_rate)
    text = ("Số điện thoại của tôi là 0900000001. Tôi xác nhận nhu cầu mới vay mua nhà 2 tỷ trong 20 năm, "
            "thu nhập 45 triệu, không có nợ, giá nhà 3 tỷ.")
    with TestClient(create_app(settings)) as client:
        session = client.post("/sessions", json={}).json()
        sid = session["session_id"]
        headers = {"Authorization": "Bearer " + session["session_token"]}
        body = {"text": text, "client_message_id": "recover"}
        with pytest.raises(RuntimeError, match="before loop acknowledgement"):
            client.post(f"/sessions/{sid}/messages", headers=headers, json=body)
        first = client.app.state.harness.db.one("SELECT result FROM tool_calls WHERE tool='get_rate'")["result"]
    monkeypatch.setattr(loop_module.AgentLoop, "_save", save)
    with TestClient(create_app(settings)) as restarted:
        response = restarted.post(f"/sessions/{sid}/messages", headers=headers, json=body)
        assert response.status_code == 200, response.text
        assert response.json()["used_fact_ids"]
        db = restarted.app.state.harness.db
        assert db.one("SELECT count(*) AS n FROM tool_calls WHERE tool='get_rate'")["n"] == 1
        assert db.one("SELECT result FROM tool_calls WHERE tool='get_rate'")["result"] == first
        calculated = json.loads(db.one("SELECT result FROM tool_calls WHERE tool='calc_installment'")["result"])
        assert calculated["error"] is None
