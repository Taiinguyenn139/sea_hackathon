"""Shared profile lookup and session access; profile selection is not proof."""


def can_access_memory(db, session):
    if session.get("verified"):
        return True
    customer = session["customer_id"]
    owns_channel_profile = db.one(
        "SELECT id FROM guest_customers WHERE id=? AND channel=? AND external_id=?",
        (customer, session.get("channel"), session.get("external_id")),
    )
    owns_phone_session = db.one(
        "SELECT session_id FROM phone_sessions WHERE session_id=? AND customer_id=?",
        (session.get("id"), customer),
    )
    return bool(owns_channel_profile or owns_phone_session)


def customer_profile(db, customer):
    from bank_tools.service import FIXTURES
    from harness.identity import resolve_identity

    customer = resolve_identity(customer) or customer
    profile = next((p for p in FIXTURES["customers"] if p["id"] == customer), None)
    if profile:
        return {**profile, "name_source": "demo_profile"}
    profile = db.one("SELECT id,name,NULL AS cif FROM customer_profiles WHERE id=?", (customer,))
    if profile:
        return {**profile, "name_source": "telegram_display"}
    profile = db.one("SELECT id,name,channel,NULL AS cif FROM guest_customers WHERE id=?", (customer,))
    if profile:
        channel = profile.pop("channel")
        source = "telegram_display" if channel == "telegram" else "demo_guest"
        return {**profile, "name_source": source}
    return None
