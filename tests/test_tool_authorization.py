import json
import unittest
from dataclasses import FrozenInstanceError

from bank_tools import AuthContext, BankTools, get_tool_definitions


class AuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.bank = BankTools()
        self.day = "2026-10-31"
        self.verified = AuthContext(customer_id="cus_001", identity_verified=True)
        self.logged_in = AuthContext(customer_id="cus_001", logged_in=True)
        self.identified_only = AuthContext(customer_id="cus_001")
        self.public_names = {
            "get_rate",
            "get_active_promotions",
            "get_card_offer",
            "get_insurance_offer",
            "calc_installment",
            "check_suitability",
            "get_disclosures",
        }

    def fact_id(self):
        result = self.bank.get_rate("home_loan", 2_000_000_000, 240, "mass", as_of=self.day)
        return result["facts"][0]["fact_id"]

    def private_arguments(self):
        return {
            "crm_lookup": {"as_of": self.day},
            "send_summary": {"as_of": self.day, "channel": "zalo", "fact_ids": [self.fact_id()]},
            "schedule_callback": {"as_of": self.day, "time": "2026-11-02T14:00:00+07:00"},
        }

    def assert_denied(self, result, prefix="authentication_required:"):
        self.assertTrue(result["error"].startswith(prefix), result)
        self.assertEqual(result["data"], {})
        self.assertEqual(result["facts"], [])
        json.dumps(result, allow_nan=False)

    def test_catalog_matches_seven_nine_ten_permission_groups(self):
        for context in (None, AuthContext(), self.identified_only):
            self.assertEqual({tool["name"] for tool in get_tool_definitions(context=context)}, self.public_names)
        verified = {tool["name"] for tool in get_tool_definitions(context=self.verified)}
        self.assertEqual(verified, self.public_names | {"send_summary", "schedule_callback"})
        logged_in = get_tool_definitions(context=self.logged_in)
        self.assertEqual({tool["name"] for tool in logged_in}, verified | {"crm_lookup"})
        for tool in logged_in:
            properties = tool["parameters"]["properties"]
            self.assertNotIn("customer_id", properties)
            self.assertNotIn("context", properties)
            self.assertNotIn("logged_in", properties)
            self.assertNotIn("identity_verified", properties)
            self.assertIn("as_of", tool["parameters"]["required"])

    def test_all_public_tools_work_without_login_or_verification(self):
        rate = self.bank.call(
            "get_rate",
            {"product": "home_loan", "amount": 2_000_000_000, "term": 240, "segment": "mass", "as_of": self.day},
        )
        self.assertIsNone(rate["error"])
        fact = rate["facts"][0]
        cases = {
            "get_active_promotions": {"product": "home_loan", "channel": "zalo"},
            "get_card_offer": {"income": 45_000_000, "segment": "mass"},
            "get_insurance_offer": {},
            "calc_installment": {
                "amount": 2_000_000_000,
                "term": 240,
                "rate": fact["value"],
                "rate_fact_id": fact["fact_id"],
            },
            "check_suitability": {"product": "credit_card", "income": 45_000_000, "debt": 0},
            "get_disclosures": {"product": "home_loan", "topics": ["rate"]},
        }
        for name, arguments in cases.items():
            with self.subTest(tool=name):
                result = self.bank.call(name, {**arguments, "as_of": self.day})
                self.assertIsNone(result["error"], result)

    def test_guest_or_customer_id_alone_cannot_call_private_tools(self):
        for context in (None, AuthContext(), self.identified_only):
            for name, arguments in self.private_arguments().items():
                with self.subTest(context=context, tool=name):
                    self.assert_denied(self.bank.call(name, arguments, context=context))
                    self.assert_denied(getattr(self.bank, name)("cus_001", **arguments, context=context))
        self.assertEqual(self.bank.get_mock_actions(), {"messages": [], "callbacks": []})

    def test_verified_customer_can_send_and_schedule_without_login(self):
        for name, arguments in self.private_arguments().items():
            result = self.bank.call(name, arguments, context=self.verified)
            if name == "crm_lookup":
                self.assert_denied(result)
            else:
                self.assertIsNone(result["error"], result)
                self.assertTrue(result["data"]["mock"])
        actions = self.bank.get_mock_actions()
        self.assertEqual(actions["messages"][0]["customer_id"], "cus_001")
        self.assertEqual(actions["callbacks"][0]["customer_id"], "cus_001")

    def test_verified_customer_direct_action_calls_are_allowed(self):
        result = self.bank.send_summary("cus_001", "zalo", [self.fact_id()], as_of=self.day, context=self.verified)
        self.assertIsNone(result["error"])
        result = self.bank.schedule_callback(
            "cus_001", "2026-11-02T14:00:00+07:00", as_of=self.day, context=self.verified
        )
        self.assertIsNone(result["error"])

    def test_login_allows_current_profile_and_both_actions(self):
        for name, arguments in self.private_arguments().items():
            result = self.bank.call(name, arguments, context=self.logged_in)
            self.assertIsNone(result["error"], result)
            if name == "crm_lookup":
                self.assertEqual(result["data"]["customer_id"], "cus_001")
        self.assertIsNone(self.bank.crm_lookup("cus_001", as_of=self.day, context=self.logged_in)["error"])

    def test_other_customer_is_blocked_on_direct_and_dispatch_paths(self):
        for context in (self.logged_in, self.verified):
            for name, arguments in self.private_arguments().items():
                if name == "crm_lookup" and not context.logged_in:
                    continue
                with self.subTest(context=context, tool=name):
                    self.assert_denied(
                        self.bank.call(name, {**arguments, "customer_id": "cus_002"}, context=context), "forbidden:"
                    )
                    self.assert_denied(getattr(self.bank, name)("cus_002", **arguments, context=context), "forbidden:")
        self.assertEqual(self.bank.get_mock_actions(), {"messages": [], "callbacks": []})

    def test_model_cannot_supply_auth_states_or_context(self):
        arguments = self.private_arguments()["schedule_callback"]
        for fake in (
            {"logged_in": True},
            {"identity_verified": True},
            {"context": {"customer_id": "cus_001", "identity_verified": True}},
        ):
            result = self.bank.call("schedule_callback", {**arguments, **fake})
            self.assertTrue(result["error"].startswith("invalid:"), result)
        spoof = self.bank.call("schedule_callback", {**arguments, "customer_id": "cus_001"})
        self.assert_denied(spoof)
        fake_context = {"customer_id": "cus_001", "identity_verified": True}
        direct = self.bank.schedule_callback("cus_001", arguments["time"], as_of=self.day, context=fake_context)
        self.assert_denied(direct, "forbidden:")
        dispatched = self.bank.call("schedule_callback", arguments, context=fake_context)
        self.assertTrue(dispatched["error"].startswith("invalid:"))
        with self.assertRaises(TypeError):
            get_tool_definitions(context=fake_context)
        self.assertEqual(self.bank.get_mock_actions()["callbacks"], [])

    def test_profile_cannot_be_searched_by_another_identifier(self):
        result = self.bank.call("crm_lookup", {"as_of": self.day, "identifier": "demo:an:zalo"}, context=self.logged_in)
        self.assertTrue(result["error"].startswith("invalid:"))
        self.assertEqual(result["data"], {})

    def test_previous_login_is_not_cached_after_logout(self):
        first = self.bank.call("crm_lookup", {"as_of": self.day}, context=self.logged_in)
        self.assertIsNone(first["error"])
        self.assert_denied(self.bank.call("crm_lookup", {"as_of": self.day}))
        self.assert_denied(self.bank.call("schedule_callback", self.private_arguments()["schedule_callback"]))
        self.assertEqual(len(get_tool_definitions()), 7)

    def test_denied_call_does_not_consume_injected_fault_or_create_action(self):
        args = self.private_arguments()["schedule_callback"]
        self.bank.inject_fault("schedule_callback", "timeout", 1)
        self.assert_denied(self.bank.call("schedule_callback", args))
        fault = self.bank.call("schedule_callback", args, context=self.verified)
        self.assertEqual(fault["error"], "timeout")
        self.assertEqual(self.bank.get_mock_actions()["callbacks"], [])
        self.assertIsNone(self.bank.call("schedule_callback", args, context=self.verified)["error"])

    def test_auth_context_is_strict_immutable_and_not_written_to_trace(self):
        for arguments in (
            {"logged_in": True},
            {"identity_verified": True},
            {"customer_id": "cus_001", "logged_in": "true"},
            {"customer_id": "cus_001", "identity_verified": 1},
            {"customer_id": " "},
        ):
            with self.assertRaises(ValueError):
                AuthContext(**arguments)
        with self.assertRaises(FrozenInstanceError):
            self.verified.logged_in = True
        self.bank.call("crm_lookup", {"as_of": self.day}, context=self.logged_in)
        self.bank.call("schedule_callback", self.private_arguments()["schedule_callback"], context=self.verified)
        traces = self.bank.get_tool_calls()
        json.dumps(traces, allow_nan=False)
        for trace in traces:
            self.assertNotIn("context", trace["arguments"])
            self.assertNotIn("logged_in", trace["arguments"])
            self.assertNotIn("identity_verified", trace["arguments"])


if __name__ == "__main__":
    unittest.main()
