"""Tool đồng bộ, kết quả JSON thuần; mỗi lời gọi có ngày as_of riêng."""

import json
from copy import deepcopy
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from functools import wraps
from hashlib import sha1
from importlib.resources import files
from inspect import signature
from pathlib import Path
from threading import RLock
from uuid import uuid4

import yaml

from .auth import TOOL_ACCESS, AccessDenied, AuthContext, require_access
from .catalog import TOOL_NAMES

PRODUCTS = {"home_loan", "personal_loan", "credit_card"}
CHANNELS = {"hotline", "zalo", "web", "telegram"}
SEGMENTS = {"mass", "priority"}
MAX_MONEY = 100_000_000_000


def _choice(value, choices, name):
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"{name} must be one of {sorted(choices)}")
    return value


def _integer(value, name, minimum=0, maximum=MAX_MONEY):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def _rate(value, name="rate"):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise TypeError(f"{name} must be a number in percent/year")
    number = Decimal(str(value))
    if not number.is_finite() or not 0 <= number <= 100:
        raise ValueError(f"{name} must be finite and in [0, 100]")
    return number


def _date(value):
    if not isinstance(value, str):
        raise TypeError("date must be a YYYY-MM-DD string")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("date must use YYYY-MM-DD")
    return parsed


def _payment(amount, months, rate):
    """Khoản trả đều hàng tháng theo dư nợ giảm dần (annuity)."""
    principal = Decimal(amount)
    monthly = Decimal(str(rate)) / Decimal(1200)
    with localcontext() as ctx:
        ctx.prec = 40
        if monthly == 0:
            return principal / months
        return principal * monthly / (1 - (1 + monthly) ** (-months))


def _round_money(value):
    return int((Decimal(value) / 1000).quantize(Decimal(1), rounding=ROUND_HALF_UP) * 1000)


def bank_tool(function):
    """Một nơi xử lý trace, lỗi và provenance cho mọi tool."""

    @wraps(function)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            name = function.__name__
            tool_call_id = "tc_" + uuid4().hex[:12]
            parameters = {}
            call_date = None
            try:
                bound = signature(function).bind(self, *args, **kwargs)
                bound.apply_defaults()
                parameters = {key: value for key, value in bound.arguments.items() if key != "self"}
                call_date = _date(parameters["as_of"]).isoformat()
                if TOOL_ACCESS[name] != "public":
                    require_access(name, parameters.get("context"), parameters.get("customer_id"))
                fault = self._faults.get(name)
                if fault and fault["count"] > 0:
                    fault["count"] -= 1
                    return self._record(name, tool_call_id, parameters, {}, [], fault["mode"], as_of=call_date)
                data, specs = function(self, **parameters)
                facts = [self._make_fact(name, tool_call_id, parameters, **spec) for spec in specs]
                return self._record(name, tool_call_id, parameters, data, facts, None, as_of=call_date)
            except AccessDenied as exc:
                return self._record(name, tool_call_id, parameters, {}, [], str(exc), as_of=call_date)
            except (ValueError, TypeError, InvalidOperation) as exc:
                return self._record(name, tool_call_id, parameters, {}, [], f"invalid: {exc}", as_of=call_date)

    return wrapped


