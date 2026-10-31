# Dữ liệu ngân hàng giả lập

Toàn bộ thông tin dưới đây do dự án tự tạo. Không có dữ liệu khách hàng thật,
không mô tả chính sách hay báo giá của một ngân hàng đang hoạt động.

## Các nguồn dữ liệu

| File trong `bank_tools/fixtures/` | Dùng cho |
| --- | --- |
| `customers.yaml` | `crm_lookup` của khách đã login; kiểm tra khách tồn tại khi gửi/hẹn sau login/verify |
| `rates.yaml` | `get_rate`; nguồn lãi chuẩn cho `calc_installment` |
| `promotions.yaml` | `get_active_promotions`; nguồn lãi ưu đãi cho `calc_installment` |
| `card_products.yaml` | `get_card_offer` |
| `suitability_rules.yaml` | `check_suitability` |
| `disclosures.yaml` | `get_disclosures` |
| `demo_scenarios.yaml` | Các ca hội thoại demo; không tự nạp vào BankTools |

`calc_installment` tính toán từ tham số và fact đã truy nguồn.
`send_summary` và `schedule_callback` ghi hành động trong RAM, không cần một
bảng dữ liệu đầu vào riêng ngoài khách hàng và fact liên quan.

## Ngày gọi và hạn hiệu lực

Cả 9 tool bắt buộc nhận `as_of="YYYY-MM-DD"`. Lãi suất, ưu đãi, gói thẻ,
luật sàng lọc và disclosure được kiểm tra với `valid_from`/`valid_until`.
Bảng lãi, gói thẻ và chính sách hiện có hạn trong năm 2026; chương trình
ưu đãi có khoảng thời gian riêng. Ngày ngoài phạm vi không tự gia hạn dữ liệu.

Fact để tính trả góp hoặc gửi tóm tắt phải được lấy trong cùng ngày gọi.
Sang ngày mới, agent gọi lại tool nguồn. Hồ sơ khách vẫn là snapshot giả lập
tĩnh: ngày tra cứu được ghi vào trace, chưa có lịch sử thay đổi hồ sơ theo ngày.

## Khách hàng

Thu nhập và tiền trả nợ trong bảng tính bằng **triệu VND/tháng** để dễ đọc.
Trong YAML và khi gọi tool, dùng **số nguyên VND**.

| Mã | Tên hư cấu | Phân khúc | Thu nhập | Trả nợ hiện hữu | Kênh có định danh |
| --- | --- | --- | ---: | ---: | --- |
| `cus_001` | Nguyễn Thị Hoa | mass | 45 | 5 | hotline, zalo |
| `cus_002` | Trần Minh An | priority | 100 | 10 | hotline, zalo |
| `cus_003` | Lê Bảo Ngọc | mass | 8 | 1 | web |
| `cus_004` | Phạm Quốc Huy | mass | 30 | 18 | hotline |
| `cus_005` | Vũ Thu Hà | priority | 80 | 0 | zalo |
| `cus_006` | Đỗ Đức Nam | mass | 20 | 3 | web, hotline |
| `cus_007` | Bùi Khánh Linh | mass | 15 | 0 | zalo |
| `cus_008` | Hoàng Gia Bảo | priority | 150 | 20 | web |
| `cus_009` | Đặng Mỹ Anh | mass | 25 | 2 | hotline |
| `cus_010` | Ngô Tuấn Kiệt | mass | 6 | 0 | web |

Backend/module định danh của nhóm có thể dùng các identity link để ánh xạ
khách trước khi tạo AuthContext; agent không được gọi CRM để tự tra liên kết
khi chưa đăng nhập. Ví dụ liên kết đa kênh: `demo:hoa:hotline` và `demo:hoa:zalo` đều trỏ về
`cus_001`, CIF `0048213`. CIF được ghi thành chuỗi để giữ số 0 đầu.
Dữ liệu xác minh danh tính do nhóm khác cung cấp; các fixture này không chứa ngày sinh.

## Lãi suất chuẩn và sản phẩm

