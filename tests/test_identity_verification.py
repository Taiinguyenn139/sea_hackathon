"""Phone identities must reuse customer history without reusing verification."""

import json

import pytest
from fastapi.testclient import TestClient

from harness.api import create_app
from harness.config import Settings
from simulators.identity_dataset import DEFAULT_DATASET_DIR
from simulators.identity_schemas import IdentityFile

IDENTITIES = IdentityFile.model_validate_json((DEFAULT_DATASET_DIR / "identities.json").read_text()).identities
ADMIN = {"Authorization": "Bearer identity-admin-test"}
PUBLIC_TOOLS = {
    "get_rate", "get_active_promotions", "get_card_offer", "calc_installment", "check_suitability", "get_disclosures",
}
VERIFIED_TOOLS = PUBLIC_TOOLS | {"send_summary", "schedule_callback"}


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(
        db_path=str(tmp_path / "identity.sqlite3"),
        admin_token="identity-admin-test",
        staff_password="identity-staff-test",
        environment="development",
        llm_backend="rules",
        release_gate="",
        telegram_token="",
        telegram_secret="",
    ))
    with TestClient(app) as connection:
        yield connection


def new_session(client, identifier, channel="web"):
    body = {"demo_profile": identifier, "mode": "harness"}
    route, headers = "/sessions", {}
    if channel != "web":
        route, headers = "/admin/sessions", ADMIN
        body["channel"] = channel
    response = client.post(route, headers=headers, json=body)
    assert response.status_code == 200, response.text
    session = response.json()
    assert session["identity_status"] == "unverified"
    assert session["brief"]["redacted"] is True
    return session, {"Authorization": "Bearer " + session["session_token"]}


def verify(client, session, auth, phone):
    return client.post(f"/sessions/{session['session_id']}/verify", headers=auth, json={"phone": phone})


def tools(client, session, auth):
    response = client.get(f"/sessions/{session['session_id']}/tools", headers=auth)
    assert response.status_code == 200, response.text
    return {tool["name"] for tool in response.json()["tools"]}


def test_demo_directory_exposes_ten_phone_profiles_from_identity_data(client):
    response = client.get("/demo/profiles")
    assert response.status_code == 200
    assert response.json()["fictional"] is True
    profiles = response.json()["profiles"]
    assert len(profiles) == len(IDENTITIES) == 10
    by_phone = {profile["id"]: profile for profile in profiles}
    assert set(by_phone) == {identity.phone for identity in IDENTITIES}
    for identity in IDENTITIES:
        profile = by_phone[identity.phone]
        assert profile["phone"] == identity.phone
        assert profile["name"] == identity.full_name
        assert "dob" not in profile and "date_of_birth" not in profile
        assert profile.get("runtime_profile") == identity.runtime_profile


@pytest.mark.parametrize("identity", IDENTITIES, ids=lambda identity: identity.phone)
def test_each_identity_verifies_only_its_own_phone(client, identity):
    session, auth = new_session(client, identity.phone)
    assert session["user_id"] == identity.phone
    assert tools(client, session, auth) == PUBLIC_TOOLS
    other = next(row for row in IDENTITIES if row.user_id != identity.user_id)
    denied = verify(client, session, auth, other.phone)
    assert denied.status_code == 200, denied.text
    assert denied.json()["identity_status"] == "unverified"
    assert denied.json()["brief"]["redacted"] is True
    assert tools(client, session, auth) == PUBLIC_TOOLS
    accepted = verify(client, session, auth, "+84" + identity.phone[1:])
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["identity_status"] == "verified"
    assert accepted.json()["brief"]["name"] == identity.full_name
    assert accepted.json()["brief"]["customer_id"] == (identity.runtime_profile or identity.phone)
    assert tools(client, session, auth) == VERIFIED_TOOLS


def test_dataset_session_flags_and_dialogue_do_not_seed_live_state(client):
    session, auth = new_session(client, IDENTITIES[2].phone)
    db = client.app.state.harness.db
    row = db.one("SELECT * FROM sessions WHERE id=?", (session["session_id"],))
    assert row["verified"] == 0
    assert tools(client, session, auth) == PUBLIC_TOOLS
    for table in ("turns", "facts", "tool_calls", "memory_jobs", "journey_events"):
        where = " WHERE client_id!='conversation-opening'" if table == "turns" else ""
        assert db.one(f"SELECT count(*) AS n FROM {table}" + where)["n"] == 0


def test_verification_is_specific_to_session_and_channel_and_never_customer_login(client):
    identity = IDENTITIES[4]
    web, web_auth = new_session(client, identity.phone)
    telegram, telegram_auth = new_session(client, "+84" + identity.phone[1:], "telegram")
    assert web["user_id"] == telegram["user_id"] == identity.phone
    assert web["session_id"] != telegram["session_id"]
    assert verify(client, web, web_auth, "+84" + identity.phone[1:]).json()["identity_status"] == "verified"
    assert tools(client, web, web_auth) == VERIFIED_TOOLS
    assert tools(client, telegram, telegram_auth) == PUBLIC_TOOLS
    assert verify(client, telegram, telegram_auth, "+84" + identity.phone[1:]).json()["identity_status"] == "verified"
    assert tools(client, telegram, telegram_auth) == VERIFIED_TOOLS
    harness = client.app.state.harness
    result = harness.engine.bank.call(
        "crm_lookup", {"as_of": "2026-10-31"}, identity.phone, telegram["session_id"],
    )
    assert result["error"] and "login required" in result["error"]
    later, later_auth = new_session(client, identity.phone)
    assert tools(client, later, later_auth) == PUBLIC_TOOLS


