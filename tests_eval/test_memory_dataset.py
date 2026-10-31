import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from eval.memory_contract import Checkpoint, MemoryContract, score_memory
from eval.oracle import Oracle
from eval.runner import load_memory_contracts, load_scenarios, run_case
from simulators.schemas import Scenario

ROOT = Path(__file__).resolve().parents[1]


def documents():
    scenario = json.loads((ROOT / "simulators/scenarios/dev/mortgage-memory-journey.json").read_text(encoding="utf-8"))
    expected = json.loads(
        (ROOT / "eval/oracle_fixtures/memory/mortgage-memory-journey.json").read_text(encoding="utf-8")
    )
    return scenario, expected


def sample_checkpoint():
    return Checkpoint.model_validate({
        "session_number": 1, "as_of": "2026-09-27", "conflict_keys": [], "unique_active_keys": ["income"],
        "facts": [{
            "key": "income", "value": "45000000", "unit": "đồng", "source_type": "customer",
            "status": "active", "channel": "web", "observed_at": "2026-09-27", "source_step": [1, 0],
        }],
    })


def sample_brief():
    fact = sample_checkpoint().facts[0].model_dump(mode="json", exclude={"source_step"}, exclude_none=True)
    return {
        "customer_id": "hoa", "redacted": False, "conflicts": [], "warnings": [],
        "facts": [{**fact, "id": "runtime_fact", "customer_id": "hoa", "source_ref": "runtime_turn"}],
    }


def test_all_dev_scenarios_obey_schema():
    assert len(load_scenarios(ROOT / "simulators/scenarios/dev")) >= 10


def test_memory_contract_matches_scenario_and_current_bank():
    scenario, expected = documents()
    Scenario.model_validate(scenario)
    contract = MemoryContract.model_validate(expected)
    contract.validate_scenario(scenario)
    bank = yaml.safe_load((ROOT / "bank_tools/fixtures/bank.yaml").read_text(encoding="utf-8"))
    profile = next(row for row in bank["customers"] if row["id"] == scenario["profile"])
    assert profile["dob"] == scenario["dob"]
    oracle = Oracle(ROOT / "eval/oracle_fixtures/bank.json")
    for cp in contract.checkpoints:
        for fact in cp.facts:
            if fact.key != "promo_rate":
                continue
            # Validate the curated oracle against policy files, never SUT responses.
            matches = [p for p in bank["promotions"] if p["product"] == scenario["product"]
                       and p["valid_from"] <= fact.observed_at.isoformat() <= p["valid_until"]
                       and fact.channel in p["channels"]]
            assert len(matches) == 1
            assert matches[0]["rate"] == fact.value
            assert matches[0]["valid_until"] == fact.valid_until.isoformat()
            values = oracle.expected_values(scenario["product"], fact.observed_at.isoformat(), fact.channel)
            assert str(values["promo_rate"]) == fact.value


@pytest.mark.parametrize("field,value", [("channel", "hotline"), ("as_of", "not-a-date")])
def test_old_channel_and_invalid_date_rejected(field, value):
    scenario, _ = documents()
    scenario["sessions"][0][field] = value
    with pytest.raises(ValidationError):
        Scenario.model_validate(scenario)


def test_unrecognized_scenario_field_rejected():
    scenario, _ = documents()
    scenario["expected_memory"] = {}
    with pytest.raises(ValidationError):
        Scenario.model_validate(scenario)


def test_future_oracle_fact_rejected():
    _, expected = documents()
    expected["checkpoints"][0]["facts"][0]["observed_at"] = "2026-10-31"
    with pytest.raises(ValidationError):
        MemoryContract.model_validate(expected)


def test_missing_source_step_rejected():
    scenario, expected = documents()
    expected["checkpoints"][0]["facts"][0]["source_step"] = [1, 99]
    with pytest.raises(ValueError, match="missing source step"):
        MemoryContract.model_validate(expected).validate_scenario(scenario)


def test_contract_loader_records_only_matching_scenarios():
    scenarios = load_scenarios(ROOT / "simulators/scenarios/dev")
    contracts = load_memory_contracts(ROOT / "eval/oracle_fixtures/memory", scenarios)
    assert set(contracts) == {"mortgage-memory-journey"}
    assert len(contracts["mortgage-memory-journey"].checkpoints) == 3


