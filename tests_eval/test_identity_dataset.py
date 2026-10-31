"""Check the synthetic corpus as relational data, not runtime credentials."""

import json
import shutil
import subprocess
import sys
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest

from simulators.identity_dataset import (
    DEFAULT_DATASET_DIR,
    DEFAULT_EXPECTATIONS_DIR,
    load_dataset,
    load_expectations,
    normalize_phone,
    reference_transcript,
)

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, document):
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@pytest.fixture
def dataset():
    return load_dataset()


@pytest.fixture
def expectations(dataset):
    return load_expectations(dataset)


@pytest.fixture
def copied_data(tmp_path):
    directory = tmp_path / "inputs"
    shutil.copytree(DEFAULT_DATASET_DIR, directory)
    return directory


@pytest.fixture
def copied_expectations(tmp_path):
    directory = tmp_path / "expected"
    shutil.copytree(DEFAULT_EXPECTATIONS_DIR, directory)
    return directory


def journey_paths(directory):
    return [directory / name for name in read_json(directory / "manifest.json")["journey_files"]]


def expectation_path(directory, user_id):
    return next(path for path in directory.rglob("*.json") if read_json(path).get("user_id") == user_id)


def verified_checkpoint(document):
    return next(cp for cp in document["checkpoints"] if cp["customer_memory"])


def unverified_checkpoint(document):
    return next(cp for cp in document["checkpoints"] if not cp["cross_session_memory_allowed"])


def test_phone_identities_have_separate_sessions_and_channel_links(dataset, expectations):
    assert len(dataset.identities) == 10
    assert len({identity.bank_customer_id for identity in dataset.identities.values()}) == 10
    assert set(dataset.identities) == set(dataset.journeys) == set(expectations)
    session_ids, message_ids, external_ids = set(), set(), set()
    for user_id, identity in dataset.identities.items():
        assert user_id == identity.phone == normalize_phone(user_id)
        assert len(user_id) == 10 and user_id.startswith("0")
        sessions = dataset.sessions_for(user_id)
        assert len(sessions) == 4
        assert {session.channel for session in sessions} == {"web", "telegram", "zalo", "hotline"}
        assert {session.identity_status for session in sessions} == {"unverified", "verified"}
        links = {link.channel: link.external_id for link in identity.channel_links}
        assert not (external_ids & set(links.values()))
        external_ids.update(links.values())
        for session in sessions:
            assert session.user_id == user_id
            assert session.session_id not in session_ids
            session_ids.add(session.session_id)
            assert session.external_id == links[session.channel]
            assert session.logged_in is False
            assert len(session.messages) == 3
            for message in session.messages:
                assert message.message_id not in message_ids
                message_ids.add(message.message_id)
                assert (message.user_id, message.session_id, message.channel) == (
                    user_id, session.session_id, session.channel,
                )
        assert len(dataset.messages_for(user_id)) == 12
        assert len(expectations[user_id].checkpoints) == 4
        assert set(dataset.journeys[user_id].themes) == {"family_decision", "bank_comparison", "cross_channel"}
    assert len(session_ids) == 40
    assert len(message_ids) == 120
    assert {row.runtime_profile for row in dataset.identities.values() if row.runtime_profile} == {"hoa", "minh"}


@pytest.mark.parametrize("value", ["0900000001", "090 000-0001", "+84900000001", "+84 90 000 0001"])
def test_phone_normalization_preserves_trunk_zero(value):
    assert normalize_phone(value) == "0900000001"


@pytest.mark.parametrize("value", [
    900000001, None, True, "", "090000001", "09000000001", "+840900000001", "+1900000001",
    "+84900000001ext2", "090A000001",
])
def test_non_phone_or_numeric_identity_is_rejected(value):
    with pytest.raises((TypeError, ValueError)):
        normalize_phone(value)


