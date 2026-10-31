"""Read-only ground truth loader and independent numeric normalization."""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any


def content_hash(path: Path) -> str:
    files = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
    digest = hashlib.sha256()
    for file in files:
        digest.update((str(file.relative_to(path)) if path.is_dir() else file.name).encode())
        digest.update(b"\0")
        digest.update(file.read_bytes())
    return digest.hexdigest()


def read_document(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        value = json.loads(text)
    else:
        import yaml

        value = yaml.safe_load(text)
    if not isinstance(value, dict):
        raise TypeError(f"Expected mapping: {path}")
    return value


def numeric_claims(text: str) -> list[tuple[str, Decimal]]:
    """Extract typed financial claims, independent of backend renderer/facts."""
    pattern = r"(?<![\w])([+-]?\d[\d.,]*)\s*(%|tỷ|triệu|nghìn|đồng|tháng|năm)"
    claims = []
    for match in re.finditer(pattern, text.casefold()):
        number, unit = match.groups()
        # Vietnamese commas are decimals; dots in groups of 3 are thousands.
        if "," in number:
            number = number.replace(".", "").replace(",", ".")
        elif re.fullmatch(r"[+-]?\d{1,3}(?:\.\d{3})+", number):
            number = number.replace(".", "")
        number = number.rstrip(".")
        value = Decimal(number)
        if unit in ("tỷ", "triệu", "nghìn", "đồng"):
            value *= {"tỷ": 10**9, "triệu": 10**6, "nghìn": 1000, "đồng": 1}[unit]
            unit = "VND"
        elif unit == "năm":
            value *= 12
            unit = "months"
        elif unit == "tháng":
            unit = "months"
        claims.append((unit, value))
    return claims


class Oracle:
    def __init__(self, path: Path):
        self.path = path
        self.snapshot = read_document(path)
        self.hash = content_hash(path)

    def allowed_claims(
        self, product: str, as_of: str, channel: str, customer_claims: list[list[Any]]
    ) -> set[tuple[str, Decimal]]:
        allowed = {(unit, Decimal(str(value))) for unit, value in customer_claims}
        for row in self.snapshot["offers"]:
            if row["product"] != product or not row["valid_from"] <= as_of <= row["valid_until"]:
                continue
            if channel not in row["channels"]:
                continue
            allowed.update((unit, Decimal(str(value))) for unit, value in row["claims"])
        return allowed

    def expected_values(
        self, product: str, as_of: str, channel: str, loan: dict[str, Any] | None = None, *, card_income: int | None = None
    ) -> dict[str, Decimal]:
        rows = [
            r
            for r in self.snapshot["offers"]
            if r["product"] == product and r["valid_from"] <= as_of <= r["valid_until"] and channel in r["channels"]
        ]
        if not rows:
            return {}
        row = rows[-1]
        if product == "credit_card":
            return {
                "card_limit_min": Decimal(10000000),
                "card_limit_max": (
                    min(Decimal(100000000), Decimal(card_income) * 3)
                    if card_income is not None else Decimal(100000000)
                ),
                "annual_fee": Decimal(300000),
                "fee_waiver_spend": Decimal(30000000),
                "promo_fee_waiver": Decimal(100),
                "promo_months": Decimal(12),
            }
        rates = [Decimal(str(value)) for unit, value in row["claims"] if unit == "%"]
        months = next(Decimal(str(value)) for unit, value in row["claims"] if unit == "months")
        values = {
            "promo_rate": rates[0],
            "standard_rate": rates[1],
            "floating_margin": rates[2],
            "promo_months": months,
        }
        if loan:
            values.update(
                installments(Decimal(str(loan["amount"])), int(loan["term"]), rates[0], int(months), rates[1])
            )
        return values


def installments(
    principal: Decimal, months: int, promo_rate: Decimal, promo_months: int, later_rate: Decimal
) -> dict[str, Decimal]:
    """Independent amortization oracle: iterate outstanding balance month by month."""
    from decimal import ROUND_HALF_UP, localcontext

    if principal <= 0 or months <= 0 or promo_rate < 0 or later_rate < 0:
        raise ValueError("Invalid loan parameters")
    with localcontext() as context:
        context.prec = 50
        period = min(promo_months, months)
        monthly = promo_rate / Decimal(1200)
        discount = sum((1 + monthly) ** (-i) for i in range(1, months + 1))
        first = principal / discount
        balance = principal
        for _ in range(period):
            balance += balance * monthly - first
        remaining = months - period
        after_monthly = later_rate / Decimal(1200)
        after_discount = sum((1 + after_monthly) ** (-i) for i in range(1, remaining + 1))
        later = balance / after_discount if remaining else first
        total = first * period + later * remaining

        def rounded(value: Decimal) -> Decimal:
            return (value / 1000).quantize(Decimal(1), rounding=ROUND_HALF_UP) * 1000

        return {
            "installment_promo": rounded(first),
            "installment_after": rounded(later),
            "total_payment": rounded(total),
        }
