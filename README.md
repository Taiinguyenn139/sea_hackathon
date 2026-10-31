# SEA Hackathon — Bank Tools giả lập

Bộ **9 tool ngân hàng** cho chatbot Memory Harness, kèm dữ liệu YAML, mô tả
tool bằng JSON Schema và ví dụ tích hợp chạy trực tiếp. Toàn bộ khách hàng,
sản phẩm, lãi suất, phí và điều kiện đều **hư cấu**, không dùng tư vấn tài chính thật.

## Chạy nhanh

Yêu cầu Python 3.10 trở lên. Chạy tại thư mục gốc repo:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m examples.demo
python -m unittest discover -s tests -v
```

Sau khi cài dependency, demo và test không cần mạng, LLM hoặc API key.

```python
from bank_tools import BankTools, get_tool_definitions

bank = BankTools()
result = bank.get_active_promotions(
    product="home_loan", channel="zalo", as_of="2026-10-31",
)
print(result)

# Khi agent trả tên tool và tham số:
result = bank.call("get_active_promotions", {
    "product": "home_loan", "channel": "zalo", "as_of": "2026-10-31",
})
definitions = get_tool_definitions()  # 6 tool công khai khi chưa xác thực
```

Mỗi tool bắt buộc nhận `as_of` (ngày gọi `YYYY-MM-DD`) do backend cung cấp.
Dữ liệu có hạn hiệu lực được kiểm tra theo ngày này. Sang ngày mới phải tra
lại fact trước khi tính trả góp hoặc gửi tóm tắt. Ngày gọi giúp phát hiện dữ
liệu hết hạn; muốn có số liệu mới vẫn cần cập nhật các file YAML.
Xác minh danh tính do module của nhóm khác phụ trách; package không chứa
ngày sinh hay tool xác minh.

## Các tool có sẵn

| Tool | Chức năng | Điều kiện |
| --- | --- | --- |
| `crm_lookup` | Đọc hồ sơ của chính khách trong phiên | Đã đăng nhập |
| `get_rate` | Tra lãi suất chuẩn và biên độ theo phân khúc | Công khai |
| `get_active_promotions` | Lọc ưu đãi theo sản phẩm, kênh, ngày giả lập | Công khai |
| `get_card_offer` | Minh họa gói thẻ theo thu nhập nhập vào | Công khai |
| `calc_installment` | Tính trả góp minh họa | Công khai |
| `check_suitability` | Sàng lọc từ dữ liệu khách tự cung cấp | Công khai |
| `get_disclosures` | Lấy câu lưu ý bắt buộc | Công khai |
| `send_summary` | Ghi nhận gửi tóm tắt **giả lập** | Đăng nhập hoặc đã xác minh danh tính |
| `schedule_callback` | Ghi nhận lịch gọi lại **giả lập** | Đăng nhập hoặc đã xác minh danh tính |

Backend tạo `AuthContext` từ module đăng nhập/xác minh của nhóm. Tool riêng
tư chỉ thao tác với `customer_id` khớp context; agent không được tự truyền
quyền hoặc chọn khách khác. Ví dụ sau khi module xác minh đã thành công:

```python
from bank_tools import AuthContext

# Chỉ backend được tạo context từ kết quả xác minh tin cậy của phiên hiện tại.
context = AuthContext(customer_id="cus_001", identity_verified=True)
definitions = get_tool_definitions(context=context)  # 8 tool; chưa có crm_lookup
result = bank.call("schedule_callback", {
    "time": "2026-11-02T14:00:00+07:00", "as_of": "2026-10-31",
}, context=context)  # Dispatcher tự chèn customer_id, không lấy từ model.
```

Sau đăng nhập, backend dùng `AuthContext(customer_id=..., logged_in=True)`;
agent thấy đủ 9 tool. Kiểm tra quyền áp dụng cả với hàm gọi trực tiếp. Khi
logout hoặc xác minh hết hiệu lực, backend bỏ context và cập nhật danh sách
tool. AuthContext không tự thực hiện đăng nhập hoặc kiểm tra token.

## Tài liệu bàn giao

- [Hướng dẫn tích hợp và hợp đồng dữ liệu](docs/INTEGRATION.md): cách gọi, cấu trúc kết quả, tham số và xử lý lỗi.
- [Hướng dẫn dữ liệu giả lập](docs/DATA.md): 10 khách hàng, bảng lãi/ưu đãi, đơn vị và ca demo.
- [Demo đủ 9 tool](examples/demo.py): mẫu để người làm agent dùng ngay.
- [Kiểm tra phân quyền](tests/test_authorization.py): đăng nhập, xác minh và chặn sai khách.
- [Bộ kiểm tra](tests/test_bank_tools.py): kiểm tra tiền, ngày hết hạn, nguồn fact và luồng tích hợp.

## Cấu trúc

```text
bank_tools/
  __init__.py          # API: BankTools, AuthContext, get_tool_definitions
  auth.py              # Phân quyền từ trạng thái backend tin cậy
  core.py              # 9 tool, ngày theo từng lời gọi, lỗi tiêm, trace
  catalog.py           # Mô tả tool + JSON Schema tham số
  fixtures/
    customers.yaml
    rates.yaml
    promotions.yaml
    card_products.yaml
    suitability_rules.yaml
    disclosures.yaml
    demo_scenarios.yaml
docs/
examples/
tests/
```

Đây là thư viện Python đồng bộ, chưa có HTTP server, chatbot, memory store,
guardrail hoặc giao diện. Dùng cùng process thì import trực tiếp; chatbot chạy
ngôn ngữ/process khác cần một lớp API bọc thư viện này.