def test_memory_matches_dynamic_ids_and_public_source():
    assert score_memory(sample_brief(), sample_checkpoint(), "hoa", {(1, 0): "runtime_turn"}, {}) == []


@pytest.mark.parametrize("mutation", ["wrong_source", "wrong_customer", "wrong_value", "duplicate", "future"])
def test_memory_mutations_are_detected(mutation):
    brief = sample_brief()
    fact = brief["facts"][0]
    if mutation == "wrong_source":
        fact["source_ref"] = "invented_turn"
    elif mutation == "wrong_customer":
        fact["customer_id"] = "minh"
    elif mutation == "wrong_value":
        fact["value"] = "40000000"
    elif mutation == "duplicate":
        brief["facts"].append(deepcopy(fact))
    else:
        fact["observed_at"] = "2026-10-31"
    assert score_memory(brief, sample_checkpoint(), "hoa", {(1, 0): "runtime_turn"}, {})


def test_bank_fact_requires_matching_public_receipt_and_expiry_warning():
    cp = Checkpoint.model_validate({
        "session_number": 2, "as_of": "2026-10-31", "conflict_keys": [], "unique_active_keys": [],
        "facts": [{
            "key": "promo_rate", "value": "6.5", "unit": "%/năm", "source_type": "bank_tool",
            "status": "expired", "channel": "web", "observed_at": "2026-09-27", "valid_until": "2026-09-30",
        }],
    })
    fact = {**cp.facts[0].model_dump(mode="json", exclude_none=True),
            "id": "bank_fact", "source_ref": "tool_1", "customer_id": "hoa"}
    brief = {"customer_id": "hoa", "redacted": False, "facts": [fact], "conflicts": [], "warnings": [fact]}
    receipt = {"result": {"facts": [{**fact, "fact_id": "bank_fact", "status": "active"}]}}
    assert score_memory(brief, cp, "hoa", {}, {"tool_1": receipt}) == []
    assert score_memory(brief, cp, "hoa", {}, {})
    brief["warnings"] = []
    assert any("missing_warning" in issue for issue in score_memory(brief, cp, "hoa", {}, {"tool_1": receipt}))


def test_runner_reads_checkpoint_after_session_end_and_surfaces_failure():
    class FakeApi:
        admin_token = "test-only"

        def __init__(self):
            self.paths = []

        def call(self, path, data=None, token=None):
            self.paths.append(path)
            if path == "/admin/sessions":
                return {"session_id": "session", "session_token": "test-session"}
            if path.endswith("/messages"):
                return {"reply": "Đã ghi nhận thông tin."}
            if path.startswith("/traces/"):
                return [{"turn_id": "runtime_turn", "tool_calls": [], "facts": [], "source_spans": []}]
            if path == "/customers/hoa/brief":
                return {"redacted": False, "customer_id": "hoa", "facts": [], "conflicts": []}
            return {}

    scenario = {
        "scenario_id": "case", "profile": "hoa", "product": "home_loan",
        "sessions": [{"channel": "web", "as_of": "2026-09-27", "steps": [{"text": "Thu nhập 45 triệu."}]}],
    }
    contract = MemoryContract.model_validate({
        "schema_version": 1, "scenario_id": "case", "customer_id": "hoa", "modes": ["harness"],
        "checkpoints": [sample_checkpoint().model_dump(mode="json")],
    })
    api = FakeApi()
    result = run_case(api, scenario, "harness", 0, 42, Oracle(ROOT / "eval/oracle_fixtures/bank.json"),
                      memory_contract=contract)
    assert api.paths.index("/sessions/session/end") < api.paths.index("/customers/hoa/brief")
    assert result["memory_checks"][0]["status"] == "failed"
    assert result["counters"]["failures"] >= 1
    baseline = FakeApi()
    result = run_case(baseline, scenario, "no_memory", 0, 42, Oracle(ROOT / "eval/oracle_fixtures/bank.json"),
                      memory_contract=contract)
    assert "/customers/hoa/brief" not in baseline.paths
    assert result["memory_checks"] == []