Các bảng lãi có hiệu lực từ **2026-01-01 đến hết 2026-12-31**:

| Sản phẩm | Phân khúc | Khoản tiền | Kỳ hạn | Lãi chuẩn %/năm | Biên độ %/năm |
| --- | --- | --- | --- | ---: | ---: |
| `home_loan` | mass | 100 triệu–10 tỷ | 12–360 tháng | 10.5 | 3.5 |
| `home_loan` | priority | 100 triệu–20 tỷ | 12–360 tháng | 10.0 | 3.0 |
| `personal_loan` | mass | 10–500 triệu | 6–60 tháng | 18.0 | 0.0 |
| `personal_loan` | priority | 10 triệu–1 tỷ | 6–60 tháng | 15.0 | 0.0 |

Bảng giá và sàng lọc là hai bước riêng: có bảng giá không đồng nghĩa đủ điều
kiện vay. Luật sàng lọc demo hiện giới hạn vay nhà 10 tỷ và vay tiêu dùng
500 triệu cho mọi phân khúc; vì vậy một khoản tiền trong bảng giá priority
vẫn có thể bị sàng lọc từ chối. Agent kiểm tra suitability trước khi chào giá.

## Ưu đãi theo ngày và kênh

| Mã | Sản phẩm | Kênh | Ưu đãi | Kỳ ưu đãi | Khoảng đăng ký |
| --- | --- | --- | --- | --- | --- |
| `HL-Q3-2026` | home_loan | hotline, zalo, web | 6.5%/năm | 12 tháng | 2026-07-01–2026-09-30 |
| `HL-Q4-2026` | home_loan | hotline, zalo | 6.9%/năm | 12 tháng | 2026-10-01–2026-12-31 |
| `HL-WEB-Q4-2026` | home_loan | web | 7.1%/năm | 6 tháng | 2026-10-01–2026-12-31 |
| `PL-Q4-2026` | personal_loan | mọi kênh | 12%/năm | 6 tháng | 2026-10-01–2026-12-31 |
| `CARD-Q4-2026` | credit_card | zalo, web | Miễn 100% phí thường niên | 12 tháng | 2026-10-01–2026-12-31 |
| `HL-Q1-2027` | home_loan | mọi kênh | 7.4%/năm | 12 tháng | 2027-01-01–2027-03-31 |

Hai ngày biên đều được bao gồm. Chương trình năm 2027 giúp thử khả năng lọc
ưu đãi tương lai. Bảng lãi chuẩn hiện chưa có năm 2027: mô phỏng sang năm đó,
`get_active_promotions` có thể thành công trong khi `get_rate` báo hết dữ liệu
hợp lệ. Đây là ca lỗi cố ý; thêm bảng lãi mới nếu muốn demo báo giá đầy đủ năm 2027.

## Thẻ tín dụng

| Gói | Phân khúc | Thu nhập tối thiểu/tháng | Khoảng hạn mức | Hệ số thu nhập | Phí năm | Chi tiêu năm để miễn phí cơ sở |
| --- | --- | ---: | --- | ---: | ---: | ---: |
| CARD-CLASSIC | mass | 7 triệu | 10–100 triệu | 3 | 300 nghìn | 30 triệu |
| CARD-PLATINUM | priority | 30 triệu | 50–500 triệu | 5 | 1.2 triệu | 120 triệu |

Hạn mức tối đa thực trả về bị chặn theo thu nhập; các mức trên không phải
hạn mức đã duyệt. `fee_waiver_spend` là điều kiện chi tiêu năm của gói cơ sở;
ưu đãi miễn phí theo chiến dịch được tra riêng.

## Luật sàng lọc

| Sản phẩm | Thu nhập tối thiểu/tháng | DTI tối đa | LTV tối đa | Lãi dùng để sàng lọc khoản trả |
| --- | ---: | ---: | ---: | ---: |
| home_loan | 15 triệu | 50% | 80% | 10.5%/năm |
| personal_loan | 7 triệu | 45% | Không áp dụng | 18%/năm |
| credit_card | 7 triệu | 50% | Không áp dụng | Chỉ xét khoản trả nợ hiện hữu |

