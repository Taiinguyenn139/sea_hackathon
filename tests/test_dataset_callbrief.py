import json
import shutil
from pathlib import Path

import pytest

from harness.config import Settings
from harness.db import Database
from harness.memory.dataset_history import project_history
from harness.memory.service import Memory, extract
from simulators.identity_dataset import DEFAULT_DATASET_DIR


@pytest.fixture
def inputs(tmp_path):
    directory = tmp_path / "identity-inputs"
    shutil.copytree(DEFAULT_DATASET_DIR, directory)
    return directory


@pytest.fixture
def memory(tmp_path, inputs):
    db = Database(str(tmp_path / "history.sqlite3"))
    return Memory(db, Settings(llm_backend="rules", identity_history_enabled=True), history_directory=inputs)


def active(facts):
    return {fact["key"]: fact["value"] for fact in facts if fact["status"] == "active"}


def write(memory, text, turn="runtime-income", channel="web"):
    with memory.db.transaction() as connection:
        memory.enqueue(connection, turn, "minh", "runtime-session", channel, text)
    memory.flush("minh", allow_llm=False)


def test_an_brief_has_initial_customer_context_and_sources_without_database_seed(memory):
    brief = memory.brief("0900000002", flush=False)
    known = active(brief["facts"])
    assert {key: known[key] for key in ("amount", "term", "income", "debt", "property_value")} == {
        "amount": "4000000000", "term": "300", "income": "100000000",
        "debt": "10000000", "property_value": "6000000000",
    }
    assert brief["customer_id"] == "minh" and brief["user_id"] == "0900000002"
    assert brief["segment"] == "priority" and brief["as_of"] == "2026-10-31"
    assert len(brief["history"]) == 9
    assert {row["channel"] for row in brief["history"]} == {"telegram", "hotline", "web"}
    assert all(row["role"] == "user" and row["synthetic"] for row in brief["history"])
    sources = {row["message_id"]: row for row in brief["history"]}
    for fact in brief["facts"]:
        assert fact["source_type"] == "customer" and fact["source_origin"] == "dataset_history"
        assert fact["source_ref"] in sources
        assert fact["source_at"] == sources[fact["source_ref"]]["at"]
        assert fact["channel"] == sources[fact["source_ref"]]["channel"]
    assert "chuyên viên" in known["family_decision"]
    assert "chưa rõ phí" in known["comparison_pending"]
    assert brief["quotes"] == brief["commitments"] == []
    for table in ("sessions", "turns", "facts", "memory_jobs", "customer_sequences", "callbacks", "outbox"):
        assert memory.db.one(f"SELECT count(*) AS n FROM {table}")["n"] == 0


def test_history_aliases_have_identical_stable_facts_and_do_not_duplicate_reads(memory):
    first = memory.brief("minh", flush=False)
    assert memory.brief("0900000002", flush=False) == first
    assert memory.brief("+84900000002", flush=False) == first
    assert memory.brief("minh", flush=False) == first
    assert len({fact["id"] for fact in first["facts"]}) == len(first["facts"])
    assert len({fact["key"] for fact in first["needs"]}) == len(first["needs"])


@pytest.mark.parametrize("as_of,count", [("2026-09-28", 0), ("2026-09-29", 3), ("2026-09-30", 3),
                                         ("2026-10-02", 6), ("2026-10-31", 9)])
def test_history_cutoff_excludes_future_and_unverified_messages(inputs, as_of, count):
    result = project_history("minh", as_of, extract, directory=inputs)
    assert len(result["history"]) == count
    assert all(row["at"][:10] <= as_of for row in result["history"])
    assert all("-s2-" not in row["message_id"] for row in result["history"])
    assert all(fact["value"] != "99000000" for fact in result["facts"])
    if count:
        assert active(result["facts"])["term"] == "300"


def test_user_history_loader_does_not_read_evaluator_answers(inputs, monkeypatch):
    original = Path.read_text

    def input_only(path, *args, **kwargs):
        assert "oracle_fixtures" not in path.parts
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", input_only)
    result = project_history("0900000002", "2026-10-31", extract, directory=inputs)
    assert len(result["history"]) == 9
    encoded = json.dumps(result, ensure_ascii=False)
    assert "reference_text" not in encoded and "assistant" not in encoded


def test_projection_is_only_for_the_requested_fixture_identity(inputs):
    result = project_history("minh", "2026-10-31", extract, directory=inputs)
    assert all(row["user_id"] == "0900000002" for row in result["history"])
    assert all(row["customer_id"] == "minh" for row in result["facts"])
    assert project_history("guest_unknown", "2026-10-31", extract, directory=inputs)["facts"] == []


@pytest.mark.parametrize("customer", [None, 900000002, "", "+1900000002"])
def test_missing_or_invalid_identity_cannot_select_a_profile_with_no_alias(inputs, customer):
    assert project_history(customer, "2026-10-31", extract, directory=inputs) == {
        "facts": [], "history": [], "pending_topics": [],
    }


def test_history_can_be_disabled_without_changing_registry_or_live_memory(memory):
    memory.settings.identity_history_enabled = False
    assert memory.brief("minh", flush=False)["facts"] == []
    assert memory.history("minh") == []
    write(memory, "Thu nhập 100 triệu")
    assert active(memory.facts("minh"))["income"] == "100000000"


def test_historical_callback_requests_do_not_replay_or_create_live_actions(memory):
    brief = memory.brief("minh", flush=False)
    assert any("07/10/2026" in row["text"] and "14:30" in row["text"] for row in brief["history"])
    assert memory.db.setting("clock") == "2026-10-31"
    assert not memory.db.all("SELECT * FROM callbacks")
    assert not memory.db.all("SELECT * FROM outbox")
    assert not memory.db.all("SELECT * FROM tool_calls")


