"""Demo identity registry; fixture links are never authentication evidence.

Only the identity input file is read here. Scenario sessions, reference answers
and their verification flags must not grant permissions in the running app.
"""

import re
import secrets
from pathlib import Path

from simulators.identity_dataset import normalize_phone
from simulators.identity_schemas import IdentityFile

IDENTITY_PATH = Path(__file__).resolve().parents[1] / "simulators/datasets/identity_v1/identities.json"

IDENTITY_TOOL_DEFINITION = {
    "name": "verify_identity",
    "description": (
        "Nhận diện một trong 10 hồ sơ HƯ CẤU bằng số điện thoại khách vừa tự khai trong lượt hiện tại. "
        "Không dùng số của người nhà hoặc phỏng đoán từ lịch sử. Sau thành công, trả call brief và mở "
        "các tool cần nhận diện; đây không phải đăng nhập ngân hàng hay xác thực OTP."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "phone": {"type": "string", "minLength": 1, "maxLength": 64},
            "as_of": {"type": "string", "format": "date"},
        },
        "required": ["phone", "as_of"],
        "additionalProperties": False,
    },
}


def _load_profiles():
    source = IdentityFile.model_validate_json(IDENTITY_PATH.read_text(encoding="utf-8"))
    rows = []
    identifiers = {}
    bank_ids = set()
    for identity in source.identities:
        customer = identity.runtime_profile or identity.user_id
        if not customer or customer.strip() != customer:
            raise ValueError("Runtime identity keys must be nonempty and canonical")
        if identity.bank_customer_id in bank_ids:
            raise ValueError("Duplicate bank customer mapping in identity fixtures")
        bank_ids.add(identity.bank_customer_id)
        row = {
            "id": customer,
            "user_id": identity.user_id,
            "phone": identity.phone,
            "bank_customer_id": identity.bank_customer_id,
            "name": identity.full_name,
            "segment": identity.segment,
            "runtime_profile": identity.runtime_profile,
        }
        # Both legacy aliases and canonical phones resolve to one database key.
        for identifier in {identity.user_id, customer}:
            if identifier in identifiers:
                raise ValueError("Duplicate phone or runtime alias in identity fixtures")
            identifiers[identifier] = row
        rows.append(row)
    return rows, identifiers


_PROFILES, _IDENTIFIERS = _load_profiles()


def profiles():
    """Return copies so a caller cannot mutate the verification registry."""
    return [dict(profile) for profile in _PROFILES]


def get_profile(identifier):
    """Look up an alias or normalized phone without authenticating its owner."""
    if not isinstance(identifier, str):
        return None
    if identifier not in _IDENTIFIERS:
        try:
            identifier = normalize_phone(identifier)
        except (TypeError, ValueError):
            return None
    profile = _IDENTIFIERS.get(identifier)
    return dict(profile) if profile else None


def resolve_identity(identifier):
    profile = get_profile(identifier)
    return profile["id"] if profile else None


def verify_identity(identifier, phone):
    """Match a demo phone to the bound fixture; this is not a bank login."""
    profile = get_profile(identifier)
    if not profile:
        return False
    try:
        submitted = normalize_phone(phone)
    except (TypeError, ValueError):
        return False
    # Unlike profile selection, an alias is never accepted as phone proof.
    return secrets.compare_digest(profile["phone"], submitted)


_PHONE_CLAIM = re.compile(r"(?<![\w+])(?:0|\+84)(?:[ \t-]*[0-9]){9}(?!\w|[ \t-]*[0-9])")


def extract_phone_claims(text):
    """Find complete local/+84 phone tokens, without consuming loan amounts."""
    phones = []
    for match in _PHONE_CLAIM.finditer(text):
        try:
            phone = normalize_phone(match[0])
        except (TypeError, ValueError):
            continue
        if phone not in phones:
            phones.append(phone)
    return phones


def validate_own_phone_claim(text, phone):
    """Validate a model's proposed tool argument; never route a chat turn."""
    try:
        submitted = normalize_phone(phone)
    except (TypeError, ValueError):
        raise ValueError("invalid_phone: Vui lòng gửi số điện thoại theo định dạng 0 hoặc +84.") from None
    candidates = extract_phone_claims(text)
    if len(candidates) != 1:
        raise ValueError("ambiguous_phone: Vui lòng gửi một số điện thoại duy nhất của chính anh/chị.")
    if candidates[0] != submitted:
        raise ValueError("phone_not_in_current_message: Không dùng số điện thoại ngoài lời khách vừa khai.")
    low = text.casefold()
    relative = re.search(
        r"(?:số(?: điện thoại| đt)?|điện thoại|sđt|phone)\s+(?:của\s+)?(?:vợ|chồng|mẹ|bố|ba|cha|anh trai|chị gái|em trai|em gái|con|bạn|người nhà)\b"
        r"|(?:vợ|chồng|mẹ|bố|ba|cha|anh trai|chị gái|em trai|em gái|người nhà)\s+(?:tôi|mình|em|anh|chị)\s+(?:có|dùng|là|số|điện thoại)",
        low,
    )
    own = re.search(r"(?:số(?: điện thoại| đt)?|điện thoại|sđt|phone)\s+(?:của\s+)?(?:tôi|mình|em|anh|chị)\b", low)
    standalone = _PHONE_CLAIM.fullmatch(text.strip()) is not None
    if relative or not (own or standalone):
        raise ValueError("own_phone_required: Chỉ nhận diện từ số điện thoại khách tự khai là của chính mình.")
    return submitted
