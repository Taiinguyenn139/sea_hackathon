"""Financial claims need matching receipts, beyond a cooperative model prompt."""

import json

import pytest
from fastapi.testclient import TestClient
from test_agent_tools import Provider, answer, calls, context, message, new_session, settings

from harness.api import create_app

LOAN_TEXT = "Tôi xác nhận nhu cầu mới vay mua nhà 2 tỷ trong 20 năm, thu nhập 45 triệu, không có nợ, giá nhà 3 tỷ."


def trace(client, session):
    return json.loads(client.app.state.harness.db.one(
        "SELECT data FROM traces WHERE session_id=? ORDER BY rowid DESC LIMIT 1", (session["session_id"],),
    )["data"])


def rate_draft(payload):
    rate = next(fact for fact in context(payload)["facts"].values() if fact["key"] == "standard_rate")
    return answer(f"Lãi suất tham khảo {{fact:{rate['id']}}}.")


@pytest.mark.parametrize("product,topics", [("credit_card", ["rate"]), ("home_loan", ["fee"])])
def test_unrelated_disclosure_cannot_unlock_a_loan_rate(tmp_path, monkeypatch, product, topics):
    provider = Provider([
        calls(
            ("rate", "get_rate", {"product": "home_loan", "amount": 2000000000, "term": 240, "segment": "mass"}),
            ("wrong-disclosure", "get_disclosures", {"product": product, "topics": topics}),
        ),
        rate_draft, rate_draft,
    ])
    provider.install(monkeypatch)
    with TestClient(create_app(settings(tmp_path, identity_history_enabled=False))) as client:
        session, auth = new_session(client)
        reply = message(client, session, auth, LOAN_TEXT)
        assert reply["used_fact_ids"] == []
        assert "10,5" not in reply["reply"]
        assert any(event["guard_id"] == "G1" for event in reply["guard_events"])
        rows = client.app.state.harness.db.all("SELECT tool,result FROM tool_calls")
        assert {row["tool"] for row in rows} == {"get_rate", "get_disclosures"}
        assert all(json.loads(row["result"])["error"] is None for row in rows)


def test_ineligible_loan_stays_g4_even_after_successful_different_product_check(tmp_path, monkeypatch):
    def approved_looking_quote(payload):
        rate = next(fact for fact in context(payload)["facts"].values() if fact["key"] == "standard_rate")
        return answer(f"Hồ sơ phù hợp, lãi suất {{fact:{rate['id']}}}.")

    provider = Provider([
        calls(("identity", "verify_identity", {"phone": "0900000003"})),
        calls(
            ("home-check", "check_suitability", {"product": "home_loan", "income": 10000000, "debt": 0,
                                                "amount": 2000000000, "term": 240, "property_value": 3000000000}),
            ("card-check", "check_suitability", {"product": "credit_card", "income": 10000000, "debt": 0}),
            ("rate", "get_rate", {"product": "home_loan", "amount": 2000000000, "term": 240, "segment": "mass"}),
            ("disclosures", "get_disclosures", {"product": "home_loan", "topics": ["rate"]}),
        ),
        approved_looking_quote,
    ])
    provider.install(monkeypatch)
    with TestClient(create_app(settings(tmp_path, identity_history_enabled=False))) as client:
        session, auth = new_session(client)
        text = "Số điện thoại của tôi là 0900000003. " + LOAN_TEXT.replace("45 triệu", "10 triệu")
        reply = message(client, session, auth, text)
        assert "chưa phù hợp" in reply["reply"] and "10,5" not in reply["reply"]
        assert reply["used_fact_ids"] == []
        assert any(event["guard_id"] == "G4" for event in reply["guard_events"])
        checks = [json.loads(row["result"])["data"]["ok"] for row in client.app.state.harness.db.all(
            "SELECT result FROM tool_calls WHERE tool='check_suitability' ORDER BY rowid",
        )]
        assert checks == [False, True]


def test_ineligible_card_offer_cannot_be_overruled_by_a_nonnumeric_model_claim(tmp_path, monkeypatch):
    provider = Provider([
        calls(("identity", "verify_identity", {"phone": "0900000003"})),
        calls(("card", "get_card_offer", {"income": 2000000, "segment": "mass"})),
        answer("Anh/chị đủ điều kiện mở thẻ."),
    ])
    provider.install(monkeypatch)
    with TestClient(create_app(settings(tmp_path, identity_history_enabled=False))) as client:
        session, auth = new_session(client)
        reply = message(client, session, auth,
                        "Số điện thoại của tôi là 0900000003. Tôi xác nhận nhu cầu mới mở thẻ, thu nhập 2 triệu, không có nợ.")
        assert "chưa phù hợp" in reply["reply"]
        assert any(event["guard_id"] == "G4" for event in reply["guard_events"])
        offer = json.loads(client.app.state.harness.db.one(
            "SELECT result FROM tool_calls WHERE tool='get_card_offer'",
        )["result"])
        assert offer["error"] is None and offer["data"]["eligible"] is False


def test_installment_requires_a_matching_suitability_receipt_in_this_turn(tmp_path, monkeypatch):
    def calculate_without_assessment(payload):
        rate = next(fact for fact in context(payload)["facts"].values() if fact["key"] == "standard_rate")
        return calls(("calc", "calc_installment", {
            "amount": 2000000000, "term": 240, "rate": rate["value"], "rate_fact_id": rate["id"],
        }))

    provider = Provider([
        calls(("rate", "get_rate", {"product": "home_loan", "amount": 2000000000, "term": 240, "segment": "mass"})),
        calculate_without_assessment, answer("Tôi cần kiểm tra điều kiện phù hợp trước khi tính khoản trả."),
    ])
    provider.install(monkeypatch)
    with TestClient(create_app(settings(tmp_path, identity_history_enabled=False))) as client:
        session, auth = new_session(client)
        reply = message(client, session, auth, LOAN_TEXT)
        assert reply["used_fact_ids"] == []
        record = next(record for record in trace(client, session)["tool_calls"] if record["tool"] == "calc_installment")
        assert "suitability" in record["result"]["error"]
        assert not client.app.state.harness.db.all("SELECT * FROM tool_calls WHERE tool='calc_installment'")


def test_huge_integer_argument_is_a_controlled_tool_error_not_a_server_error(tmp_path, monkeypatch):
    provider = Provider([
        calls(("oversized", "get_rate", {"product": "home_loan", "amount": 10**400, "term": 240, "segment": "mass"})),
        answer("Anh/chị vui lòng xác nhận lại số tiền vay."),
    ])
    provider.install(monkeypatch)
    with TestClient(create_app(settings(tmp_path, identity_history_enabled=False))) as client:
        session, auth = new_session(client)
        reply = message(client, session, auth, LOAN_TEXT)
        assert reply["used_fact_ids"] == []
        record = trace(client, session)["tool_calls"][0]
        assert record["tool"] == "get_rate" and record["result"]["error"].startswith("invalid:")
        assert not client.app.state.harness.db.all("SELECT * FROM tool_calls WHERE tool='get_rate'")