def test_same_value_live_update_has_one_active_fact_and_no_conflict(memory):
    write(memory, "Thu nhập 100 triệu", channel="web")
    brief = memory.brief("minh", flush=False)
    assert brief["conflicts"] == []
    income = [fact for fact in brief["needs"] if fact["key"] == "income"]
    assert len(income) == 1 and income[0]["source_ref"] == "runtime-income"
    assert active(brief["facts"])["income"] == "100000000"


def test_different_cross_channel_update_requires_confirmation_then_live_value_wins(memory):
    write(memory, "Thu nhập 120 triệu", channel="web")
    brief = memory.brief("minh", flush=False)
    assert {fact["value"] for fact in brief["conflicts"] if fact["key"] == "income"} == {"100000000", "120000000"}
    assert "income" not in active(brief["facts"])
    write(memory, "Xác nhận thu nhập chính xác 120 triệu", turn="confirmed-income", channel="web")
    brief = memory.brief("minh", flush=False)
    assert brief["conflicts"] == []
    assert active(brief["facts"])["income"] == "120000000"
    assert next(fact for fact in brief["needs"] if fact["key"] == "income")["source_ref"] == "confirmed-income"
    sequence = memory.db.one("SELECT next_seq,processed_seq FROM customer_sequences WHERE customer_id='minh'")
    assert sequence == {"next_seq": 2, "processed_seq": 2}
    memory.brief("minh", flush=False)
    assert memory.db.one("SELECT next_seq,processed_seq FROM customer_sequences WHERE customer_id='minh'") == sequence


def test_flush_false_does_not_process_a_pending_runtime_job(memory):
    with memory.db.transaction() as connection:
        memory.enqueue(connection, "pending", "minh", "session", "web", "Xác nhận thu nhập 120 triệu")
    assert active(memory.brief("minh", flush=False)["facts"])["income"] == "100000000"
    assert memory.db.one("SELECT status FROM memory_jobs WHERE turn_id='pending'")["status"] == "pending"
    assert active(memory.brief("minh")["facts"])["income"] == "120000000"


def test_source_history_includes_processed_live_statements_without_unverified_turns(memory):
    with memory.db.transaction() as connection:
        connection.execute(
            "INSERT INTO sessions(id,customer_id,channel,mode,token_hash,verified,created_at) VALUES(?,?,?,?,?,?,?)",
            ("live-session", "minh", "web", "harness", "test-capability-hash", 1, "2026-10-31T09:00:00+07:00"),
        )
        connection.execute(
            "INSERT INTO turns VALUES(?,?,?,?,?,?,?)",
            ("live-turn", "live-session", "live-client-id", "Xác nhận thu nhập 120 triệu", None, None,
             "2026-10-31T09:01:00+07:00"),
        )
        connection.execute(
            "INSERT INTO turns VALUES(?,?,?,?,?,?,?)",
            ("untrusted-turn", "live-session", "untrusted", "Trước khi xác minh tôi khai 999 triệu", None, None,
             "2026-10-31T09:00:30+07:00"),
        )
        memory.enqueue(connection, "live-turn", "minh", "live-session", "web", "Xác nhận thu nhập 120 triệu")
    memory.flush("minh", allow_llm=False)
    history = memory.history("0900000002")
    assert len(history) == 10
    assert history[-1]["source_origin"] == "runtime" and history[-1]["message_id"] == "live-turn"
    assert history[-1]["channel"] == "web" and "120 triệu" in history[-1]["text"]
    assert not any(row["message_id"] == "untrusted-turn" for row in history)


def test_dotted_vnd_and_loan_terms_have_customer_scope(inputs):
    first = json.loads((inputs / "journeys/0900000002.json").read_text())["sessions"][0]["messages"][0]["text"]
    values = extract(first)
    assert values["amount"] == ("4000000000", "đồng")
    assert values["income"] == ("100000000", "đồng")
    assert values["debt"] == ("10000000", "đồng")
    assert values["property_value"] == ("6000000000", "đồng")
    assert values["term"] == ("300", "tháng")
    assert "term" not in extract("Sông Hư Cấu quảng cáo 6,2% trong 6 tháng.")
    assert "amount" not in extract("Mẹ có thể giúp thêm 100 triệu.")
    assert "income" not in extract("Không tự đổi lương thành 15 triệu.")


@pytest.mark.parametrize("text", [
    "Tôi chưa cần hẹn gọi.", "Đừng tự hẹn nhân viên gọi thúc giục.",
    "Chỉ lưu ý muốn tự chủ động liên hệ lại.", "Đừng tạo lịch gọi khi tôi chỉ nói sau này sẽ liên hệ lại.",
])
def test_declining_a_callback_is_not_extracted_as_callback_consent(text):
    assert "commitment" not in extract(text)


@pytest.mark.parametrize("phone,amount", [
    ("0900000001", "1800000000"), ("0900000003", "200000000"), ("0900000004", "900000000"),
    ("0900000005", "2000000000"), ("0900000006", "600000000"), ("0900000007", "400000000"),
    ("0900000008", "4500000000"), ("0900000009", "800000000"), ("0900000010", "150000000"),
])
def test_latest_explicit_customer_budget_keeps_family_support_out_of_income(inputs, phone, amount):
    assert active(project_history(phone, "2026-10-31", extract, directory=inputs)["facts"])["amount"] == amount