def test_offer_selection_uses_date_channel_product_and_segment(dataset):
    offers = dataset.active_offers("home_loan", "web", date(2026, 10, 31), segment="mass")
    assert offers
    assert any(offer.promo_rate == 7.1 for offer in offers)
    for offer in offers:
        assert offer.product == "home_loan" and offer.segment == "mass"
        assert "web" in offer.channels
        assert offer.valid_from <= date(2026, 10, 31) <= offer.valid_until
        assert offer.source_type == "synthetic_fixture"
        assert offer.rate_unit == "percent_per_year"
        assert offer.fee_note
    assert dataset.active_offers("home_loan", "web", date(2030, 1, 1), segment="mass") == []


@pytest.mark.parametrize("mutation", [
    "identity_number", "identity_phone", "duplicate_user", "duplicate_external_id", "unknown_bank",
    "duplicate_offer", "journey_user", "session_user", "duplicate_session", "external_id", "message_user",
    "message_session", "message_channel", "duplicate_message", "naive_time", "message_before_start",
    "future_message", "verification_after_message", "message_order", "session_order", "logged_in",
])
def test_input_loader_rejects_broken_relations_or_time(copied_data, mutation):
    load_dataset(copied_data)
    identity_path, offer_path = copied_data / "identities.json", copied_data / "offers.json"
    identities, offers = read_json(identity_path), read_json(offer_path)
    path = journey_paths(copied_data)[0]
    journey = read_json(path)
    first, second = journey["sessions"][:2]
    message = first["messages"][0]
    if mutation == "identity_number":
        identities["identities"][0]["user_id"] = 900000001
    elif mutation == "identity_phone":
        identities["identities"][0]["phone"] = "0900000099"
    elif mutation == "duplicate_user":
        identities["identities"].append(deepcopy(identities["identities"][0]))
    elif mutation == "duplicate_external_id":
        identities["identities"][1]["channel_links"][0]["external_id"] = (
            identities["identities"][0]["channel_links"][0]["external_id"]
        )
    elif mutation == "unknown_bank":
        offers["offers"][0]["bank_id"] = "bank-that-is-not-in-the-directory"
    elif mutation == "duplicate_offer":
        offers["offers"].append(deepcopy(offers["offers"][0]))
    elif mutation == "journey_user":
        journey["user_id"] = "0900000099"
    elif mutation == "session_user":
        first["user_id"] = identities["identities"][1]["user_id"]
    elif mutation == "duplicate_session":
        second["session_id"] = first["session_id"]
    elif mutation == "external_id":
        first["external_id"] = "synthetic:unlinked-device"
    elif mutation == "message_user":
        message["user_id"] = identities["identities"][1]["user_id"]
    elif mutation == "message_session":
        message["session_id"] = second["session_id"]
    elif mutation == "message_channel":
        message["channel"] = "telegram" if first["channel"] != "telegram" else "web"
    elif mutation == "duplicate_message":
        second["messages"][0]["message_id"] = message["message_id"]
    elif mutation == "naive_time":
        message["at"] = "2026-09-20T09:00:00"
    elif mutation == "message_before_start":
        message["at"] = "2000-01-01T09:00:00+07:00"
    elif mutation == "future_message":
        message["at"] = "2030-01-01T09:00:00+07:00"
    elif mutation == "verification_after_message":
        verified = next(session for session in journey["sessions"] if session["identity_status"] == "verified")
        verified["verified_at"] = "2030-01-01T09:00:00+07:00"
    elif mutation == "message_order":
        first["messages"].reverse()
    elif mutation == "session_order":
        journey["sessions"].reverse()
    else:
        first["logged_in"] = True
    write_json(identity_path, identities)
    write_json(offer_path, offers)
    write_json(path, journey)
    with pytest.raises(ValueError):
        load_dataset(copied_data)


