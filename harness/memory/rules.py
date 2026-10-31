"""Deterministic offline/evaluation backend; never a fallback for OpenAI memory."""

import re
from decimal import Decimal


def extract(text):
    low = text.lower()
    slots = {}
    if any(x in low for x in ["mua nhà", "vay nhà", "home_loan"]):
        slots["product"] = ("home_loan", "")
    elif any(x in low for x in ["tiêu dùng", "personal_loan"]):
        slots["product"] = ("personal_loan", "")
    elif any(x in low for x in ["thẻ", "credit_card"]):
        slots["product"] = ("credit_card", "")
    previous_money_end = 0
    for match in re.finditer(r"(-?\d+(?:[.,]\d+)?)\s*(tỷ|triệu|nghìn|trieu|ty)", low):
        value = (
            Decimal(match[1].replace(",", "."))
            * {"tỷ": 10**9, "ty": 10**9, "triệu": 10**6, "trieu": 10**6, "nghìn": 1000}[match[2]]
        )
        before = low[max(previous_money_end, match.start() - 120) : match.start()]
        previous_money_end = match.end()
        cues = {"income": ["thu nhập", "lương"], "debt": ["nợ", "trả nợ"], "property_value": ["giá nhà", "giá trị"]}
        nearest = [(before.rfind(cue), key) for key, words in cues.items() for cue in words if cue in before]
        key = max(nearest)[1] if nearest else "amount"
        slots[key] = (str(value.quantize(Decimal(1))), "đồng")
    if re.search(r"không (?:có |còn )?nợ|(?:nợ|trả nợ)(?: hàng tháng)?\s*(?:là |: )?0(?:\s|$|[.,;])", low):
        slots["debt"] = ("0", "đồng")
    term = re.search(r"(?:kỳ hạn|thời hạn|trong|vay)\s*(\d+)\s*(năm|tháng)", low)
    if not term:
        term = re.search(r"(\d+)\s*(năm|tháng)", low)
    if term:
        slots["term"] = (str(int(term[1]) * (12 if term[2] == "năm" else 1)), "tháng")
    if any(x in low for x in ["lãi cao", "lãi sau", "tăng lãi"]):
        slots["objection"] = ("rate_high", "")
    if any(x in low for x in ["chồng", "vợ", "gia đình"]):
        slots["objection"] = ("need_spouse", "")
    if any(x in low for x in ["gọi lại", "hẹn", "liên hệ lại"]):
        slots["commitment"] = ("Khách yêu cầu liên hệ lại", "")
    return slots


def corrects(text, key):
    """Recognize an explicit current-value update for this field, not another clause."""
    clauses = re.split(r"[;\n]|\.(?!\d)|,(?!\d)|\s+(?:và|nhưng)\s+", text.lower())
    for clause in clauses:
        if key not in extract(clause):
            continue
        # A proposed or negated change must not resolve conflicting statements.
        intent = re.sub(r"không (?:có |còn )?nợ", "", clause)
        if "?" in clause or re.search(
            r"\b(?:nếu|giả sử|giả định|ước gì|mong muốn|dự kiến|sẽ|chưa|không)\b", intent,
        ):
            continue
        if re.search(
            r"\b(?:xác nhận|đính chính|sửa lại|chính xác|cập nhật|"
            r"tăng (?:lên|thành)|giảm (?:xuống|còn)|đổi (?:sang|thành)|"
            r"hiện tại|bây giờ|hiện nay|mới là)\b", clause,
        ):
            return True
    return False