def test_another_sessions_capability_cannot_verify_or_read_tools(client):
    identity = IDENTITIES[2]
    target, _ = new_session(client, identity.phone)
    _, attacker_auth = new_session(client, IDENTITIES[3].phone)
    assert verify(client, target, attacker_auth, "+84" + identity.phone[1:]).status_code == 403
    assert verify(client, target, {}, "+84" + identity.phone[1:]).status_code == 403
    assert verify(client, target, ADMIN, "+84" + identity.phone[1:]).status_code == 403
    assert client.get(f"/sessions/{target['session_id']}/tools", headers=attacker_auth).status_code == 403
    assert client.app.state.harness.session(target["session_id"])["verified"] == 0


def test_alias_phone_and_e164_share_identity_rate_limit_across_different_ips(client):
    identity = IDENTITIES[0]
    identifiers = ["hoa", identity.phone, "+84" + identity.phone[1:], "090 000-0001", "hoa"]
    for index, identifier in enumerate(identifiers):
        session, auth = new_session(client, identifier)
        source = TestClient(client.app, client=(f"198.51.100.{index + 1}", 50000))
        try:
            denied = verify(source, session, auth, IDENTITIES[1].phone)
            assert denied.status_code == 200, denied.text
            assert denied.json()["identity_status"] == "unverified"
        finally:
            source.close()
    session, auth = new_session(client, "+84" + identity.phone[1:])
    source = TestClient(client.app, client=("198.51.100.100", 50000))
    try:
        assert verify(source, session, auth, "+84" + identity.phone[1:]).status_code == 429
    finally:
        source.close()
    attempts = client.app.state.harness.db.all("SELECT identity,source FROM auth_attempts")
    assert len(attempts) == 5
    assert {row["identity"] for row in attempts} == {"hoa"}
    assert len({row["source"] for row in attempts}) == 5


def test_verification_audit_redacts_supplied_phone_arguments(client):
    identity = IDENTITIES[9]
    session, auth = new_session(client, identity.phone)
    assert verify(client, session, auth, IDENTITIES[0].phone).json()["identity_status"] == "unverified"
    assert verify(client, session, auth, identity.phone).json()["identity_status"] == "verified"
    db = client.app.state.harness.db
    calls = db.all("SELECT args,result FROM tool_calls WHERE tool='verify_identity'")
    assert len(calls) == 2
    assert all(json.loads(row["args"])["phone"] == "[redacted]" for row in calls)
    assert identity.phone not in json.dumps([row["args"] for row in calls])
    assert IDENTITIES[0].phone not in json.dumps([row["args"] for row in calls])


def test_all_phone_ids_map_to_the_expected_bank_customer(client):
    bank = client.app.state.harness.engine.bank
    for identity in IDENTITIES:
        assert bank.bank_id(identity.phone) == identity.bank_customer_id
        assert bank.bank_id("+84" + identity.phone[1:]) == identity.bank_customer_id
        if identity.runtime_profile:
            assert bank.bank_id(identity.runtime_profile) == identity.bank_customer_id


@pytest.mark.parametrize("identity", IDENTITIES[:2], ids=lambda identity: identity.runtime_profile)
def test_phone_and_old_alias_share_history_without_leaking_it_to_new_session(client, identity):
    old, old_auth = new_session(client, identity.runtime_profile)
    assert old["user_id"] == identity.phone
    assert verify(client, old, old_auth, "+84" + identity.phone[1:]).json()["identity_status"] == "verified"
    written = client.post(f"/sessions/{old['session_id']}/messages", headers=old_auth, json={
        "text": "Thu nhập 45 triệu mỗi tháng", "client_message_id": "legacy-income",
    })
    assert written.status_code == 200, written.text
    aliases = [identity.runtime_profile, identity.phone, "+84" + identity.phone[1:]]
    briefs = [client.get(f"/customers/{alias}/brief", headers=ADMIN) for alias in aliases]
    assert all(response.status_code == 200 for response in briefs)
    assert briefs[0].json() == briefs[1].json() == briefs[2].json()
    assert any(fact["key"] == "income" and fact["value"] == "45000000" for fact in briefs[0].json()["facts"])
    assert briefs[0].json()["customer_id"] == identity.runtime_profile
    phone, phone_auth = new_session(client, identity.phone)
    assert tools(client, phone, phone_auth) == PUBLIC_TOOLS
    assert client.get(f"/customers/{identity.phone}/brief", headers=phone_auth).status_code == 401
    accepted = verify(client, phone, phone_auth, "+84" + identity.phone[1:])
    assert accepted.status_code == 200
    assert any(fact["key"] == "income" for fact in accepted.json()["brief"]["facts"])
    assert len({row["customer_id"] for row in client.app.state.harness.db.all("SELECT * FROM sessions")}) == 1