def test_oracle_covers_each_message_without_granting_phone_auth(dataset, expectations):
    response_ids = set()
    for user_id, expected in expectations.items():
        sessions = {session.session_id: session for session in dataset.sessions_for(user_id)}
        messages = {message.message_id: message for message in dataset.messages_for(user_id)}
        prior_memory = []
        for checkpoint in expected.checkpoints:
            session = sessions[checkpoint.session_id]
            assert checkpoint.crm_allowed is False
            if session.identity_status == "unverified":
                assert not checkpoint.cross_session_memory_allowed
                assert checkpoint.visible_memory_keys == []
                assert checkpoint.customer_memory == prior_memory
            else:
                prior_memory = checkpoint.customer_memory
            for memory in checkpoint.customer_memory:
                assert sessions[messages[memory.source_message_id].session_id].identity_status == "verified"
            assert {response.in_reply_to for response in checkpoint.responses} == {
                message.message_id for message in session.messages
            }
            for quote in checkpoint.quotes:
                offer = dataset.offers[quote.offer_id]
                source = messages[quote.source_message_id]
                assert sessions[source.session_id].identity_status == "verified"
                assert source.channel in offer.channels
                assert quote.quoted_at <= checkpoint.as_of
                assert quote.status == ("expired" if offer.valid_until < checkpoint.as_of else "active")
            for response in checkpoint.responses:
                assert response.message_id not in response_ids
                response_ids.add(response.message_id)
                assert response.at >= messages[response.in_reply_to].at
                if session.identity_status == "unverified":
                    assert not ({"send_summary", "schedule_callback"} & set(response.suggested_tools))
                for offer_id in response.offer_ids:
                    offer = dataset.offers[offer_id]
                    assert offer.valid_from <= checkpoint.as_of <= offer.valid_until
                    assert session.channel in offer.channels
    assert len(response_ids) == 120


def test_inputs_do_not_embed_evaluator_answers():
    for path in DEFAULT_DATASET_DIR.rglob("*.json"):
        document = path.read_text(encoding="utf-8")
        assert '"reference_text"' not in document
        assert '"customer_memory"' not in document
        assert '"role": "assistant"' not in document


@pytest.mark.parametrize("mutation", [
    "unknown_session", "checkpoint_date", "foreign_memory_source", "unknown_memory_source", "future_memory_source",
    "unverified_memory_source", "duplicate_active_key", "unverified_private_memory", "unverified_state_write",
    "unverified_private_tool",
    "unknown_reply", "foreign_reply", "reply_channel", "reply_user", "future_reply", "expired_offer",
    "wrong_channel_offer", "invalid_quote_source", "wrong_quote_status",
])
def test_oracle_rejects_leaks_and_unsupported_references(dataset, copied_expectations, mutation):
    load_expectations(dataset, copied_expectations)
    user_id, other_id = list(dataset.identities)[:2]
    path = expectation_path(copied_expectations, user_id)
    document = read_json(path)
    checkpoint = verified_checkpoint(document)
    unverified = unverified_checkpoint(document)
    response = checkpoint["responses"][0]
    source = checkpoint["customer_memory"][0]
    if mutation == "unknown_session":
        checkpoint["session_id"] = "session-that-does-not-exist"
    elif mutation == "checkpoint_date":
        checkpoint["as_of"] = "2030-01-01"
    elif mutation == "foreign_memory_source":
        source["source_message_id"] = dataset.messages_for(other_id)[0].message_id
    elif mutation == "unknown_memory_source":
        source["source_message_id"] = "message-that-does-not-exist"
    elif mutation == "future_memory_source":
        source["source_message_id"] = dataset.messages_for(user_id)[-1].message_id
    elif mutation == "unverified_memory_source":
        session = next(s for s in dataset.sessions_for(user_id) if s.identity_status == "unverified")
        latest = next(cp for cp in reversed(document["checkpoints"]) if cp["cross_session_memory_allowed"])
        latest["customer_memory"][0]["source_message_id"] = session.messages[0].message_id
    elif mutation == "duplicate_active_key":
        active = next(row for row in checkpoint["customer_memory"] if row["status"] == "active")
        checkpoint["customer_memory"].append(deepcopy(active))
    elif mutation == "unverified_private_memory":
        unverified["visible_memory_keys"] = [source["key"]]
    elif mutation == "unverified_state_write":
        unverified["customer_memory"][0]["value"] = "tampered-without-a-verified-write"
    elif mutation == "unverified_private_tool":
        unverified["responses"][0]["suggested_tools"].append("send_summary")
    elif mutation == "unknown_reply":
        response["in_reply_to"] = "message-that-does-not-exist"
    elif mutation == "foreign_reply":
        response["in_reply_to"] = dataset.messages_for(other_id)[0].message_id
    elif mutation == "reply_channel":
        response["channel"] = "telegram" if response["channel"] != "telegram" else "web"
    elif mutation == "reply_user":
        response["user_id"] = other_id
    elif mutation == "future_reply":
        response["at"] = "2030-01-01T09:00:00+07:00"
    elif mutation == "expired_offer":
        checkpoint = next(cp for cp in document["checkpoints"] if any(
            offer.valid_until < date.fromisoformat(cp["as_of"]) for offer in dataset.offers.values()
        ))
        expired = next(offer for offer in dataset.offers.values()
                       if offer.valid_until < date.fromisoformat(checkpoint["as_of"]))
        checkpoint["responses"][0]["offer_ids"] = [expired.offer_id]
    elif mutation == "wrong_channel_offer":
        sessions = {session.session_id: session for session in dataset.sessions_for(user_id)}
        checkpoint, offer = next((cp, offer) for cp in document["checkpoints"] for offer in dataset.offers.values()
                                 if sessions[cp["session_id"]].channel not in offer.channels
                                 and offer.valid_from <= date.fromisoformat(cp["as_of"]) <= offer.valid_until)
        checkpoint["responses"][0]["offer_ids"] = [offer.offer_id]
    elif mutation == "invalid_quote_source":
        quote = next(quote for cp in document["checkpoints"] for quote in cp["quotes"])
        quote["source_message_id"] = "message-that-does-not-exist"
    else:
        quote = next(quote for cp in document["checkpoints"] for quote in cp["quotes"] if quote["status"] == "expired")
        quote["status"] = "active"
    write_json(path, document)
    with pytest.raises(ValueError):
        load_expectations(dataset, copied_expectations)


