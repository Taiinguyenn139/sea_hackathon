import ast
import json
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from eval.changes import validate_changes
from eval.freeze import canonical_hash, effective_config, verify_runtime
from eval.judge import agreement
from eval.kpis import Counters, handoff_score, score_reply
from eval.oracle import Oracle, installments, numeric_claims
from eval.provenance import audit
from eval.runner import Api, load_scenarios, publish, run_case, summarize, validate_holdout
from simulators.customer import Customer

ROOT = Path(__file__).resolve().parent.parent


class EvaluationTests(unittest.TestCase):
    def test_vietnamese_numeric_normalization(self):
        self.assertEqual(
            numeric_claims("2 tỷ đồng, 6,9%/năm, 20 năm, 500.000 đồng"),
            [("VND", Decimal(2000000000)), ("%", Decimal("6.9")), ("months", Decimal(240)), ("VND", Decimal(500000))],
        )

    def test_oracle_detects_mutated_price_without_trusting_tool(self):
        oracle = Oracle(ROOT / "eval/oracle_fixtures/bank.json")
        allowed = oracle.allowed_claims("home_loan", "2026-10-31", "telegram", [])
        # A mutated SUT can claim any tool receipt; the independent oracle still rejects 6.5.
        counts, issues = score_reply("Lãi ưu đãi 6,5%/năm.", {"offer": True}, allowed, set())
        self.assertEqual(counts.price_errors, 1)
        self.assertTrue(issues)
        self.assertEqual(score_reply("Lãi ưu đãi 6,9%/năm.", {"offer": True}, allowed, set())[0].price_errors, 0)

    def test_silent_bot_cannot_win_price_gate(self):
        counts, _ = score_reply("Tôi sẽ kiểm tra lại.", {"offer": True}, set(), set())
        self.assertEqual(counts.missing_offers, 1)
        self.assertIsNone(counts.values()["price_error_rate"])

    def test_conflict_confirmation_excluded_from_reask(self):
        counts, _ = score_reply("Thu nhập của chị là bao nhiêu?", {"confirmation_slots": ["income"]}, set(), {"income"})
        self.assertEqual(counts.eligible_questions, 0)
        counts, _ = score_reply("Thu nhập của chị là bao nhiêu?", {}, set(), {"income"})
        self.assertEqual(counts.redundant_questions, 1)

    def test_missing_handoff_fields_are_counted(self):
        counts = handoff_score({"reason": "apply"}, ["reason", "next_step"])
        self.assertEqual(counts.values()["handoff_completeness"], 0.5)

    def test_missing_or_duplicate_result_is_incomplete(self):
        row = {
            "scenario_id": "s",
            "mode": "harness",
            "repetition": 0,
            "status": "completed",
            "counters": vars(Counters(scenarios=1)),
        }
        self.assertEqual(summarize([row], 3)[0], "incomplete")
        self.assertEqual(summarize([row, row, row], 3)[0], "incomplete")

    def test_scenarios_are_seeded_and_cover_products(self):
        scenarios = load_scenarios(ROOT / "simulators/scenarios/dev")
        self.assertEqual({s["product"] for s in scenarios}, {"home_loan", "personal_loan", "credit_card"})
        one, two = Customer(scenarios[0], 42), Customer(scenarios[0], 42)
        self.assertEqual(one.message({"variants": ["a", "b"]}), two.message({"variants": ["a", "b"]}))

    def test_reset_boundary_preserves_multisession_case(self):
        class FakeApi:
            def __init__(self):
                self.calls = []
                self.admin_token = "test"

            def call(self, path, data=None, token=None):
                self.calls.append(path)
                if path == "/admin/sessions":
                    return {"session_id": "x", "session_token": "t", "user_id": "0900000001"}
                if path.endswith("/messages"):
                    return {"reply": "Xin chào"}
                return {}

        api = FakeApi()
        scenario = {
            "scenario_id": "two",
            "product": "home_loan",
            "sessions": [{"steps": [{"text": "hello"}]}, {"steps": [{"text": "hello"}]}],
        }
        run_case(api, scenario, "harness", 0, 42, Oracle(ROOT / "eval/oracle_fixtures/bank.json"))
        self.assertEqual(api.calls.count("/admin/reset"), 1)
        self.assertEqual(api.calls.count("/admin/sessions"), 2)

    def test_reject_remote_reset_target(self):
        with self.assertRaises(ValueError):
            Api("https://bank.example", "secret")

    def test_atomic_publishing_and_manifest(self):
        with TemporaryDirectory() as folder:
            p = Path(folder)
            run = {
                "run_id": "run-a",
                "status": "incomplete",
                "split": "dev",
                "created_at": "now",
                "backend": "rules",
                "seed": 42,
                "repetitions": 3,
                "summary": {},
            }
            publish(p, run)
            self.assertEqual(json.loads((p / "index.json").read_text())["runs"][0]["status"], "incomplete")
            self.assertEqual(json.loads((p / "run-a.json").read_text()), run)
            self.assertEqual(len(list(p.glob("*.tmp"))), 0)

    def test_holdout_must_be_external(self):
        with self.assertRaises(ValueError):
            validate_holdout(ROOT / "simulators/scenarios/dev", Path("absent"), "hash")

    def test_import_boundary(self):
        for directory in ("eval", "simulators"):
            for path in (ROOT / directory).rglob("*.py"):
                for node in ast.walk(ast.parse(path.read_text())):
                    modules = (
                        [alias.name for alias in node.names]
                        if isinstance(node, ast.Import)
                        else [node.module or ""]
                        if isinstance(node, ast.ImportFrom)
                        else []
                    )
                    self.assertFalse(
                        any(m.split(".")[0] in ("harness", "bank_tools", "sqlite3") for m in modules), str(path)
                    )

    def test_changes_require_human_receipt(self):
        with self.assertRaises(ValueError):
            validate_changes({"changes": [{"change_id": "x"}]})
        validate_changes(
            {"changes": [{"change_id": "x", "diff": "patch", "approved_by": "human", "approved_at": "now"}]}
        )

    def test_installments_independent_zero_interest(self):
        values = installments(Decimal(12000000), 12, Decimal(0), 6, Decimal(0))
        self.assertEqual(values["installment_promo"], Decimal(1000000))
        self.assertEqual(values["installment_after"], Decimal(1000000))
        self.assertEqual(values["total_payment"], Decimal(12000000))

    def test_provenance_detects_render_and_expiry_mutation(self):
        fact = {
            "id": "f",
            "key": "promo_rate",
            "value": "6.9",
            "unit": "%/năm",
            "source_type": "bank_tool",
            "source_ref": "tc",
            "observed_at": "2026-10-31",
            "valid_until": "2026-12-31",
            "status": "active",
            "channel": "telegram",
        }
        reply = "Lãi 6,9%/năm"
        span = {"start": 4, "end": 12, "fact_id": "f", "source_ref": "tc"}
        trace = {
            "facts": [fact],
            "source_spans": [span],
            "tool_calls": [{"result": {"facts": [{**fact, "fact_id": "f"}]}}],
        }
        self.assertEqual(audit(reply, trace, {"promo_rate": Decimal("6.9")}, "2026-10-31", "telegram", []), [])
        fact["valid_until"] = "2026-09-30"
        self.assertTrue(
            any(
                "invalid_fact_time" in item
                for item in audit(reply, trace, {"promo_rate": Decimal("6.9")}, "2026-10-31", "telegram", [])
            )
        )
        self.assertIn("untraced_numeric_claim", audit("Lãi 6,9%", {}, {}, "2026-10-31", "telegram", []))

    def test_freeze_detects_runtime_config_and_playbook_changes(self):
        state = {
            "backend": "rules",
            "model": None,
            "effective_config": {"guard_version": 1},
            "fixture_hash": "bank",
            "playbook": "v1",
        }
        manifest = {
            "config_hash": canonical_hash(effective_config(state)),
            "playbook_hash": canonical_hash("v1"),
            "fixture_hash": "bank",
        }
        verify_runtime(manifest, state)
        state["playbook"] = "v2"
        with self.assertRaises(ValueError):
            verify_runtime(manifest, state)

    def test_judge_label_agreement(self):
        self.assertEqual(agreement([1, 3], [1, 4])["exact_agreement"], 0.5)
        with self.assertRaises(ValueError):
            agreement([1], [1, 2])


if __name__ == "__main__":
    unittest.main()
