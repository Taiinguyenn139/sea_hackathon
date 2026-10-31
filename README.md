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
definitions = get_tool_definitions()  # 9 mô tả, độc lập SDK/model
```

Mỗi tool bắt buộc nhận `as_of` (ngày gọi `YYYY-MM-DD`) do backend cung cấp.
Dữ liệu có hạn hiệu lực được kiểm tra theo ngày này. Sang ngày mới phải tra
lại fact trước khi tính trả góp hoặc gửi tóm tắt. Ngày gọi giúp phát hiện dữ
liệu hết hạn; muốn có số liệu mới vẫn cần cập nhật các file YAML.
Xác minh danh tính do module của nhóm khác phụ trách; package không chứa
ngày sinh hay tool xác minh.

## Các tool có sẵn

| Tool | Chức năng |
| --- | --- |
| `crm_lookup` | Tìm khách bằng customer ID, CIF hoặc định danh kênh |
| `get_rate` | Tra lãi suất chuẩn và biên độ theo phân khúc |
| `get_active_promotions` | Lọc ưu đãi theo sản phẩm, kênh, ngày giả lập |
| `get_card_offer` | Tra gói thẻ, khoảng hạn mức và phí |
| `calc_installment` | Tính trả góp đều, có thể đổi lãi sau giai đoạn ưu đãi |
| `check_suitability` | Kiểm tra thu nhập, tỷ lệ trả nợ và tỷ lệ vay/giá trị nhà |
| `get_disclosures` | Lấy câu lưu ý bắt buộc |
| `send_summary` | Ghi nhận gửi tóm tắt **giả lập** |
| `schedule_callback` | Ghi nhận lịch gọi lại **giả lập** |

## Tài liệu bàn giao

- [Hướng dẫn tích hợp và hợp đồng dữ liệu](docs/INTEGRATION.md): cách gọi, cấu trúc kết quả, tham số và xử lý lỗi.
- [Hướng dẫn dữ liệu giả lập](docs/DATA.md): 10 khách hàng, bảng lãi/ưu đãi, đơn vị và ca demo.
- [Demo đủ 9 tool](examples/demo.py): mẫu để người làm agent dùng ngay.
- [Bộ kiểm tra](tests/test_bank_tools.py): kiểm tra tiền, ngày hết hạn, nguồn fact và luồng tích hợp.

## Cấu trúc

```text
bank_tools/
  __init__.py          # API public: BankTools, get_tool_definitions
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
