"""Deterministic offline/corpus parsing; never a fallback for live OpenAI memory."""

import re
from decimal import Decimal

_MONEY = re.compile(
    r"(?<![\w.])-?(?:\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)\s*(?:tỷ|triệu|nghìn|trieu|ty|đồng|vnd)\b"
)
_CLAUSES = re.compile(r"(?<!\d)[.;!?]|[.;!?](?!\d)|,(?!\d)")


def _money_slots(text):
    slots = {}
    for clause in _CLAUSES.split(text):
        previous_end = 0
        for match in _MONEY.finditer(clause):
            token = re.fullmatch(r"(-?[\d.,]+)\s*(tỷ|triệu|nghìn|trieu|ty|đồng|vnd)", match[0])
            number, unit = token.groups()
            if unit in ("đồng", "vnd") and not re.fullmatch(r"-?(?:\d{1,3}(?:[.,]\d{3})+|\d+)", number):
                continue
            number = re.sub(r"[.,]", "", number) if re.fullmatch(r"-?\d{1,3}(?:[.,]\d{3})+", number) else number.replace(",", ".")
            value = Decimal(number) * {
                "tỷ": 10**9, "ty": 10**9, "triệu": 10**6, "trieu": 10**6,
                "nghìn": 1000, "đồng": 1, "vnd": 1,
            }[unit]
            before = clause[previous_end:match.start()]
            after = clause[match.end():match.end() + 40]
            previous_end = match.end()
            if any(cue in before for cue in ("đừng", "không tự", "không lấy lại", "không được", "chưa nhận")):
                continue
            cues = {
                "income": ["thu nhập", "lương"], "debt": ["nợ", "trả nợ"],
                "property_value": ["giá nhà", "giá trị", "căn nhà"], "amount": ["khoản vay", "vay", "ngân sách"],
            }
            nearest = [(before.rfind(cue), key) for key, words in cues.items() for cue in words if cue in before]
            key = max(nearest)[1] if nearest else "amount"
            if re.search(r"(?<!mua )\bnhà\s*(?:giá|là|vẫn)?\s*$", before):
                key = "property_value"
            if not nearest and (re.match(r"\s*thu nhập", after) or ("khai" in before and "mỗi tháng" in after)):
                key = "income"
            if key == "amount" and any(cue in before for cue in ("hỗ trợ", "giúp thêm", "góp thêm", "cho thêm")):
                continue
            if key == "amount" and any(cue in clause for cue in ("hư cấu", "%", "ưu đãi")) and not any(
                cue in before for cue in ("vay", "ngân sách", "cùng", "phương án")
            ):
                continue
            slots[key] = (str(value.quantize(Decimal(1))), "đồng")
    return slots


def extract(text):
    low = text.lower()
    slots = {}
    if any(x in low for x in ["mua nhà", "vay nhà", "home_loan"]):
        slots["product"] = ("home_loan", "")
    elif any(x in low for x in ["tiêu dùng", "personal_loan"]):
        slots["product"] = ("personal_loan", "")
    elif any(x in low for x in ["thẻ", "credit_card"]):
        slots["product"] = ("credit_card", "")
    slots.update(_money_slots(low))
    if re.search(r"không (?:có |còn )?nợ|(?:nợ|trả nợ)(?: hàng tháng)?\s*(?:là |: )?0(?:\s|$|[.,;])", low):
        slots["debt"] = ("0", "đồng")
    for clause in _CLAUSES.split(low):
        # A promotion's duration is a bank claim, not the customer's loan term.
        if any(cue in clause for cue in ("%", "ưu đãi", "hư cấu", "trả sớm", "trả bớt")):
            continue
        term = re.search(r"(\d+)\s*(năm|tháng)", clause)
        if term:
            slots["term"] = (str(int(term[1]) * (12 if term[2] == "năm" else 1)), "tháng")
    if any(x in low for x in ["lãi cao", "lãi sau", "tăng lãi"]):
        slots["objection"] = ("rate_high", "")
    if any(x in low for x in ["chồng", "vợ", "gia đình"]):
        slots["objection"] = ("need_spouse", "")
    for clause in _CLAUSES.split(low):
        if any(x in clause for x in ["gọi lại", "hẹn", "liên hệ lại"]) and not re.search(
            r"(?:đừng|không|chưa)\b.*(?:hẹn|gọi|liên hệ)|(?:tự|sẽ)\s*(?:chủ động\s*)?liên hệ", clause,
        ):
            slots["commitment"] = ("Khách yêu cầu liên hệ lại", "")
    if re.search(r"\btôi (?:sẽ |quyết định |muốn )?(?:hoãn|dừng kế hoạch|chưa vay|không vay nữa)\b", low):
        slots["loan_status"] = ("tạm hoãn kế hoạch vay", "")
    return slots


def corrects(text, key):
    """Recognize an explicit current-value update for this field, not another clause."""
    clauses = re.split(r"[;\n]|\.(?!\d)|,(?!\d)|\s+(?:và|nhưng)\s+", text.lower())
    # An explicit confirmation of the whole new request also covers the
    # comma-separated fields in that sentence, without extending to later ones.
    clauses.extend(sentence for sentence in re.split(r"[;\n]|\.(?!\d)", text.lower())
                   if re.search(r"\bxác nhận nhu cầu mới\b", sentence))
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
