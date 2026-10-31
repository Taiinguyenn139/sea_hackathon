"""Switching products cannot borrow an earlier synthetic loan's parameters."""

import pytest

from harness.config import Settings
from harness.db import Database
from harness.service import Harness


@pytest.mark.parametrize("mode", ["harness", "full_history"])
@pytest.mark.parametrize("with_live_amount", [False, True])
def test_preferred_history_product_change_discards_old_terms_but_keeps_live_amount(tmp_path, mode, with_live_amount):
    harness = Harness(Database(str(tmp_path / "product-context.sqlite3")), Settings(
        llm_backend="rules", identity_history_enabled=True,
    ))
    created = harness.new_session(mode=mode)
    sid = created["session_id"]
    harness.verify(harness.session(sid), "0900000002", "source")
    baseline = harness.engine.load_context({"session": harness.session(sid), "text": "Xin chào", "turn_id": "before"})
    assert baseline["known"]["product"] == "home_loan"
    assert baseline["known"]["term"] == "300"

    text = "Tôi xác nhận nhu cầu mới vay tiêu dùng" + (" 200 triệu" if with_live_amount else "")
    changed = harness.engine.load_context({"session": harness.session(sid), "text": text, "turn_id": "change"})
    assert changed["known"]["product"] == "personal_loan"
    assert not any(fact["key"] in {"term", "property_value", "loan_status"} for fact in changed["facts"].values())
    assert changed["known"].get("amount") == ("200000000" if with_live_amount else None)
    assert not any(fact["key"] == "amount" and fact.get("source_origin") == "dataset_history" for fact in changed["facts"].values())

    harness.message(sid, text, "new-product")
    harness.engine.memory.flush("minh")
    sibling = harness.new_session(mode=mode)
    harness.verify(harness.session(sibling["session_id"]), "0900000002", "source")
    reloaded = harness.engine.load_context({
        "session": harness.session(sibling["session_id"]), "text": "Xin chào", "turn_id": "after",
    })
    assert reloaded["known"]["product"] == "personal_loan"
    assert not any(fact["key"] in {"term", "property_value", "loan_status"} for fact in reloaded["facts"].values())
    assert reloaded["known"].get("amount") == ("200000000" if with_live_amount else None)
