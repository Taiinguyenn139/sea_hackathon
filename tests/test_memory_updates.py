"""An update to one field must not confirm hypothetical or unrelated values."""

import pytest

from harness.config import Settings
from harness.db import Database
from harness.memory.service import Memory


def remember(db, memory, turn, channel, text):
    with db.transaction() as connection:
        memory.enqueue(connection, turn, "customer", "session-" + channel, channel, text)
    memory.flush("customer")


@pytest.mark.parametrize("text", [
    "Nếu lương tăng lên 53 triệu thì vay được bao nhiêu?",
    "Lương tôi sẽ tăng lên 53 triệu",
    "Lương chưa tăng lên 53 triệu",
    "Lương không tăng lên 53 triệu",
    "Lương tăng lên 53 triệu?",
    "Tôi xác nhận thời hạn vay 15 năm; thu nhập 53 triệu",
    "Nếu tôi xác nhận nhu cầu mới vay mua nhà 2 tỷ, thu nhập 53 triệu thì sao?",
    "Tôi không xác nhận nhu cầu mới vay mua nhà 2 tỷ, thu nhập 53 triệu",
    "Tôi xác nhận nhu cầu mới vay mua nhà 2 tỷ; nếu thu nhập 53 triệu thì sao?",
])
def test_possible_or_unrelated_update_does_not_confirm_income(text):
    db = Database(":memory:")
    memory = Memory(db)
    remember(db, memory, "first", "telegram", "Thu nhập 47 triệu")
    remember(db, memory, "second", "web", text)
    assert not db.all("SELECT id FROM facts WHERE key='income' AND status='active'")
    assert {f["value"] for f in db.all("SELECT value FROM facts WHERE key='income' AND status='conflicted'")} == {
        "47000000", "53000000",
    }


@pytest.mark.parametrize("text,key,value", [
    ("Xác nhận thu nhập chính xác 53 triệu không có nợ", "income", "53000000"),
    ("Thu nhập giảm còn 53 triệu", "income", "53000000"),
    ("Tôi cập nhật thu nhập thành 53,5 triệu", "income", "53500000"),
    ("Tôi cập nhật thời hạn vay thành 15 năm", "term", "180"),
])
def test_explicit_update_resolves_existing_conflict(text, key, value):
    db = Database(":memory:")
    memory = Memory(db)
    remember(db, memory, "first", "telegram", "Thu nhập 47 triệu, vay trong 20 năm")
    remember(db, memory, "second", "web", "Thu nhập 50 triệu, vay trong 10 năm")
    remember(db, memory, "third", "web", text)
    assert db.all("SELECT value FROM facts WHERE key=? AND status='active'", (key,)) == [{"value": value}]
    assert not db.all("SELECT id FROM facts WHERE key=? AND status='conflicted'", (key,))


def test_natural_income_update_overrides_history_without_replacing_other_customer_needs(tmp_path):
    db = Database(str(tmp_path / "dataset-income.sqlite3"))
    memory = Memory(db, Settings(llm_backend="rules", identity_history_enabled=True))
    assert next(f for f in memory.brief("+84900000002", flush=False)["needs"] if f["key"] == "income")["value"] == "100000000"
    with db.transaction() as connection:
        memory.enqueue(connection, "new-income", "minh", "current-web", "web", "Lương mới được tăng lên 120 triệu rồi")
    memory.flush("minh")
    brief = memory.brief("0900000002", flush=False)
    needs = {fact["key"]: fact for fact in brief["needs"]}
    assert needs["income"]["value"] == "120000000"
    assert needs["income"]["source_ref"] == "new-income" and needs["income"]["source_origin"] == "runtime"
    assert {key: needs[key]["value"] for key in ("amount", "term", "debt", "property_value")} == {
        "amount": "4000000000", "term": "300", "debt": "10000000", "property_value": "6000000000",
    }
    assert not brief["conflicts"] and brief["name_source"] == "demo_profile"
    assert memory.brief("minh", flush=False) == brief


def test_confirmation_of_whole_new_request_covers_its_comma_separated_fields():
    db = Database(":memory:")
    memory = Memory(db)
    remember(db, memory, "old", "telegram", "Vay mua nhà 1 tỷ trong 10 năm, thu nhập 47 triệu, nợ 5 triệu, giá nhà 2 tỷ")
    remember(db, memory, "new", "web", "Số điện thoại của tôi là 0900000001. Tôi xác nhận nhu cầu mới vay mua nhà 2 tỷ "
             "trong 20 năm, thu nhập 45 triệu, không có nợ, giá nhà 3 tỷ")
    facts = db.all("SELECT key,value,source_ref FROM facts WHERE status='active'")
    assert {fact["key"]: fact["value"] for fact in facts} == {
        "product": "home_loan", "amount": "2000000000", "term": "240", "income": "45000000",
        "debt": "0", "property_value": "3000000000",
    }
    assert all(fact["source_ref"] == "new" for fact in facts)
    assert not db.all("SELECT * FROM facts WHERE status='conflicted'")