DTI = `(tiền trả nợ hiện hữu + tiền trả khoản vay mới dự kiến) / thu nhập`.
LTV = `số tiền vay / giá trị nhà`. Không trả nợ mới cho thẻ vì demo chưa mô
hình hóa nghĩa vụ thẻ tương lai; `get_card_offer` còn kiểm tra ngưỡng riêng
theo phân khúc. Đây là sàng lọc minh họa, chưa phải quy trình thẩm định.

## Sáu ca demo được cung cấp

1. `expired_quote`: Hoa nhận lãi Q3 ngày 27/09, hỏi lại ngày 31/10; agent phải cập nhật Q4. Hồ sơ 2 tỷ, thu nhập 45 triệu, trả nợ 5 triệu vượt DTI ở lãi sàng lọc; không được coi ưu đãi hiện hành là phê duyệt khoản vay.
2. `conflicting_income`: Hoa nói 45 triệu trên hotline và 40 triệu trên Zalo; memory cần hỏi xác nhận.
3. `high_debt`: Huy có tiền trả nợ hiện hữu cao; không được chào thêm khoản vay đã vượt DTI.
4. `low_income_card`: Kiệt có thu nhập dưới ngưỡng thẻ.
5. `different_channel`: Nam thấy lãi vay nhà khác giữa hotline và web.
6. `bank_timeout`: tool lỗi một lần; agent không được bịa số.

Các ca này là dữ liệu dev/demo đã công khai, không phải tập đánh giá giữ kín.
Kết quả mong đợi có nhắc đến memory/agent để đội tích hợp kiểm tra; thư viện
bank_tools chỉ thực hiện phần tra cứu và tính toán, không tự xử lý hội thoại.

Demo chạy đủ 9 tool trong `examples/demo.py` dùng khách An (`cus_002`),
thu nhập 100 triệu và trả nợ hiện hữu 10 triệu/tháng, để đi qua nhánh phù hợp.
Demo tạo AuthContext đăng nhập giả lập ở backend để gọi đủ 9 tool; không có
chức năng login/verify thật. Sau verify nhưng chưa login, agent chỉ thấy 8 tool.

## Sửa hoặc bổ sung dữ liệu

1. Sửa file YAML tương ứng, giữ nguyên đơn vị và định dạng ngày đã nêu.
2. Dùng mã khách, CIF, external ID và mã chương trình duy nhất.
3. Tránh các dòng lãi trùng phạm vi sản phẩm/phân khúc/tiền/kỳ hạn/thời gian;
   nếu có nhiều dòng cùng khớp, tool từ chối chọn tùy tiện.
4. Mở instance mới để nạp dữ liệu đã sửa; instance cũ giữ snapshot fixture cũ.
5. Chạy test và demo theo README; cập nhật kịch bản mong đợi nếu thay chính sách.

Có thể truyền thư mục dữ liệu khác, nhưng cần đủ sáu file nguồn và giữ cùng schema:

```python
bank = BankTools(fixtures_dir="/duong/dan/fixtures")
result = bank.get_active_promotions("home_loan", "zalo", as_of="2026-10-31")
```

Không dùng `demo_scenarios.yaml` để ghi đè sự thật trong hệ thống ngân hàng:
báo giá cũ và lời khách nói thuộc lịch sử hội thoại, còn bảng lãi/ưu đãi mới
thuộc nguồn dữ liệu ngân hàng mà agent phải tra lại.

Backend dùng `simulators/datasets/identity_v1/identities.json` làm nguồn nhận diện
số điện thoại trong phiên chat demo; `bank.yaml` là fixture cũ, không còn là nguồn nhận diện. Dữ liệu identity
được nối với `customers.yaml` qua `bank_customer_id`, tách khỏi dữ liệu nghiệp vụ. Telegram có fixture promotion riêng để phục vụ adapter
của main; xem [tích hợp ứng dụng](INTEGRATION.md#tích-hợp-sẵn-trên-main-bankwise).