@pytest.mark.parametrize("identity", IDENTITIES[:3], ids=lambda identity: identity.phone)
def test_admin_telegram_link_normalizes_phone_and_preserves_runtime_alias(client, identity):
    identifier = "+84" + identity.phone[1:]
    response = client.post("/admin/identity-links/telegram", headers=ADMIN, json={
        "customer_id": identifier, "external_id": "synthetic-test-telegram-link",
    })
    assert response.status_code == 200, response.text
    row = client.app.state.harness.db.one("SELECT * FROM identity_links WHERE external_id=?", (
        "synthetic-test-telegram-link",
    ))
    assert row["customer_id"] == (identity.runtime_profile or identity.phone)
    assert row["channel"] == "telegram"


@pytest.mark.parametrize("identifier,expected_status", [(900000001, 422), ("0900000099", 400), ("+1900000001", 400)])
def test_unknown_or_numeric_identity_is_not_silently_bound(client, identifier, expected_status):
    response = client.post("/sessions", json={"demo_profile": identifier})
    assert response.status_code == expected_status
    assert not client.app.state.harness.db.all("SELECT * FROM sessions")


@pytest.mark.parametrize("include_phone", [False, True])
def test_dob_payload_is_rejected_instead_of_falling_back_to_old_verification(client, include_phone):
    identity = IDENTITIES[0]
    session, auth = new_session(client, identity.phone)
    body = {"dob": identity.date_of_birth.isoformat()}
    if include_phone:
        body["phone"] = identity.phone
    response = client.post(f"/sessions/{session['session_id']}/verify", headers=auth, json=body)
    assert response.status_code == 422
    assert tools(client, session, auth) == PUBLIC_TOOLS
    assert not client.app.state.harness.db.all("SELECT * FROM tool_calls WHERE tool='verify_identity'")


def chat(client, session, auth, text, message_id):
    response = client.post(f"/sessions/{session['session_id']}/messages", headers=auth, json={
        "text": text, "client_message_id": message_id,
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_phone_in_chat_verifies_bound_identity_once_and_public_chat_still_works(client):
    identity = IDENTITIES[2]
    session, auth = new_session(client, identity.phone)
    public = chat(client, session, auth, "Ưu đãi vay mua nhà hiện tại?", "public")
    assert "7,1" in public["reply"]
    assert tools(client, session, auth) == PUBLIC_TOOLS
    text = "Số điện thoại của tôi là +84" + identity.phone[1:]
    verified = chat(client, session, auth, text, "identify")
    assert verified["identity_status"] == "verified"
    assert verified["user_id"] == identity.phone
    assert verified["brief"]["redacted"] is False
    assert verified["brief"]["user_id"] == identity.phone
    assert verified["brief"]["name"] == identity.full_name
    assert tools(client, session, auth) == VERIFIED_TOOLS
    assert chat(client, session, auth, text, "identify") == verified
    calls = client.app.state.harness.db.all(
        "SELECT args FROM tool_calls WHERE tool='verify_identity' AND session_id=?", (session["session_id"],),
    )
    assert len(calls) == 1
    assert json.loads(calls[0]["args"])["phone"] == "[redacted]"


def test_wrong_chat_phone_cannot_switch_identity_or_read_another_customers_memory(client):
    own, other = IDENTITIES[2:4]
    other_session, other_auth = new_session(client, other.phone)
    assert verify(client, other_session, other_auth, other.phone).json()["identity_status"] == "verified"
    chat(client, other_session, other_auth, "Thu nhập 99 triệu mỗi tháng", "other-income")
    session, auth = new_session(client, own.phone)
    denied = chat(client, session, auth, "Số điện thoại của tôi là " + other.phone, "wrong-phone")
    assert denied["identity_status"] == "unverified"
    assert "99" not in denied["reply"] and other.full_name not in denied["reply"]
    row = client.app.state.harness.session(session["session_id"])
    assert row["customer_id"] == own.phone and row["verified"] == 0
    assert tools(client, session, auth) == PUBLIC_TOOLS
    assert not client.app.state.harness.db.all(
        "SELECT * FROM memory_jobs WHERE customer_id=?", (own.phone,),
    )


def test_multiple_phone_numbers_in_one_message_require_clarification(client):
    own, other = IDENTITIES[2:4]
    session, auth = new_session(client, own.phone)
    chat(client, session, auth, f"Số của tôi là {own.phone} hoặc {other.phone}", "ambiguous")
    assert tools(client, session, auth) == PUBLIC_TOOLS
    assert client.app.state.harness.session(session["session_id"])["verified"] == 0
    assert not client.app.state.harness.db.all(
        "SELECT * FROM tool_calls WHERE tool='verify_identity' AND session_id=?", (session["session_id"],),
    )
