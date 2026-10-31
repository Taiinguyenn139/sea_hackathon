"""Verified phone keys shared by customer profiles across chat channels."""

import hashlib
import secrets
import time

import phonenumbers
from fastapi import HTTPException

from bank_tools.service import ident, now


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def normalize_phone(value):
    try:
        number = phonenumbers.parse(value, "VN")
    except phonenumbers.NumberParseException:
        raise HTTPException(422, "Số điện thoại không hợp lệ.") from None
    if number.extension or not phonenumbers.is_valid_number(number):
        raise HTTPException(422, "Số điện thoại không hợp lệ.")
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


class PhoneIdentity:
    def __init__(self, db):
        self.db = db

    def bind(self, customer, name, target, contact, sender_id):
        if not isinstance(sender_id, int) or contact.get("user_id") != sender_id or str(sender_id) != target:
            raise HTTPException(400, "Vui lòng bấm Chia sẻ số điện thoại để gửi số của chính anh/chị.")
        phone = normalize_phone(contact.get("phone_number", ""))
        with self.db.transaction() as c:
            existing = c.execute("SELECT * FROM customer_profiles WHERE phone_e164=?", (phone,)).fetchone()
            current = c.execute("SELECT * FROM customer_profiles WHERE id=?", (customer,)).fetchone()
            if (existing and (existing["id"] != customer or existing["telegram_id"] != target)) or (
                current and current["phone_e164"] != phone
            ):
                raise HTTPException(409, "Số điện thoại đã có liên kết khác. Anh/chị vui lòng liên hệ hỗ trợ.")
            c.execute(
                "INSERT OR IGNORE INTO customer_profiles VALUES(?,?,?,?,?)", (customer, phone, target, name, now())
            )
            c.execute("INSERT OR REPLACE INTO identity_links VALUES('telegram',?,?)", (target, customer))
        return phone

    def begin(self, raw_phone, source):
        phone = normalize_phone(raw_phone)
        current = time.time()
        identity = "phone:" + digest(phone)
        source = "phone-login:" + source
        challenge = secrets.token_urlsafe(32)
        code = f"{secrets.randbelow(1_000_000):06d}"
        with self.db.transaction() as c:
            phone_attempts = c.execute(
                "SELECT count(*) FROM auth_attempts WHERE identity=? AND at>?", (identity, current - 900)
            ).fetchone()[0]
            source_attempts = c.execute(
                "SELECT count(*) FROM auth_attempts WHERE source=? AND at>?", (source, current - 900)
            ).fetchone()[0]
            if phone_attempts >= 3 or source_attempts >= 10:
                raise HTTPException(429, "Vui lòng chờ ít phút trước khi yêu cầu mã mới.")
            c.execute("INSERT INTO auth_attempts VALUES(?,?,?)", (identity, source, current))
            profile = c.execute(
                "SELECT p.id,p.telegram_id AS external_id FROM customer_profiles p JOIN identity_links l "
                "ON l.customer_id=p.id AND l.channel='telegram' AND l.external_id=p.telegram_id "
                "WHERE p.phone_e164=?",
                (phone,),
            ).fetchone()
            c.execute(
                "UPDATE phone_challenges SET consumed_at=? WHERE phone_e164=? AND consumed_at IS NULL", (current, phone)
            )
            c.execute(
                "INSERT INTO phone_challenges(id,customer_id,phone_e164,telegram_target,code_hash,expires,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    challenge,
                    profile["id"] if profile else None,
                    phone,
                    profile["external_id"] if profile else None,
                    digest(challenge + ":" + code),
                    current + 600,
                    current,
                ),
            )
            if profile:
                c.execute(
                    "INSERT INTO outbox(id,channel,target,text,status) VALUES(?,?,?,?,?)",
                    (
                        ident("out_", ["phone-login", challenge]),
                        "telegram",
                        profile["external_id"],
                        (
                            f"Mã xác nhận để tiếp tục hội thoại trên website: {code}. "
                            "Mã có hiệu lực trong 10 phút. Chỉ nhập mã trên website khi chính anh/chị yêu cầu."
                        ),
                        "pending",
                    ),
                )
        # Same response for unknown numbers; customer information is disclosed only after proof.
        return {"challenge_id": challenge, "expires_in": 600}

    def confirm(self, challenge, code):
        current = time.time()
        customer = None
        with self.db.transaction() as c:
            row = c.execute("SELECT * FROM phone_challenges WHERE id=?", (challenge,)).fetchone()
            if row and row["consumed_at"] is None and row["expires"] > current and row["attempts"] < 5:
                c.execute("UPDATE phone_challenges SET attempts=attempts+1 WHERE id=?", (challenge,))
                if row["customer_id"] and secrets.compare_digest(row["code_hash"], digest(challenge + ":" + code)):
                    # Recheck the binding in case an administrator changed the channel link.
                    profile = c.execute(
                        "SELECT p.id FROM customer_profiles p JOIN identity_links l ON l.customer_id=p.id "
                        "AND l.channel='telegram' AND l.external_id=p.telegram_id "
                        "WHERE p.id=? AND p.phone_e164=? AND p.telegram_id=?",
                        (row["customer_id"], row["phone_e164"], row["telegram_target"]),
                    ).fetchone()
                    if profile:
                        customer = profile["id"]
                        c.execute("UPDATE phone_challenges SET consumed_at=? WHERE id=?", (current, challenge))
        if not customer:
            raise HTTPException(400, "Mã xác nhận không hợp lệ hoặc đã hết hạn. Vui lòng yêu cầu mã mới.")
        return customer