class BankTools:
    """Tạo một instance cho mỗi phiên agent để cô lập fact và dữ liệu hành động.

    Instance kiểm tra quyền từ AuthContext do backend cung cấp, không thực hiện
    đăng nhập/xác minh danh tính, không lưu DB hay gửi tin nhắn thật.
    inject_fault dành cho test; backend truyền ngày
    as_of bắt buộc vào từng tool, không giữ clock mặc định trong instance.
    """

    def __init__(self, *, fixtures_dir=None):
        self._lock = RLock()
        root = Path(fixtures_dir) if fixtures_dir is not None else files("bank_tools").joinpath("fixtures")
        self._fixtures = {}
        for name in ("customers", "rates", "promotions", "card_products", "suitability_rules", "disclosures"):
            document = yaml.safe_load(root.joinpath(f"{name}.yaml").read_text(encoding="utf-8"))
            if not isinstance(document, dict) or name not in document:
                raise ValueError(f"Invalid fixture file: {name}.yaml")
            self._fixtures[name] = document[name]
        self._validate_fixtures()
        self._facts = {}
        self._faults = {}
        self._calls = []
        self._messages = []
        self._callbacks = []

    def inject_fault(self, tool, mode="timeout", count=1):
        """Admin/test: lỗi có đếm, không đăng ký phương thức này cho model."""
        _choice(tool, TOOL_NAMES, "tool")
        _choice(mode, {"timeout", "error"}, "mode")
        _integer(count, "count", 1, 1000)
        with self._lock:
            self._faults[tool] = {"mode": mode, "count": count}

    def get_tool_calls(self):
        with self._lock:
            return deepcopy(self._calls)

    def get_mock_actions(self):
        with self._lock:
            return deepcopy({"messages": self._messages, "callbacks": self._callbacks})

    def call(self, tool_name, arguments, *, context=None):
        """Dispatcher chèn customer_id từ context backend, không lấy quyền từ JSON."""
        if not isinstance(tool_name, str) or tool_name not in TOOL_NAMES:
            with self._lock:
                return self._record("unknown", "tc_" + uuid4().hex[:12], {}, {}, [], "invalid: unknown tool")
        try:
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            if not isinstance(arguments, dict):
                raise TypeError("arguments must be a JSON object")
            if any(not isinstance(key, str) for key in arguments):
                raise ValueError("argument names must be strings")
            if any(key in arguments for key in ("context", "logged_in", "identity_verified")):
                raise ValueError("auth context and auth states must not appear in tool arguments")
            if context is not None and not isinstance(context, AuthContext):
                raise ValueError("context must be an AuthContext supplied by the backend")
        except (ValueError, TypeError) as exc:
            with self._lock:
                return self._record(tool_name, "tc_" + uuid4().hex[:12], {}, {}, [], f"invalid: {exc}")
        if TOOL_ACCESS[tool_name] != "public":
            arguments = dict(arguments)
            arguments.setdefault("customer_id", context.customer_id if context is not None else None)
            return getattr(self, tool_name)(**arguments, context=context)
        return getattr(self, tool_name)(**arguments)

    def _validate_fixtures(self):
        customers = self._fixtures["customers"]
        for field in ("customer_id", "cif"):
            values = [customer[field] for customer in customers]
            if len(set(values)) != len(values):
                raise ValueError(f"Duplicate customer {field}")
        identifiers = set()
        for customer in customers:
            _choice(customer["segment"], SEGMENTS, "segment")
            _integer(customer["income"], "income")
            _integer(customer["monthly_debt_payment"], "monthly_debt_payment")
            for identifier in [customer["customer_id"], customer["cif"]] + [
                link["external_id"] for link in customer["identity_links"]
            ]:
                if not isinstance(identifier, str) or not identifier or identifier in identifiers:
                    raise ValueError("Customer identifiers must be globally unique strings")
                identifiers.add(identifier)
            for link in customer["identity_links"]:
                _choice(link["channel"], CHANNELS, "channel")
        for group, id_key in (("rates", "rate_id"), ("promotions", "promo_id"), ("card_products", "card_id")):
            ids = [row[id_key] for row in self._fixtures[group]]
            if len(ids) != len(set(ids)):
                raise ValueError(f"Duplicate identifiers in {group}")
            for row in self._fixtures[group]:
                if _date(row["valid_from"]) > _date(row["valid_until"]):
                    raise ValueError(f"Invalid date range in {group}")
        for row in self._fixtures["rates"]:
            _choice(row["product"], PRODUCTS - {"credit_card"}, "product")
            _choice(row["segment"], SEGMENTS, "segment")
            _rate(row["standard_rate"])
            _rate(row["floating_margin"])
            self._validate_bands(row)
        for row in self._fixtures["promotions"]:
            _choice(row["product"], PRODUCTS, "product")
            if not row["channels"]:
                raise ValueError("Promotion must have a channel")
            for channel in row["channels"]:
                _choice(channel, CHANNELS, "channel")
            _integer(row["promo_months"], "promo_months", 1, 600)
            _rate(row["fee_waiver"] if row["product"] == "credit_card" else row["rate"])
        for row in self._fixtures["card_products"]:
            _choice(row["segment"], SEGMENTS, "segment")
            for key in ("min_income", "card_limit_min", "card_limit_max", "annual_fee", "fee_waiver_spend"):
                _integer(row[key], key)
            _integer(row["income_multiplier"], "income_multiplier", 1, 100)
            if row["card_limit_min"] > row["card_limit_max"]:
                raise ValueError("Invalid card limit range")
        for product in PRODUCTS:
            rule = self._fixtures["suitability_rules"][product]
            disclosure = self._fixtures["disclosures"][product]
            for row in (rule, disclosure):
                if _date(row["valid_from"]) > _date(row["valid_until"]):
                    raise ValueError("Invalid policy date range")
            _integer(rule["min_income"], "min_income", 1)
            if not 0 < rule["max_dti"] <= 1:
                raise ValueError("max_dti must be in (0, 1]")
            if product != "credit_card":
                self._validate_bands(rule)
                _rate(rule["assessment_rate"])
            if product == "home_loan" and not 0 < rule["max_ltv"] <= 1:
                raise ValueError("max_ltv must be in (0, 1]")
            for topic in ("rate", "installment", "fee", "promo"):
                text = disclosure[topic]
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("Disclosure text must not be empty")

    @staticmethod
    def _validate_bands(row):
        _integer(row["amount_min"], "amount_min", 1)
        _integer(row["amount_max"], "amount_max", row["amount_min"])
        _integer(row["term_min"], "term_min", 1, 600)
        _integer(row["term_max"], "term_max", row["term_min"], 600)

    def _active(self, row, as_of):
        return _date(row["valid_from"]) <= _date(as_of) <= _date(row["valid_until"])

    def _rules(self, product, as_of):
        rule = self._fixtures["suitability_rules"][product]
        if not self._active(rule, as_of):
            raise ValueError("suitability rules are not effective on as_of")
        return rule

    def _customer(self, customer_id):
        if not isinstance(customer_id, str):
            raise TypeError("customer_id must be a string")
        for customer in self._fixtures["customers"]:
            if customer["customer_id"] == customer_id:
                return customer
        raise ValueError("customer not found")

    @staticmethod
    def _spec(key, value, unit, label, valid_until, **metadata):
        return {
            "key": key,
            "value": value,
            "unit": unit,
            "label": label,
            "valid_until": valid_until,
            "metadata": metadata,
        }

    def _make_fact(self, tool, tool_call_id, args, key, value, unit, label, valid_until, metadata):
        as_of = args["as_of"]
        canonical = json.dumps([tool, key, label, args, as_of], sort_keys=True, ensure_ascii=False, default=str)
        fact = {
            "fact_id": "f_" + sha1(canonical.encode()).hexdigest()[:12],
            "key": key,
            "value": value,
            "unit": unit,
            "label": label,
            "source_type": "bank_tool",
            "source_ref": tool_call_id,
            "tool": tool,
            "observed_at": as_of,
            "valid_until": valid_until,
            "confidence": 1.0,
            "status": "active",
            "metadata": metadata,
        }
        # ID ổn định trong một ngày; mỗi lần gọi có trace riêng trong get_tool_calls().
        self._facts[fact["fact_id"]] = deepcopy(fact)
        return fact

    def _record(self, tool, tool_call_id, args, data, facts, error, *, as_of=None):
        result = {
            "tool_call_id": tool_call_id,
            "tool": tool,
            "as_of": as_of,
            "data": data,
            "facts": facts,
            "error": error,
        }
        # Không lưu đối tượng context/token/session của backend vào trace model.
        trace_args = {key: value for key, value in args.items() if key != "context"}
        self._calls.append({"arguments": deepcopy(trace_args), **deepcopy(result)})
        return result

    def _live_fact(self, fact_id, as_of, keys=None):
        if not isinstance(fact_id, str) or fact_id not in self._facts:
            raise ValueError("fact_id was not issued by this instance; call the source tool first")
        fact = self._facts[fact_id]
        if not (_date(fact["observed_at"]) <= _date(as_of) <= _date(fact["valid_until"])):
            raise ValueError("fact expired or not yet valid; refresh the source tool")
        if fact["observed_at"] != as_of:
            raise ValueError("fact was issued on a different date; refresh the source tool for as_of")
        if keys is not None and fact["key"] not in keys:
            raise ValueError("fact has the wrong type")
        return fact

    @bank_tool
    def crm_lookup(self, customer_id, *, as_of, context=None):
        for customer in self._fixtures["customers"]:
            if customer_id == customer["customer_id"]:
                return {
                    "found": True,
                    **{key: customer[key] for key in ("customer_id", "cif", "full_name", "segment")},
                }, []
        return {"found": False}, []

    @bank_tool
    def get_rate(self, product, amount, term, segment, *, as_of):
        _choice(product, PRODUCTS - {"credit_card"}, "product")
        _choice(segment, SEGMENTS, "segment")
        _integer(amount, "amount", 1)
        _integer(term, "term", 1, 600)
        matches = [
            row
            for row in self._fixtures["rates"]
            if row["product"] == product
            and row["segment"] == segment
            and row["amount_min"] <= amount <= row["amount_max"]
            and row["term_min"] <= term <= row["term_max"]
            and self._active(row, as_of)
        ]
        if len(matches) != 1:
            raise ValueError("no unique active rate for this product/segment/amount/term")
        row = matches[0]
        specs = [
            self._spec(
                key,
                row[key],
                "%/năm",
                row["rate_id"],
                row["valid_until"],
                product=product,
                segment=segment,
                amount=amount,
                term=term,
            )
            for key in ("standard_rate", "floating_margin")
        ]
        return {"rate_id": row["rate_id"], "product": product}, specs

    @bank_tool
    def get_active_promotions(self, product, channel, *, as_of):
        _choice(product, PRODUCTS, "product")
        _choice(channel, CHANNELS, "channel")
        rows = sorted(
            [
                row
                for row in self._fixtures["promotions"]
                if row["product"] == product and channel in row["channels"] and self._active(row, as_of)
            ],
            key=lambda row: row["promo_id"],
        )
        specs = []
        for row in rows:
            key, value, unit = (
                ("promo_rate", row["rate"], "%/năm") if "rate" in row else ("promo_fee_waiver", row["fee_waiver"], "%")
            )
            specs.append(
                self._spec(key, value, unit, row["promo_id"], row["valid_until"], product=product, channel=channel)
            )
            specs.append(
                self._spec(
                    "promo_months",
                    row["promo_months"],
                    "tháng",
                    row["promo_id"],
                    row["valid_until"],
                    product=product,
                    channel=channel,
                )
            )
        return {"product": product, "channel": channel, "promotions": deepcopy(rows)}, specs

    @bank_tool
    def get_card_offer(self, income, segment, *, as_of):
        _integer(income, "income")
        _choice(segment, SEGMENTS, "segment")
        rows = [
            row for row in self._fixtures["card_products"] if row["segment"] == segment and self._active(row, as_of)
        ]
        if len(rows) != 1:
            raise ValueError("no unique active card product for segment")
        row = rows[0]
        upper_limit = min(row["card_limit_max"], income * row["income_multiplier"])
        if income < row["min_income"] or upper_limit < row["card_limit_min"]:
            return {"eligible": False, "reason": "income_below_min"}, []
        values = {key: row[key] for key in ("card_limit_min", "annual_fee", "fee_waiver_spend")}
        values["card_limit_max"] = upper_limit
        specs = [
            self._spec(key, value, "VND", row["card_id"], row["valid_until"], segment=segment)
            for key, value in values.items()
        ]
        return {"eligible": True, "card_id": row["card_id"], "approval_required": True}, specs

    @bank_tool
    def calc_installment(
        self, amount, term, rate, rate_fact_id, promo_months=0, after_rate=None, after_rate_fact_id=None, *, as_of
    ):
        _integer(amount, "amount", 1)
        _integer(term, "term", 1, 600)
        _integer(promo_months, "promo_months", 0, term)
        annual_rate = _rate(rate)
        source = self._live_fact(rate_fact_id, as_of, {"standard_rate", "promo_rate"})
        if annual_rate != Decimal(str(source["value"])):
            raise ValueError("rate does not match rate_fact_id")
        product = source["metadata"]["product"]
        rule_expiry = None
        if source["key"] == "standard_rate":
            if source["metadata"]["amount"] != amount or source["metadata"]["term"] != term:
                raise ValueError("standard rate fact was quoted for a different amount/term")
            if promo_months != 0:
                raise ValueError("promo_months requires a promo_rate fact")
        else:
            promotion = next(row for row in self._fixtures["promotions"] if row["promo_id"] == source["label"])
            if promo_months != min(promotion["promo_months"], term):
                raise ValueError("promo_months must match the promotion, capped at loan term")
            rule = self._rules(product, as_of)
            rule_expiry = rule["valid_until"]
            if (
                not rule["amount_min"] <= amount <= rule["amount_max"]
                or not rule["term_min"] <= term <= rule["term_max"]
            ):
                raise ValueError("amount/term outside supported product range")
        remaining_months = term - promo_months if promo_months else 0
        dependencies = [source]
        if remaining_months:
            later_rate = _rate(after_rate, "after_rate")
            after_source = self._live_fact(after_rate_fact_id, as_of, {"standard_rate"})
            if Decimal(str(after_source["value"])) != later_rate:
                raise ValueError("after_rate does not match after_rate_fact_id")
            if any(
                after_source["metadata"][key] != value
                for key, value in (("product", product), ("amount", amount), ("term", term))
            ):
                raise ValueError("after_rate fact must match product, amount and term")
            dependencies.append(after_source)
        elif after_rate is not None or after_rate_fact_id is not None:
            raise ValueError("after_rate is only used when a post-promotion period exists")
        with localcontext() as ctx:
            ctx.prec = 40
            first_payment = _payment(amount, term, annual_rate)
            if remaining_months:
                monthly_rate = annual_rate / 1200
                balance = Decimal(amount)
                for _ in range(promo_months):
                    balance = balance * (1 + monthly_rate) - first_payment
                second_payment = _payment(balance, remaining_months, later_rate)
                total = first_payment * promo_months + second_payment * remaining_months
            else:
                second_payment = None
                total = first_payment * term
        values = {
            "installment_promo" if promo_months else "installment_standard": _round_money(first_payment),
            "total_payment": _round_money(total),
        }
        if second_payment is not None:
            values["installment_after"] = _round_money(second_payment)
        expiry = min(fact["valid_until"] for fact in dependencies)
        if rule_expiry is not None:
            expiry = min(expiry, rule_expiry)
        origin = {"channel": source["metadata"]["channel"]} if "channel" in source["metadata"] else {}
        specs = [
            self._spec(
                key,
                value,
                "VND",
                "annuity_illustration",
                expiry,
                product=product,
                depends_on=[fact["fact_id"] for fact in dependencies],
                **origin,
            )
            for key, value in values.items()
        ]
        return {
            "illustrative_only": True,
            "method": "annuity_two_stage",
            "rounding_vnd": 1000,
            "assumptions": "Rates fixed within each stage; excludes fees. Total uses unrounded payments.",
        }, specs

    @bank_tool
    def check_suitability(self, product, income, debt, amount=None, term=None, property_value=None, *, as_of):
        _choice(product, PRODUCTS, "product")
        _integer(income, "income")
        _integer(debt, "debt")
        rule = self._rules(product, as_of)
        reasons = []
        if income < rule["min_income"]:
            reasons.append("income_below_min")
        proposed_payment = Decimal(0)
        ltv = None
        if product != "credit_card":
            _integer(amount, "amount", 1)
            _integer(term, "term", 1, 600)
            if not rule["amount_min"] <= amount <= rule["amount_max"]:
                reasons.append("amount_out_of_range")
            if not rule["term_min"] <= term <= rule["term_max"]:
                reasons.append("term_out_of_range")
            proposed_payment = _payment(amount, term, rule["assessment_rate"])
            if product == "home_loan":
                _integer(property_value, "property_value", 1)
                ltv = Decimal(amount) / Decimal(property_value)
                if ltv > Decimal(str(rule["max_ltv"])):
                    reasons.append("ltv_too_high")
            elif property_value is not None:
                raise ValueError("property_value only applies to home_loan")
        elif any(value is not None for value in (amount, term, property_value)):
            raise ValueError("credit_card check uses income and debt only")
        dti = (Decimal(debt) + proposed_payment) / Decimal(income) if income else None
        if dti is None or dti > Decimal(str(rule["max_dti"])):
            reasons.append("dti_too_high")
        # Không phát fact số: bot dùng ok/reasons để định tuyến; không đọc số chẩn đoán cho khách.
        return {
            "ok": not reasons,
            "reasons": reasons,
            "approval_required": True,
            "diagnostics": {
                "dti": float(dti) if dti is not None else None,
                "ltv": float(ltv) if ltv is not None else None,
                "assessment_monthly_payment": _round_money(proposed_payment),
            },
        }, []

    @bank_tool
    def get_disclosures(self, product, topics, *, as_of):
        _choice(product, PRODUCTS, "product")
        disclosure = self._fixtures["disclosures"][product]
        if not self._active(disclosure, as_of):
            raise ValueError("disclosures are not effective on as_of")
        if not isinstance(topics, list) or not topics:
            raise ValueError("topics must be a nonempty list")
        selected = []
        for topic in topics:
            _choice(topic, {"rate", "installment", "fee", "promo"}, "topic")
            if topic not in selected:
                selected.append(topic)
        return {"disclosures": [{"topic": topic, "text": disclosure[topic]} for topic in selected]}, []

    @bank_tool
    def send_summary(self, customer_id, channel, fact_ids, *, as_of, context=None):
        self._customer(customer_id)
        _choice(channel, CHANNELS, "channel")
        if not isinstance(fact_ids, list) or not fact_ids:
            raise ValueError("fact_ids must be a nonempty list")
        facts = []
        for fact_id in fact_ids:
            fact = self._live_fact(fact_id, as_of)
            if fact["metadata"].get("channel", channel) != channel:
                raise ValueError("promotion channel does not match delivery channel")
            if fact not in facts:
                facts.append(deepcopy(fact))
        message_id = "msg_" + uuid4().hex[:12]
        self._messages.append(
            {"message_id": message_id, "customer_id": customer_id, "channel": channel, "facts": facts, "as_of": as_of}
        )
        return {"message_id": message_id, "status": "mock_sent", "mock": True}, []

    @bank_tool
    def schedule_callback(self, customer_id, time, *, as_of, context=None):
        self._customer(customer_id)
        if not isinstance(time, str) or "T" not in time:
            raise ValueError("time must be ISO 8601 date-time with timezone")
        scheduled = datetime.fromisoformat(time)
        if scheduled.tzinfo is None or scheduled.utcoffset() is None:
            raise ValueError("time must include a timezone, e.g. +07:00")
        # Clock demo chỉ có ngày: chỉ chấp nhận ngày tương lai tại múi giờ lịch hẹn.
        if scheduled.date() <= _date(as_of):
            raise ValueError("callback must be on a later calendar day than as_of")
        task_id = "task_" + uuid4().hex[:12]
        self._callbacks.append(
            {"task_id": task_id, "customer_id": customer_id, "time": scheduled.isoformat(), "as_of": as_of}
        )
        return {"task_id": task_id, "time": scheduled.isoformat(), "status": "mock_scheduled", "mock": True}, []
