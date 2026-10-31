"""Thông tin tin cậy do backend lấy từ module đăng nhập/xác minh bên ngoài.

Không tạo AuthContext từ JSON của người dùng hoặc tham số tool do model sinh.
Module xác thực chịu trách nhiệm hiệu lực, hết hạn và thu hồi của phiên.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AuthContext:
    customer_id: str | None = None
    logged_in: bool = False
    identity_verified: bool = False

    def __post_init__(self):
        if type(self.logged_in) is not bool or type(self.identity_verified) is not bool:
            raise ValueError("auth states must be booleans")
        if self.customer_id is not None and (
            not isinstance(self.customer_id, str)
            or not self.customer_id.strip()
            or self.customer_id != self.customer_id.strip()
        ):
            raise ValueError("customer_id must be a nonempty canonical string")
        if (self.logged_in or self.identity_verified) and self.customer_id is None:
            raise ValueError("authenticated/verified context requires customer_id")


TOOL_ACCESS = {
    "crm_lookup": "login",
    "get_rate": "public",
    "get_active_promotions": "public",
    "get_card_offer": "public",
    "get_insurance_offer": "public",
    "calc_installment": "public",
    "check_suitability": "public",
    "get_disclosures": "public",
    "send_summary": "identified",
    "schedule_callback": "identified",
}


class AccessDenied(Exception):
    """Lỗi quyền được trả trong ToolResult.error, không thực hiện tool."""


def can_use(tool, context=None):
    if context is not None and not isinstance(context, AuthContext):
        raise TypeError("context must be an AuthContext supplied by the backend")
    access = TOOL_ACCESS[tool]
    if access == "public":
        return True
    if context is None or context.customer_id is None:
        return False
    if access == "login":
        return context.logged_in
    return context.logged_in or context.identity_verified


def require_access(tool, context, customer_id):
    if context is not None and not isinstance(context, AuthContext):
        raise AccessDenied("forbidden: auth context must come from the backend")
    if not can_use(tool, context):
        condition = "login required" if TOOL_ACCESS[tool] == "login" else "login or identity verification required"
        raise AccessDenied(f"authentication_required: {condition}")
    if customer_id != context.customer_id:
        raise AccessDenied("forbidden: customer_id does not match the current authenticated/verified customer")
