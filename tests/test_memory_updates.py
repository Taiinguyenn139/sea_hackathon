"""An update to one field must not confirm hypothetical or unrelated values."""

import pytest

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
