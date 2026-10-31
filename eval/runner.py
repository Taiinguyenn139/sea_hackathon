"""Run isolated scenarios via HTTP and atomically publish measured results."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from eval.kpis import Counters, handoff_score, score_reply
from eval.memory_contract import MemoryContract, score_memory
from eval.oracle import Oracle, content_hash, read_document
from eval.provenance import audit
from simulators.customer import Customer
from simulators.schemas import Scenario

MODES = ("no_memory", "full_history", "harness")
ROOT = Path(__file__).resolve().parent.parent


class Api:
    def __init__(self, base_url: str, admin_token: str):
        parsed = urllib.parse.urlparse(base_url)
        if parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("Reset-capable runner only accepts an isolated localhost backend")
        self.base_url = base_url.rstrip("/")
        self.admin_token = admin_token

    def call(self, path: str, data: dict | None = None, token: str | None = None) -> dict:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        elif path.startswith("/admin/"):
            headers["Authorization"] = "Bearer " + self.admin_token
        body = None if data is None else json.dumps(data).encode()
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, body, headers)
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            # No tokens, request body or raw server traceback in persisted output.
            raise RuntimeError(f"HTTP {error.code}: {path}") from None


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, suffix=".tmp", delete=False) as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
        temp_path = Path(stream.name)
    os.replace(temp_path, path)


def load_scenarios(directory: Path) -> list[dict]:
    files = sorted(p for p in directory.iterdir() if p.suffix in (".json", ".yaml", ".yml"))
    scenarios = [read_document(path) for path in files]
    if not scenarios or len({s["scenario_id"] for s in scenarios}) != len(scenarios):
        raise ValueError("Scenarios must be nonempty with unique scenario_id")
    for scenario in scenarios:
        Scenario.model_validate(scenario)
    return scenarios


def load_memory_contracts(directory: Path, scenarios: list[dict]) -> dict[str, MemoryContract]:
    contracts = {}
    if not directory.is_dir():
        raise ValueError("Memory oracle directory does not exist")
    by_id = {scenario["scenario_id"]: scenario for scenario in scenarios}
    for path in sorted(directory.glob("*.json")):
        contract = MemoryContract.model_validate(read_document(path))
        if contract.scenario_id not in by_id:
            raise ValueError(f"Memory oracle has no selected scenario: {contract.scenario_id}")
        if contract.scenario_id in contracts:
            raise ValueError("Duplicate memory oracle scenario")
        contract.validate_scenario(by_id[contract.scenario_id])
        contracts[contract.scenario_id] = contract
    if not contracts:
        raise ValueError("Memory oracle directory contains no contracts")
    return contracts


def validate_holdout(directory: Path, manifest_path: Path, oracle_hash: str) -> dict:
    if directory.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("Held-out scenarios must be supplied from outside the repository")
    manifest = read_document(manifest_path)
    for key in (
        "approved_by",
        "frozen_at",
        "code_revision",
        "config_hash",
        "playbook_hash",
        "scenario_hash",
        "oracle_hash",
        "fixture_hash",
    ):
        if not manifest.get(key):
            raise ValueError(f"Frozen manifest missing {key}")
    if manifest["scenario_hash"] != content_hash(directory) or manifest["oracle_hash"] != oracle_hash:
        raise ValueError("Held-out/oracle hash differs from frozen manifest")
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=normal"], cwd=ROOT, text=True
        )
    except subprocess.CalledProcessError as error:
        raise ValueError("Held-out requires a frozen git revision") from error
    if head != manifest["code_revision"] or dirty.strip():
        raise ValueError("Code revision changed or working tree is not frozen")
    return manifest


def run_case(
    api: Api, scenario: dict, mode: str, repetition: int, seed: int, oracle: Oracle, playbook: str | None = None,
    memory_contract: MemoryContract | None = None,
) -> dict:
    customer = Customer(scenario, seed)
    counts = Counters(scenarios=1)
    records: list[dict] = []
    known_slots: set[str] = set()
    memory_checks: list[dict] = []
    turn_ids: dict[tuple[int, int], str] = {}
    tool_receipts: dict[str, dict] = {}
    if memory_contract:
        memory_contract.validate_scenario(scenario)
    status = "completed"
    try:
        api.call("/admin/reset", {})
        if playbook is not None:
            api.call("/admin/playbook", {"version": playbook})
        for call in scenario["sessions"]:
            customer.calls += 1
            as_of, channel = call.get("as_of", "2026-10-31"), call.get("channel", "web")
            api.call("/admin/clock", {"as_of": as_of})
            session = api.call(
                "/admin/sessions", {"mode": mode, "demo_profile": scenario.get("profile", "hoa"), "channel": channel}
            )
            sid, token = session["session_id"], session["session_token"]
            if call.get("verify", True):
                phone = session.get("user_id")
                if not phone:
                    profile = scenario.get("profile", "hoa")
                    directory = api.call("/demo/profiles").get("profiles", [])
                    phone = next(
                        (row.get("phone", row["id"]) for row in directory
                         if row["id"] == profile or row.get("runtime_profile") == profile),
                        None,
                    )
                if not phone:
                    raise ValueError("Verified scenario has no phone identity in the public demo directory")
                api.call(f"/sessions/{sid}/verify", {"phone": phone}, token)
            for index, step in enumerate(call["steps"]):
                if step.get("fault"):
                    api.call("/admin/faults", step["fault"])
                text = customer.message(step)
                message_id = f"{scenario['scenario_id']}-{customer.calls}-{index}"
                reply = api.call(f"/sessions/{sid}/messages", {"text": text, "client_message_id": message_id}, token)
                if step.get("duplicate"):
                    duplicate = api.call(
                        f"/sessions/{sid}/messages", {"text": text, "client_message_id": message_id}, token
                    )
                    if duplicate.get("reply") != reply.get("reply"):
                        raise AssertionError("Duplicate message returned a different result")
                known_slots.update(step.get("reveals", []))
                allowed = oracle.allowed_claims(
                    scenario["product"], as_of, channel, scenario.get("customer_claims", [])
                )
                expected_values = oracle.expected_values(
                    scenario["product"], as_of, channel, scenario.get("loan"), card_income=scenario.get("card_income")
                )
                allowed.update(
                    ("VND", value)
                    for key, value in expected_values.items()
                    if key.startswith("installment_") or key in ("total_payment", "card_limit_max")
                )
                metrics, issues = score_reply(reply.get("reply", ""), step.get("expect", {}), allowed, known_slots)
                if call.get("verify", True):
                    traces = api.call(f"/traces/{sid}", token=api.admin_token)
                    if isinstance(traces, dict):
                        traces = traces.get("items", traces.get("traces", []))
                    trace = traces[-1] if traces else {}
                    if trace.get("turn_id"):
                        turn_ids[(customer.calls, index)] = trace["turn_id"]
                    for receipt in trace.get("tool_calls", []):
                        if receipt.get("id"):
                            tool_receipts[receipt["id"]] = receipt
                    source_issues = audit(
                        reply.get("reply", ""),
                        trace,
                        expected_values,
                        as_of,
                        channel,
                        scenario.get("customer_claims", []),
                    )
                    metrics.traced_replies += 1
                    metrics.provenance_errors += int(bool(source_issues))
                    issues.extend(source_issues)
                counts.add(metrics)
                customer.observe(reply.get("reply", ""), step, not metrics.price_errors and not metrics.missing_offers)
                records.append(
                    {
                        "session_number": customer.calls,
                        "step": index,
                        "channel": channel,
                        "as_of": as_of,
                        "input": text,
                        "reply": reply.get("reply", ""),
                        "issues": issues,
                        "used_fact_ids": reply.get("used_fact_ids", []),
                        "guard_events": reply.get("guard_events", []),
                    }
                )
                if step.get("handoff"):
                    handoff = api.call(f"/sessions/{sid}/handoff", {"reason": "customer_request"}, token)
                    summary = handoff.get("handoff_summary", handoff)
                    counts.add(handoff_score(summary, step.get("handoff_required", ["reason", "next_step"])))
                    completion = {
                        "notes": "Đã nhận đủ thông tin trong kịch bản đánh giá.",
                        "commitments": [],
                        "idempotency_key": message_id + "-complete",
                    }
                    receipt = api.call(f"/handoffs/{handoff['handoff_id']}/complete", completion, api.admin_token)
                    retry_receipt = api.call(f"/handoffs/{handoff['handoff_id']}/complete", completion, api.admin_token)
                    if receipt != retry_receipt:
                        raise AssertionError("Duplicate handoff completion changed result")
            api.call(f"/sessions/{sid}/end", {}, token)
            if memory_contract and mode in memory_contract.modes:
                checkpoint = next(
                    (cp for cp in memory_contract.checkpoints if cp.session_number == customer.calls), None
                )
                if checkpoint:
                    brief = api.call(f"/customers/{memory_contract.customer_id}/brief", token=api.admin_token)
                    issues = score_memory(brief, checkpoint, memory_contract.customer_id, turn_ids, tool_receipts)
                    memory_checks.append({
                        "session_number": customer.calls, "as_of": as_of,
                        "status": "failed" if issues else "passed", "issues": issues,
                    })
                    counts.failures += int(bool(issues))
        counts.calls = customer.calls
        counts.converted = int(customer.converted)
    except (RuntimeError, KeyError, ValueError, AssertionError, urllib.error.URLError) as error:
        status = "failed"
        counts.failures += 1
        records.append({"error": str(error)})
    counts.calls = customer.calls
    counts.converted = int(customer.converted)
    return {
        "scenario_id": scenario["scenario_id"],
        "mode": mode,
        "repetition": repetition,
        "seed": seed,
        "status": status,
        "counters": asdict(counts),
        "kpis": counts.values(),
        "turns": records,
        "memory_checks": memory_checks,
    }


def summarize(results: list[dict], expected: int) -> tuple[str, dict, dict]:
    counters = {mode: Counters() for mode in MODES}
    complete = len(results) == expected and all(row["status"] == "completed" for row in results)
    keys = {(row["scenario_id"], row["mode"], row["repetition"]) for row in results}
    complete = complete and len(keys) == expected and {row["mode"] for row in results} == set(MODES)
    for result in results:
        counters[result["mode"]].add(Counters(**result["counters"]))
    return (
        "completed" if complete else "incomplete",
        {mode: c.values() for mode, c in counters.items()},
        {mode: asdict(c) for mode, c in counters.items()},
    )


def publish(output: Path, run: dict) -> None:
    atomic_json(output / f"{run['run_id']}.json", run)
    index_path = output / "index.json"
    index = read_document(index_path) if index_path.exists() else {"schema_version": 1, "runs": []}
    row = {
        key: run[key]
        for key in ("run_id", "status", "split", "created_at", "backend", "seed", "repetitions", "summary")
    }
    index["runs"] = [row] + [old for old in index["runs"] if old["run_id"] != run["run_id"]]
    atomic_json(index_path, index)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--backend", choices=("rules", "llm"), default="rules")
    parser.add_argument("--split", choices=("dev", "heldout"), default="dev")
    parser.add_argument("--scenario-dir", "--heldout-path", type=Path)
    parser.add_argument("--frozen-manifest", type=Path)
    parser.add_argument("--oracle", type=Path, default=ROOT / "eval/oracle_fixtures/bank.json")
    parser.add_argument("--memory-oracles", type=Path, help="Optional directory of evaluator-only memory contracts")
    parser.add_argument("--output", type=Path, default=ROOT / "eval/results")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--judge", action="store_true")
    parser.add_argument("--human-labels", type=Path)
    parser.add_argument("--changes", type=Path)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args(argv)
    if args.repetitions < 1:
        parser.error("repetitions must be positive")
    token = os.environ.get("ADMIN_TOKEN")
    if not token:
        parser.error("ADMIN_TOKEN is required; value is never printed")
    oracle = Oracle(args.oracle)
    directory = args.scenario_dir or ROOT / "simulators/scenarios/dev"
    frozen = None
    if args.split == "heldout" or args.frozen_manifest:
        if not args.scenario_dir or not args.frozen_manifest:
            parser.error("held-out requires --scenario-dir and --frozen-manifest")
        frozen = validate_holdout(directory, args.frozen_manifest, oracle.hash)
        args.split = "heldout"
    scenarios = load_scenarios(directory)
    memory_contracts = load_memory_contracts(args.memory_oracles, scenarios) if args.memory_oracles else {}
    memory_hash = content_hash(args.memory_oracles) if args.memory_oracles else None
    if frozen and memory_contracts and frozen.get("memory_oracle_hash") != memory_hash:
        parser.error("Held-out memory checks require a matching memory_oracle_hash in the frozen manifest")
    api = Api(args.base_url, token)
    state = api.call("/admin/state")
    if frozen:
        from eval.freeze import verify_runtime

        verify_runtime(frozen, state)
    actual_backend = state.get("backend", state.get("llm_backend"))
    if ("llm" if actual_backend == "openai" else actual_backend) != args.backend:
        parser.error(f"Backend metadata mismatch: requested {args.backend}, server reports {actual_backend}")
    results = [
        run_case(
            api, scenario, mode, repetition, args.seed + repetition, oracle, state.get("playbook"),
            memory_contracts.get(scenario["scenario_id"]),
        )
        for scenario in scenarios
        for repetition in range(args.repetitions)
        for mode in MODES
    ]
    status, summary, counters = summarize(results, len(scenarios) * args.repetitions * len(MODES))
    now = datetime.now(UTC)
    run_id = now.strftime("run-%Y%m%d-%H%M%S-") + hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:6]
    harness = counters["harness"]
    gates = {
        "price_errors_zero": harness["price_errors"] == 0 and harness["numeric_claims"] > 0,
        "handoff_complete": harness["handoff_required"] > 0
        and harness["handoff_present"] == harness["handoff_required"],
        "no_missing_offer": harness["expected_offers"] > 0 and harness["missing_offers"] == 0,
        "run_complete": status == "completed",
        "provenance_valid": harness["traced_replies"] > 0 and harness["provenance_errors"] == 0,
        "behavior_checks": harness["failures"] == 0,
    }
    run = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": now.isoformat(),
        "status": status,
        "split": args.split,
        "backend": args.backend,
        "seed": args.seed,
        "repetitions": args.repetitions,
        "results": results,
        "summary": summary,
        "counters": counters,
        "gates": gates,
        "metadata": {
            "oracle_hash": oracle.hash,
            "scenario_hash": content_hash(directory),
            "memory_oracle_hash": memory_hash,
            "memory_oracle_scenarios": sorted(memory_contracts),
            "frozen_manifest": frozen,
            "server": {
                key: state.get(key) for key in ("backend", "model", "playbook", "fixture_hash", "effective_config")
            },
            "expected_results": len(scenarios) * args.repetitions * 3,
        },
        "judge": {"status": "skipped", "reason": "Optional judge not requested; deterministic KPIs only"},
    }
    if args.judge:
        from eval.judge import agreement, judge

        run["judge"] = judge([turn for result in results for turn in result["turns"]], state.get("model"))
        if args.human_labels and run["judge"]["status"] == "completed":
            labels = read_document(args.human_labels)
            keys = sorted(run["judge"]["scores"])
            run["judge"]["agreement"] = agreement(
                [run["judge"]["scores"][key] for key in keys], [labels[key] for key in keys]
            )
    if args.changes or args.baseline:
        if not args.changes or not args.baseline:
            parser.error("Regression requires both --changes and --baseline")
        from eval.changes import regression, validate_changes

        changes = read_document(args.changes)
        validate_changes(changes)
        run["changes"] = changes
        run["regressions"] = regression(read_document(args.baseline), run)
        gates["no_regression"] = not run["regressions"]
    publish(args.output, run)
    print(
        json.dumps(
            {"run_id": run_id, "status": status, "gates": gates, "summary": summary}, ensure_ascii=False, indent=2
        )
    )
    return 0 if all(gates.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