def test_transcript_is_explicit_reference_pairs_for_one_user(dataset, expectations):
    user_id = next(iter(dataset.identities))
    transcript = reference_transcript(dataset, expectations, user_id)
    assert len(transcript) == 24
    for user, assistant in zip(transcript[::2], transcript[1::2], strict=True):
        assert user["role"] == "user" and assistant["role"] == "assistant"
        assert user["user_id"] == assistant["user_id"] == user_id
        assert assistant["reference"] is True
        assert assistant["in_reply_to"] == user["message_id"]
        assert assistant["session_id"] == user["session_id"]


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "simulators.identity_dataset", *args],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )


def test_cli_defaults_to_input_only_and_reference_requires_opt_in(expectations):
    default = run_cli()
    assert default.returncode == 0, default.stderr
    json.loads(default.stdout)
    assert "reference_text" not in default.stdout and "customer_memory" not in default.stdout
    sample = next(iter(expectations.values())).checkpoints[0].responses[0].reference_text
    assert sample not in default.stdout
    invalid = run_cli("--transcript")
    assert invalid.returncode != 0
    invalid = run_cli("--user-id", "0900000001", "--transcript")
    assert invalid.returncode != 0
    replay = run_cli("--user-id", "0900000001")
    assert replay.returncode == 0, replay.stderr
    assert "0900000001" in replay.stdout and "0900000002" not in replay.stdout
    assert "reference_text" not in replay.stdout and "customer_memory" not in replay.stdout
    reference = run_cli("--check-expectations", "--user-id", "0900000001", "--transcript")
    assert reference.returncode == 0, reference.stderr
    json.loads(reference.stdout)
    assert '"reference": true' in reference.stdout
    assert "0900000002" not in reference.stdout


def test_input_cli_does_not_require_or_read_oracle_directory(tmp_path):
    unavailable = str(tmp_path / "oracle-is-not-part-of-agent-inputs")
    replay = run_cli("--expectations-dir", unavailable, "--user-id", "0900000001")
    assert replay.returncode == 0, replay.stderr
    assert json.loads(replay.stdout)["record_type"] == "user_replay"
    checked = run_cli("--expectations-dir", unavailable, "--check-expectations")
    assert checked.returncode != 0
