"""JSON Schema độc lập SDK để agent đăng ký các tool."""

from copy import deepcopy

from .auth import can_use


def _field(kind, description, **constraints):
    return {"type": kind, "description": description, **constraints}


PRODUCT = _field("string", "Mã sản phẩm", enum=["home_loan", "personal_loan", "credit_card", "health_insurance"])
LOAN = {**PRODUCT, "enum": ["home_loan", "personal_loan"]}
CREDIT = {**PRODUCT, "enum": ["home_loan", "personal_loan", "credit_card"]}
SEGMENT = _field(
    "string",
    "Phân khúc dùng để tính thử theo bảng giá công khai; không chứng minh khách thuộc phân khúc đó",
    enum=["mass", "priority"],
)
CHANNEL = _field("string", "Kênh liên hệ", enum=["hotline", "zalo", "web", "telegram"])
AMOUNT = _field("integer", "Số tiền VND, không dùng chuỗi như '2 tỷ'", minimum=1, maximum=100_000_000_000)
TERM = _field("integer", "Kỳ hạn theo THÁNG, không phải năm", minimum=1, maximum=600)
MONEY = _field("integer", "Số tiền VND", minimum=0, maximum=100_000_000_000)
RATE = _field("number", "Lãi suất %/năm; ví dụ 6.9, không phải 0.069", minimum=0, maximum=100)
AS_OF = _field("string", "Ngày gọi YYYY-MM-DD do backend cung cấp; dùng để kiểm tra hiệu lực dữ liệu", format="date")


def _tool(name, description, properties, required):
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {**properties, "as_of": AS_OF},
            "required": [*required, "as_of"],
            "additionalProperties": False,
        },
    }


_DEFINITIONS = [
    _tool(
        "crm_lookup",
        "Chỉ sau đăng nhập: lấy hồ sơ của chính khách trong phiên. Backend tự truyền customer_id; không tra khách khác bằng tên/CIF/định danh.",
        {},
        [],
    ),
    _tool(
        "get_rate",
        "Lấy lãi suất chuẩn và biên độ còn hiệu lực theo sản phẩm, số tiền, tháng và phân khúc. Không cộng biên độ vào standard_rate.",
        {
            "product": LOAN,
            "amount": AMOUNT,
            "term": TERM,
            "segment": SEGMENT,
        },
        ["product", "amount", "term", "segment"],
    ),
    _tool(
        "get_active_promotions",
        "Lấy mọi ưu đãi còn hạn, đúng sản phẩm và kênh. Mảng rỗng nghĩa là không có ưu đãi; không phải lỗi.",
        {
            "product": PRODUCT,
            "channel": CHANNEL,
        },
        ["product", "channel"],
    ),
    _tool(
        "get_card_offer",
        "Tra gói thẻ và khoảng hạn mức minh họa theo thu nhập tháng và phân khúc. Không phải phê duyệt tín dụng.",
        {
            "income": MONEY,
            "segment": SEGMENT,
        },
        ["income", "segment"],
    ),
    _tool(
        "get_insurance_offer",
        "Tra gói bảo hiểm sức khỏe cá nhân demo còn hiệu lực: phí năm, hạn mức, thời hạn, thời gian chờ và độ tuổi. Không phải quyết định chấp nhận bảo hiểm hoặc bồi thường.",
        {},
        [],
    ),
    _tool(
        "calc_installment",
        "Tính trả góp đều, tối đa hai giai đoạn. Phải dùng fact lãi suất đã lấy trong cùng instance; nếu đổi lãi sau ưu đãi cần cả after_rate_fact_id. Không áp dụng cho thẻ.",
        {
            "amount": AMOUNT,
            "term": TERM,
            "rate": RATE,
            "rate_fact_id": _field("string", "fact_id của standard_rate hoặc promo_rate từ tool"),
            "promo_months": _field("integer", "Số tháng ưu đãi; mặc định 0", minimum=0, maximum=600),
            "after_rate": RATE,
            "after_rate_fact_id": _field(
                "string", "fact_id standard_rate khớp after_rate; bắt buộc nếu có giai đoạn sau ưu đãi"
            ),
        },
        ["amount", "term", "rate", "rate_fact_id"],
    ),
    _tool(
        "check_suitability",
        "Sàng lọc minh họa theo thu nhập, tỷ lệ trả nợ tháng DTI và tỷ lệ vay/giá trị nhà LTV. Không phải phê duyệt. Vay cần amount+term; vay nhà cần property_value.",
        {
            "product": CREDIT,
            "income": MONEY,
            "debt": MONEY,
            "amount": AMOUNT,
            "term": TERM,
            "property_value": AMOUNT,
        },
        ["product", "income", "debt"],
    ),
    _tool(
        "get_disclosures",
        "Lấy nguyên văn lưu ý bắt buộc theo sản phẩm và chủ đề; chèn vào câu trả lời khi nêu lãi, phí, trả góp hoặc ưu đãi.",
        {
            "product": PRODUCT,
            "topics": _field(
                "array",
                "Ít nhất một chủ đề",
                minItems=1,
                items={"type": "string", "enum": ["rate", "installment", "fee", "promo", "coverage", "exclusions"]},
            ),
        },
        ["product", "topics"],
    ),
    _tool(
        "send_summary",
        "Sau đăng nhập HOẶC xác minh danh tính: GIẢ LẬP gửi tóm tắt cho chính khách trong phiên. Backend tự truyền customer_id. Chỉ nhận fact hợp lệ trong ngày; không gọi Zalo hay SMS.",
        {
            "channel": CHANNEL,
            "fact_ids": _field("array", "Các fact đã dùng trong câu trả lời", minItems=1, items={"type": "string"}),
        },
        ["channel", "fact_ids"],
    ),
    _tool(
        "schedule_callback",
        "Sau đăng nhập HOẶC xác minh danh tính: GIẢ LẬP ghi lịch cho chính khách trong phiên. Backend tự truyền customer_id. Không gửi thông báo; hỏi khách xác nhận thời gian trước khi gọi.",
        {
            "time": _field("string", "ISO 8601 có múi giờ, ví dụ 2026-11-02T14:00:00+07:00", format="date-time"),
        },
        ["time"],
    ),
]

TOOL_NAMES = frozenset(tool["name"] for tool in _DEFINITIONS)


def get_tool_definitions(*, context=None):
    """Ẩn tool chưa đủ quyền: khách ẩn danh 7, đã verify 9, đã login 10."""
    return deepcopy([tool for tool in _DEFINITIONS if can_use(tool["name"], context)])
