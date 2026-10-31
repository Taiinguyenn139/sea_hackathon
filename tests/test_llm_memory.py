"""Structured LLM memory is authoritative across channels, with no rule fallback."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from harness.api import create_app
from harness.config import Settings
from harness.llm import DEFAULT_REPLY


def fact(value="65000000", key="income", operation="replace", source="65tr"):
    return {"key": key, "value": value, "unit": "đồng", "operation": operation, "source_text": source}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    settings = Settings(db_path=str(tmp_path / "llm.sqlite"), llm_backend="openai", openai_key="test-key",
                        openai_model="primary", openai_backup_model="backup", demo_phone_identity=True,
                        telegram_secret="test-secret", telegram_token="", identity_history_enabled=False)
    answers, extractions, drafts = {}, [], []

    def forbidden(*args, **kwargs):
        raise AssertionError("LLM memory must not call the rule parser")

    for target in ["harness.memory.service.extract", "harness.memory.service.corrects", "harness.agent.engine.extract"]:
        monkeypatch.setattr(target, forbidden)

    async def post(self, url, **kwargs):
        messages = kwargs["json"]["messages"]
        instruction = messages[0]["content"]
        if "BACKEND_CONTEXT_JSON:\n" in instruction:
            payload = json.loads(instruction.split("BACKEND_CONTEXT_JSON:\n", 1)[1])
            drafts.append(payload)
            income = next((f for f in payload["facts"].values()
                           if f["key"] == "income" and f["status"] == "active"), None)
            result = {"draft": "Thu nhập là {fact:" + income["id"] + "}." if income
                      else "Anh/chị chưa có mức thu nhập thống nhất trong hồ sơ. Vui lòng xác nhận lại."}
        else:
            payload = json.loads(messages[1]["content"])
            if "INPUT_SECURITY_CLASSIFIER" in instruction:
                result = {"category": "safe"}
            else:
                assert "Trích xuất memory" in instruction, "OpenAI advice must use native function-call context"
                extractions.append(payload)
                result = answers.get(payload.get("text", "demo_history"), {"facts": []})
                if result == "unavailable":
                    return httpx.Response(503)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}, "finish_reason": "stop"}]})

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    return settings, answers, extractions, drafts


def known(payload):
    return {f["key"]: f["value"] for f in payload["facts"].values()
            if f["status"] == "active" and f["source_type"] != "profile"}


def login(client, phone="0901234567", mode="harness"):
    response = client.post("/demo/phone/session", json={"phone": phone})
    assert response.status_code == 200, response.text
    session = response.json()
    client.app.state.harness.db.execute("UPDATE sessions SET mode=? WHERE id=?", (mode, session["session_id"]))
    return session


def say(client, session, text, key, stream=False):
    response = client.post(f"/sessions/{session['session_id']}/messages" + ("/stream" if stream else ""),
                          headers={"Authorization": "Bearer " + session["session_token"]},
                          json={"text": text, "client_message_id": key})
    assert response.status_code == 200, response.text
    if stream:
        assert "event: error" not in response.text
        return json.loads(response.text.split("event: result\ndata: ")[-1].split("\n")[0])
    return response.json()


def tg(client, number, text=None, contact=None):
    response = client.post("/webhooks/telegram", headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
                           json={"update_id": number, "message": {
                               "date": 1000 + number, "chat": {"id": 111, "type": "private"},
                               "from": {"id": 111, "first_name": "Test"},
                               **({"text": text} if text is not None else {}),
                               **({"contact": {"user_id": 111, "phone_number": contact}} if contact else {}),
                           }})
    assert response.status_code == 200
    db = client.app.state.harness.db
    assert db.one("SELECT status,error FROM provider_events WHERE id=?", (f"telegram:{number}",)) == {
        "status": "processed", "error": None,
    }
    return db.one("SELECT * FROM sessions WHERE channel='telegram' ORDER BY rowid DESC LIMIT 1")


@pytest.mark.parametrize("update_on_web", [False, True])
def test_shorthand_update_and_short_confirmation_cross_channels_and_restart(setup, update_on_web):
    settings, answers, extractions, drafts = setup
    answers.update({
        "Thu nhập 47 triệu": {"facts": [fact("47000000", source="47 triệu")]},
        "Mức thu nhập hiện tại của tôi là 65tr / tháng": {"facts": [fact()]},
        "65tr đó": {"facts": [fact(source="65tr đó")]},
    })
    with TestClient(create_app(settings)) as client:
        session = login(client)
        telegram = tg(client, 1, contact="0901234567")
        tg(client, 2, "Thu nhập 47 triệu")
        if update_on_web:
            first = say(client, session, "Mức thu nhập hiện tại của tôi là 65tr / tháng", "update", stream=True)
            tg(client, 3, "Lương tôi bao nhiêu?")
            assert known(drafts[-1])["income"] == "65000000"
            before = len(extractions)
            assert say(client, session, "Mức thu nhập hiện tại của tôi là 65tr / tháng", "update") == first
            assert len(extractions) == before
        else:
            tg(client, 3, "Mức thu nhập hiện tại của tôi là 65tr / tháng")
            tg(client, 4, "65tr đó")
            short = next(x for x in extractions if x["text"] == "65tr đó")
            assert any("65tr" in t["text"] for t in short["recent_conversation"])
            assert any(f["key"] == "income" and f["value"] == "65000000" for f in short["remembered_facts"])
            assert "65 triệu" in say(client, session, "Lương tôi bao nhiêu?", "recall")["reply"]
        customer = telegram["customer_id"]
        db = client.app.state.harness.db
        assert db.all("SELECT value FROM facts WHERE customer_id=? AND key='income' AND status='active'", (customer,)) == [
            {"value": "65000000"},
        ]
        assert not db.all("SELECT id FROM facts WHERE customer_id=? AND status='conflicted'", (customer,))
        stored = [json.loads(r["payload"]) for r in db.all("SELECT payload FROM memory_jobs")]
        assert any(p.get("extraction", {}).get("facts") == [fact()] for p in stored)
        other = login(client, "0901234568")
        say(client, other, "Lương tôi bao nhiêu?", "other")
        assert known(drafts[-1]) == {}
        assert extractions[-1]["remembered_facts"] == []
    with TestClient(create_app(settings)) as client:
        session = login(client)
        assert "65 triệu" in say(client, session, "Lương tôi bao nhiêu?", "after-restart")["reply"]
        assert session["brief"]["customer_id"] == customer


@pytest.mark.parametrize("mode", ["harness", "no_memory", "full_history"])
def test_all_llm_modes_use_structured_values_without_rule_parsing(setup, mode):
    settings, answers, _, drafts = setup
    # Natural words outside the old money regex must remain the LLM's exact decision.
    answers["Lương sáu mươi lăm củ nhé"] = {"facts": [fact(source="sáu mươi lăm củ")]}
    with TestClient(create_app(settings)) as client:
        session = login(client, mode=mode)
        assert "65 triệu" in say(client, session, "Lương sáu mươi lăm củ nhé", "income")["reply"]
        assert "65 triệu" in say(client, session, "Lương tôi bao nhiêu?", "recall")["reply"]
        assert known(drafts[-1])["income"] == "65000000"
        assert drafts[-1]["memory_result"]["saved"] is True
        assert "giá trị căn nhà" not in say(client, session, "Lương tôi bao nhiêu?", "recall-again")["reply"]


@pytest.mark.parametrize("answer", ["unavailable", {"facts": [fact("65tr")]}, {"spans": ["Thu nhập 45 triệu"]}])
def test_extraction_failure_does_not_save_or_acknowledge_using_rules(setup, answer):
    settings, answers, _, drafts = setup
    answers["Thu nhập 45 triệu"] = answer
    with TestClient(create_app(settings)) as client:
        session = login(client)
        response = say(client, session, "Thu nhập 45 triệu", "failure", stream=True)
        assert response["reply"] == DEFAULT_REPLY
        assert drafts == []  # Do not claim a write that did not happen.
        db = client.app.state.harness.db
        assert not db.all("SELECT * FROM facts")
        assert db.one("SELECT status FROM memory_jobs")["status"] == "pending"
        client.app.state.harness.engine.memory.flush(session["brief"]["customer_id"], allow_llm=False)
        assert not db.all("SELECT * FROM facts")


def test_model_controls_conflict_and_replacement_and_ignores_hypotheticals(setup):
    settings, answers, _, drafts = setup
    answers.update({
        "Thu nhập 47 triệu": {"facts": [fact("47000000", source="47 triệu")]},
        "Có người ghi 53tr, tôi chưa chắc": {"facts": [fact("53000000", operation="conflict", source="53tr")]},
        "Xác nhận 65tr đó": {"facts": [fact(source="65tr")]},
        "Nếu lương tăng 90tr thì sao?": {"facts": []},
    })
    with TestClient(create_app(settings)) as client:
        session = login(client)
        say(client, session, "Thu nhập 47 triệu", "first")
        # Even on the same channel, the LLM can mark a genuine conflict.
        say(client, session, "Có người ghi 53tr, tôi chưa chắc", "conflict")
        assert "income" not in known(drafts[-1])
        assert drafts[-1]["memory_result"]["facts"][0]["operation"] == "conflict"
        say(client, session, "Xác nhận 65tr đó", "confirm")
        say(client, session, "Nếu lương tăng 90tr thì sao?", "hypothetical")
        assert known(drafts[-1])["income"] == "65000000"
        assert drafts[-1]["memory_result"] == {"saved": True, "facts": []}


def test_preferred_dataset_baseline_keeps_provenance_and_live_values_take_priority(setup):
    settings, answers, extractions, drafts = setup
    settings.identity_history_enabled = True
    answers["Lương hiện tại 65tr"] = {"facts": [fact()]}
    with TestClient(create_app(settings)) as client:
        first = login(client, "0900000002")
        baseline = [f for f in first["brief"]["needs"] if f.get("source_origin") == "dataset_history"]
        values = {f["key"]: f["value"] for f in baseline}
        assert values["income"] == "100000000" and values["amount"] == "4000000000" and values["term"] == "300"
        assert all(f["source_ref"] and f["source_at"] for f in baseline)
        history = [row for row in first["brief"]["history"] if row["source_origin"] == "dataset_history"]
        assert len(history) == 9
        assert extractions == []  # Canonical fixture inputs do not invoke a second LLM baseline extractor.
        second = login(client, "0900000002")
        assert second["brief"]["customer_id"] == first["brief"]["customer_id"]
        assert {f["id"] for f in baseline} <= {f["id"] for f in second["brief"]["needs"]}
        assert extractions == []
        say(client, first, "Lương hiện tại 65tr", "live")
        say(client, second, "Lương tôi bao nhiêu?", "recall")
        assert known(drafts[-1])["income"] == "65000000"
        income = [f for f in drafts[-1]["facts"].values() if f["key"] == "income"]
        assert len(income) == 1 and income[0]["source_origin"] == "runtime"
        assert not any("history" in request for request in extractions)


def test_openai_history_opt_out_does_not_reenable_legacy_demo_baseline(setup):
    settings, _, extractions, _ = setup
    assert settings.demo_phone_identity and settings.identity_history_enabled is False
    with TestClient(create_app(settings)) as client:
        session = login(client, "0900000002")
        assert session["brief"]["needs"] == []
        assert session["brief"]["demo_history"] == []
        assert extractions == []
