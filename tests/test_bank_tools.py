from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from inspect import Parameter, signature
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import yaml

from bank_tools import AuthContext, BankTools, get_tool_definitions
from examples.demo import main as demo_main
from contextlib import redirect_stdout
from io import StringIO


class BankToolsTests(unittest.TestCase):
    def setUp(self):
        self.bank = BankTools()
        self.context = AuthContext(customer_id="cus_001", logged_in=True)

    def assert_success(self, result):
        self.assertIsNone(result["error"], result)
        json.dumps(result, allow_nan=False)
        return result

    def rate(self, bank=None, **overrides):
        args = dict(product="home_loan", amount=2_000_000_000, term=240, segment="mass", as_of="2026-10-31")
        args.update(overrides)
        return self.assert_success((bank or self.bank).get_rate(**args))

    def financing(self):
        standard = next(fact for fact in self.rate()["facts"] if fact["key"] == "standard_rate")
        promo = self.bank.get_active_promotions("home_loan", "zalo", as_of="2026-10-31")["facts"][0]
        return dict(amount=2_000_000_000, term=240, rate=promo["value"], rate_fact_id=promo["fact_id"],
                    promo_months=12, after_rate=standard["value"], after_rate_fact_id=standard["fact_id"])

    def test_crm_returns_only_current_customer_without_private_financial_profile(self):
        result = self.assert_success(self.bank.crm_lookup("cus_001", as_of="2026-10-31", context=self.context))
        profile = result["data"]
        self.assertEqual(profile["customer_id"], "cus_001")
        self.assertNotIn("dob", profile)
        self.assertNotIn("income", profile)
        self.assertNotIn("monthly_debt_payment", profile)

    def test_verification_is_external_and_fixture_has_no_birthdates(self):
        self.assertEqual(self.bank.crm_lookup("unknown", as_of="2026-10-31", context=AuthContext(customer_id="unknown", logged_in=True))["data"], {"found": False})
        self.assertFalse(hasattr(self.bank, "verify_identity"))
        self.assertNotIn("verify_identity", {tool["name"] for tool in get_tool_definitions(context=self.context)})
        fixture = Path(__file__).resolve().parents[1] / "bank_tools/fixtures/customers.yaml"
        self.assertTrue(all("dob" not in customer for customer in yaml.safe_load(fixture.read_text())["customers"]))

    def test_all_ten_customers_are_available(self):
        for number in range(1, 11):
            with self.subTest(number=number):
                self.assertTrue(self.bank.crm_lookup(f"cus_{number:03}", as_of="2026-10-31", context=AuthContext(customer_id=f"cus_{number:03}", logged_in=True))["data"]["found"])

    def test_promotion_expiry_is_inclusive_and_does_not_leak_future(self):
        for day, expected in (("2026-09-30", "HL-Q3-2026"), ("2026-10-01", "HL-Q4-2026"),
                              ("2026-12-31", "HL-Q4-2026"), ("2027-01-01", "HL-Q1-2027")):
            with self.subTest(day=day):
                result = self.assert_success(self.bank.get_active_promotions("home_loan", "zalo", as_of=day))
                self.assertEqual([row["promo_id"] for row in result["data"]["promotions"]], [expected])

    def test_channels_have_different_promotions(self):
        hotline = self.bank.get_active_promotions("home_loan", "hotline", as_of="2026-10-31")
        web = self.bank.get_active_promotions("home_loan", "web", as_of="2026-10-31")
        self.assertEqual(hotline["facts"][0]["value"], 6.9)
        self.assertEqual(web["facts"][0]["value"], 7.1)
        self.assertEqual(web["facts"][1]["value"], 6)

    def test_no_promotion_is_success_with_no_facts(self):
        result = self.assert_success(self.bank.get_active_promotions("credit_card", "hotline", as_of="2026-10-31"))
        self.assertEqual(result["data"]["promotions"], [])
        self.assertEqual(result["facts"], [])

    def test_card_waiver_is_percentage_not_vnd(self):
        result = self.assert_success(self.bank.get_active_promotions("credit_card", "zalo", as_of="2026-10-31"))
        fact = result["facts"][0]
        self.assertEqual((fact["key"], fact["value"], fact["unit"]), ("promo_fee_waiver", 100.0, "%"))

    def test_rate_bands_segments_and_expired_rate_table(self):
        self.assertEqual(self.rate()["facts"][0]["value"], 10.5)
        self.assertEqual(self.rate(segment="priority")["facts"][0]["value"], 10.0)
        for amount, term in ((1, 240), (2_000_000_000, 1), (2_000_000_000, 361)):
            self.assertIsNotNone(self.bank.get_rate("home_loan", amount, term, "mass", as_of="2026-10-31")["error"])
        result = self.bank.get_rate("home_loan", 2_000_000_000, 240, "mass", as_of="2027-01-01")
        self.assertIsNotNone(result["error"])
        self.assertEqual(result["facts"], [])

    def test_fact_ids_stable_across_instances_but_context_sensitive(self):
        first = self.rate()
        second = self.rate(BankTools())
        self.assertEqual(first["facts"][0]["fact_id"], second["facts"][0]["fact_id"])
        self.assertNotEqual(first["tool_call_id"], second["tool_call_id"])
        self.assertEqual(first["facts"][0]["source_ref"], first["tool_call_id"])
        self.assertNotEqual(first["facts"][0]["fact_id"], self.rate(amount=1_000_000_000)["facts"][0]["fact_id"])
        self.assertNotEqual(first["facts"][0]["fact_id"], self.rate(as_of="2026-11-01")["facts"][0]["fact_id"])

    def test_card_offer_threshold_and_income_capped_limit(self):
        below = self.assert_success(self.bank.get_card_offer(6_000_000, "mass", as_of="2026-10-31"))
        self.assertFalse(below["data"]["eligible"])
        self.assertEqual(below["facts"], [])
        eligible = self.assert_success(self.bank.get_card_offer(7_000_000, "mass", as_of="2026-10-31"))
        self.assertEqual(next(f["value"] for f in eligible["facts"] if f["key"] == "card_limit_max"), 21_000_000)
        priority = self.assert_success(self.bank.get_card_offer(150_000_000, "priority", as_of="2026-10-31"))
        self.assertEqual(next(f["value"] for f in priority["facts"] if f["key"] == "card_limit_max"), 500_000_000)

    def test_two_stage_installment_against_independent_amortization_schedule(self):
        args = self.financing()
        result = self.assert_success(self.bank.calc_installment(**args, as_of="2026-10-31"))
        values = {fact["key"]: fact["value"] for fact in result["facts"]}
        self.assertGreater(values["installment_after"], values["installment_promo"])
        # Oracle dùng công thức hệ số tăng trưởng, rồi chạy lịch trả nợ từng tháng.
        balance = Decimal(args["amount"])
        total = Decimal(0)
        payment = None
        for month in range(args["term"]):
            rate = Decimal(str(args["rate"] if month < 12 else args["after_rate"])) / 1200
            if month in (0, 12):
                remaining = args["term"] - month
                growth = (1 + rate) ** remaining
                payment = balance * rate * growth / (growth - 1)
                key = "installment_promo" if month == 0 else "installment_after"
                self.assertLessEqual(abs(Decimal(values[key]) - payment), 500)
            balance = balance * (1 + rate) - payment
            total += payment
        self.assertLess(abs(balance), Decimal("0.000001"))
        self.assertLessEqual(abs(Decimal(values["total_payment"]) - total), 500)
        self.assertTrue(all(fact["valid_until"] == "2026-12-31" for fact in result["facts"]))

    def test_zero_interest_installment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(__file__).resolve().parents[1] / "bank_tools" / "fixtures"
            shutil.copytree(root, temporary, dirs_exist_ok=True)
            path = Path(temporary) / "rates.yaml"
            fixture = yaml.safe_load(path.read_text())
            next(row for row in fixture["rates"] if row["rate_id"] == "PL-MASS-2026")["standard_rate"] = 0
            path.write_text(yaml.safe_dump(fixture), encoding="utf-8")
            bank = BankTools(fixtures_dir=temporary)
            fact = self.rate(bank, product="personal_loan", amount=10_000_000, term=10)["facts"][0]
            result = self.assert_success(bank.calc_installment(10_000_000, 10, 0, fact["fact_id"], as_of="2026-10-31"))
            self.assertEqual({f["key"]: f["value"] for f in result["facts"]},
                             {"installment_standard": 1_000_000, "total_payment": 10_000_000})

    def test_installment_rejects_untrusted_or_mismatched_rates(self):
        original = self.financing()
        for updates in ({"rate_fact_id": "invented"}, {"rate": 1.0}, {"after_rate": 2.0},
                        {"after_rate_fact_id": None}, {"promo_months": 6}, {"promo_months": 0},
                        {"amount": 1_000_000_000}):
            with self.subTest(updates=updates):
                result = self.bank.calc_installment(**{**original, **updates}, as_of="2026-10-31")
                self.assertIsNotNone(result["error"])
                self.assertEqual(result["facts"], [])

    def test_standard_fact_is_not_reusable_for_other_amount(self):
        fact = self.rate()["facts"][0]
        self.assertIsNotNone(self.bank.calc_installment(1_000_000_000, 240, 10.5, fact["fact_id"], as_of="2026-10-31")["error"])

    def test_promotion_covers_entire_loan_has_no_second_stage(self):
        fact = self.bank.get_active_promotions("home_loan", "zalo", as_of="2026-09-30")["facts"][0]
        result = self.assert_success(self.bank.calc_installment(100_000_000, 12, 6.5, fact["fact_id"], promo_months=12, as_of="2026-09-30"))
        self.assertNotIn("installment_after", {f["key"] for f in result["facts"]})

    def test_suitability_counts_proposed_loan_in_dti(self):
        okay = self.assert_success(self.bank.check_suitability("home_loan", 100_000_000, 10_000_000,
                                                               2_000_000_000, 240, 3_000_000_000, as_of="2026-10-31"))
        self.assertTrue(okay["data"]["ok"])
        hoa = self.assert_success(self.bank.check_suitability("home_loan", 45_000_000, 5_000_000,
                                                              2_000_000_000, 240, 3_000_000_000, as_of="2026-10-31"))
        self.assertIn("dti_too_high", hoa["data"]["reasons"])
        high = self.assert_success(self.bank.check_suitability("personal_loan", 30_000_000, 18_000_000, 100_000_000, 36, as_of="2026-10-31"))
        self.assertIn("dti_too_high", high["data"]["reasons"])
        new_loan = self.assert_success(self.bank.check_suitability("personal_loan", 10_000_000, 0, 500_000_000, 6, as_of="2026-10-31"))
        self.assertIn("dti_too_high", new_loan["data"]["reasons"])

    def test_suitability_handles_zero_income_and_ltv_boundary(self):
        result = self.assert_success(self.bank.check_suitability("credit_card", 0, 0, as_of="2026-10-31"))
        self.assertFalse(result["data"]["ok"])
        self.assertIsNone(result["data"]["diagnostics"]["dti"])
        for amount, expected in ((800_000_000, False), (800_000_001, True)):
            data = self.assert_success(self.bank.check_suitability("home_loan", 100_000_000, 0, amount, 240, 1_000_000_000, as_of="2026-10-31"))["data"]
            self.assertEqual("ltv_too_high" in data["reasons"], expected)

    def test_missing_suitability_inputs_are_errors_not_rejections(self):
        self.assertIsNotNone(self.bank.check_suitability("home_loan", 45_000_000, 0, as_of="2026-10-31")["error"])
        self.assertIsNotNone(self.bank.check_suitability("credit_card", 45_000_000, 0, amount=100, as_of="2026-10-31")["error"])

    def test_disclosures_reject_unknown_topics_and_deduplicate(self):
        result = self.assert_success(self.bank.get_disclosures("home_loan", ["rate", "promo", "rate"], as_of="2026-10-31"))
        self.assertEqual(len(result["data"]["disclosures"]), 2)
        for topics in ([], ["unknown"], "rate"):
            self.assertIsNotNone(self.bank.get_disclosures("home_loan", topics, as_of="2026-10-31")["error"])

    def test_summary_rejects_invented_fact_and_wrong_channel(self):
        self.assertIsNotNone(self.bank.send_summary("cus_001", "zalo", ["invented"], as_of="2026-10-31", context=self.context)["error"])
        args = self.financing()
        derived = self.bank.calc_installment(**args, as_of="2026-10-31")["facts"][0]
        self.assertIsNotNone(self.bank.send_summary("cus_001", "web", [derived["fact_id"]], as_of="2026-10-31", context=self.context)["error"])
        result = self.assert_success(self.bank.send_summary("cus_001", "zalo", [derived["fact_id"]], as_of="2026-10-31", context=self.context))
        self.assertEqual(result["data"]["status"], "mock_sent")
        self.assertEqual(len(self.bank.get_mock_actions()["messages"]), 1)

    def test_next_day_requires_refresh_even_when_fact_not_expired(self):
        fact = self.rate()["facts"][0]
        self.assertIsNotNone(self.bank.send_summary("cus_001", "zalo", [fact["fact_id"]], as_of="2026-11-01", context=self.context)["error"])

    def test_fact_registry_is_isolated_and_cannot_be_mutated_by_return_value(self):
        fact = self.rate()["facts"][0]
        other = BankTools()
        self.assertIsNotNone(other.send_summary("cus_001", "zalo", [fact["fact_id"]], as_of="2026-10-31", context=self.context)["error"])
        fact["value"] = 0
        result = self.assert_success(self.bank.send_summary("cus_001", "zalo", [fact["fact_id"]], as_of="2026-10-31", context=self.context))
        action = self.bank.get_mock_actions()["messages"][0]
        self.assertEqual(action["facts"][0]["value"], 10.5)
        action["facts"][0]["value"] = 999
        self.assertNotEqual(self.bank.get_mock_actions()["messages"][0]["facts"][0]["value"], 999)

    def test_callback_requires_timezone_and_later_date(self):
        for value in ("2026-11-02T14:00:00", "2026-10-30T14:00:00+07:00", "2026-10-31T23:00:00+07:00", "not a date"):
            self.assertIsNotNone(self.bank.schedule_callback("cus_001", value, as_of="2026-10-31", context=self.context)["error"])
        result = self.assert_success(self.bank.schedule_callback("cus_001", "2026-11-02T14:00:00+07:00", as_of="2026-10-31", context=self.context))
        self.assertTrue(result["data"]["mock"])
        self.assertEqual(len(self.bank.get_mock_actions()["callbacks"]), 1)

    def test_fault_count_is_consumed_and_does_not_create_facts_or_actions(self):
        self.bank.inject_fault("get_rate", "timeout", 2)
        for _ in range(2):
            result = self.bank.get_rate("home_loan", 2_000_000_000, 240, "mass", as_of="2026-10-31")
            self.assertEqual(result["error"], "timeout")
            self.assertEqual(result["facts"], [])
        self.rate()
        self.bank.inject_fault("schedule_callback", "error")
        result = self.bank.schedule_callback("cus_001", "2026-11-02T14:00:00+07:00", as_of="2026-10-31", context=self.context)
        self.assertEqual(result["error"], "error")
        self.assertEqual(self.bank.get_mock_actions()["callbacks"], [])

    def test_dispatch_handles_bad_arguments_and_blocks_admin_methods(self):
        for name, arguments in (("inject_fault", {}), ("__init__", {}), ("get_rate", "{bad"),
                                ("get_rate", "[]"), ("get_rate", {}), ("crm_lookup", {"identifier": "cus_001", "extra": 1}),
                                ("crm_lookup", "null"), ("crm_lookup", {1: "bad"})):
            with self.subTest(name=name, arguments=arguments):
                result = self.bank.call(name, arguments, context=self.context)
                self.assertIsNotNone(result["error"])
                self.assertEqual(result["facts"], [])
        result = self.assert_success(self.bank.call("crm_lookup", '{"as_of":"2026-10-31"}', context=self.context))
        self.assertTrue(result["data"]["found"])

    def test_invalid_numeric_inputs_do_not_enter_financial_calculations(self):
        for amount in (True, -1, 0, "2000000000", 1.5, 10**20):
            self.assertIsNotNone(self.bank.get_rate("home_loan", amount, 240, "mass", as_of="2026-10-31")["error"])
        args = self.financing()
        for rate in (float("nan"), float("inf"), True, -1, "6.9"):
            self.assertIsNotNone(self.bank.calc_installment(**{**args, "rate": rate}, as_of="2026-10-31")["error"])

    def test_tool_catalog_isolated_and_traces_are_json(self):
        definitions = get_tool_definitions(context=self.context)
        self.assertEqual(len(definitions), 9)
        self.assertEqual(len({tool["name"] for tool in definitions}), 9)
        definitions[0]["parameters"]["properties"]["as_of"]["type"] = "wrong"
        self.assertEqual(get_tool_definitions(context=self.context)[0]["parameters"]["properties"]["as_of"]["type"], "string")
        self.rate()
        trace = self.bank.get_tool_calls()
        json.dumps(trace, allow_nan=False)
        trace[0]["facts"].clear()
        self.assertEqual(len(self.bank.get_tool_calls()[0]["facts"]), 2)

    def test_entire_demo_runs_all_nine_tools(self):
        output = StringIO()
        with redirect_stdout(output):
            demo_main()
        self.assertIn("Đã chạy 9/9 tool", output.getvalue())

    def test_bad_custom_fixture_is_rejected_at_startup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(__file__).resolve().parents[1] / "bank_tools" / "fixtures"
            shutil.copytree(root, temporary, dirs_exist_ok=True)
            path = Path(temporary) / "customers.yaml"
            fixture = yaml.safe_load(path.read_text())
            fixture["customers"][1]["identity_links"][0]["external_id"] = "demo:hoa:zalo"
            path.write_text(yaml.safe_dump(fixture), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unique"):
                BankTools(fixtures_dir=temporary)

    def test_every_tool_requires_valid_date_in_schema_and_runtime(self):
        for definition in get_tool_definitions(context=self.context):
            name = definition["name"]
            schema = definition["parameters"]
            self.assertIn("as_of", schema["required"])
            self.assertEqual(schema["properties"]["as_of"]["format"], "date")
            parameter = signature(getattr(self.bank, name)).parameters["as_of"]
            self.assertIs(parameter.default, Parameter.empty)
            self.assertIs(parameter.kind, Parameter.KEYWORD_ONLY)
            args = {key: None for key in schema["required"] if key != "as_of"}
            with self.subTest(tool=name, date="missing"):
                result = self.bank.call(name, args, context=self.context)
                self.assertIn("as_of", result["error"])
                self.assertIsNone(result["as_of"])
                self.assertEqual(result["facts"], [])
            for day in (None, False, "20261031", "31/10/2026", "2026-02-30", "2026-10-31T00:00:00"):
                with self.subTest(tool=name, date=day):
                    result = self.bank.call(name, {**args, "as_of": day}, context=self.context)
                    self.assertIsNotNone(result["error"])
                    self.assertIsNone(result["as_of"])
                    self.assertEqual(result["facts"], [])
        self.assertEqual(self.bank.get_mock_actions(), {"messages": [], "callbacks": []})

    def test_interleaved_concurrent_dates_do_not_change_other_calls(self):
        days = ["2026-09-30", "2026-10-31"] * 4

        def lookup(day):
            return self.bank.get_active_promotions("home_loan", "zalo", as_of=day)

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lookup, days))
        for day, result in zip(days, results):
            self.assert_success(result)
            self.assertEqual(result["as_of"], day)
            self.assertTrue(all(fact["observed_at"] == day for fact in result["facts"]))
            self.assertEqual(result["facts"][0]["value"], 6.5 if day == "2026-09-30" else 6.9)
        self.assertFalse(hasattr(self.bank, "set_as_of"))

    def test_new_day_rejects_old_facts_and_accepts_refreshed_facts(self):
        original = self.rate()["facts"][0]
        old = self.bank.send_summary("cus_001", "zalo", [original["fact_id"]], as_of="2026-11-01", context=self.context)
        self.assertIn("different date", old["error"])
        refreshed = self.rate(as_of="2026-11-01")["facts"][0]
        self.assertNotEqual(original["fact_id"], refreshed["fact_id"])
        self.assert_success(self.bank.send_summary("cus_001", "zalo", [refreshed["fact_id"]], as_of="2026-11-01", context=self.context))

    def test_calculation_rejects_future_stale_and_expired_rate_facts(self):
        arguments = self.financing()
        for day in ("2026-10-30", "2026-11-01", "2027-01-01"):
            with self.subTest(day=day):
                result = self.bank.calc_installment(**arguments, as_of=day)
                self.assertIsNotNone(result["error"])
                self.assertEqual(result["as_of"], day)
                self.assertEqual(result["facts"], [])

    def test_dated_policies_and_cards_fail_outside_validity(self):
        for day in ("2025-12-31", "2027-01-01"):
            for tool, args in (
                ("get_card_offer", {"income": 45_000_000, "segment": "mass"}),
                ("check_suitability", {"product": "credit_card", "income": 45_000_000, "debt": 0}),
                ("get_disclosures", {"product": "home_loan", "topics": ["rate"]}),
            ):
                with self.subTest(day=day, tool=tool):
                    result = self.bank.call(tool, {**args, "as_of": day}, context=self.context)
                    self.assertIsNotNone(result["error"])
                    self.assertEqual(result["as_of"], day)
                    self.assertEqual(result["facts"], [])
        end = self.bank.get_disclosures("home_loan", ["rate"], as_of="2026-12-31")
        self.assert_success(end)

    def test_invalid_date_never_uses_previous_date_or_consumes_fault(self):
        self.rate()
        self.bank.inject_fault("get_rate", "timeout", 1)
        arguments = {"product": "home_loan", "amount": 2_000_000_000, "term": 240, "segment": "mass"}
        invalid = self.bank.call("get_rate", {**arguments, "as_of": "bad-date"}, context=self.context)
        self.assertIsNone(invalid["as_of"])
        missing = self.bank.call("get_rate", arguments, context=self.context)
        self.assertIsNone(missing["as_of"])
        valid = self.bank.call("get_rate", {**arguments, "as_of": "2026-10-31"}, context=self.context)
        self.assertEqual(valid["error"], "timeout")
        self.assertEqual(valid["as_of"], "2026-10-31")

    def test_callback_uses_call_date_and_records_it(self):
        result = self.bank.schedule_callback("cus_001", "2026-11-02T14:00:00+07:00", as_of="2026-11-01", context=self.context)
        self.assert_success(result)
        self.assertEqual(self.bank.get_mock_actions()["callbacks"][0]["as_of"], "2026-11-01")
        rejected = self.bank.schedule_callback("cus_001", "2026-11-02T14:00:00+07:00", as_of="2026-11-02", context=self.context)
        self.assertIsNotNone(rejected["error"])
        self.assertEqual(len(self.bank.get_mock_actions()["callbacks"]), 1)


if __name__ == "__main__":
    unittest.main()
