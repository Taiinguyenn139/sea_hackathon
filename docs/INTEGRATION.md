# Tích hợp Bank Tools vào agent

## 1. Cài và khởi tạo

Chạy từ repo:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m examples.demo
```

Ở dự án Python khác, có thể chạy `python -m pip install -e /duong/dan/sea_hackathon`.
YAML được đóng gói cùng package; không phụ thuộc thư mục đang chạy.

```python
from bank_tools import BankTools, get_tool_definitions

# Một instance cho mỗi session; giữ lại qua các lượt trong session đó.
bank = BankTools()
definitions = get_tool_definitions()
```

`as_of` là tham số **bắt buộc ở từng tool**, dạng `YYYY-MM-DD`, không có ngày
mặc định trong instance và không có `set_as_of`. Backend lấy ngày nghiệp vụ
hiện tại theo múi giờ của ứng dụng rồi truyền vào; không để model tự chọn ngày.
Trong demo/benchmark, runner truyền ngày giả lập của kịch bản. Ví dụ:

```python
result = bank.get_active_promotions(
    product="home_loan", channel="zalo", as_of="2026-10-31",
)
```

Mỗi lời gọi giữ ngày riêng, không ảnh hưởng ngày của lời gọi khác. Tool từ chối
ngày thiếu/sai định dạng. Ngày chỉ giúp kiểm tra dữ liệu có hiệu lực, không tự
cập nhật giá trong YAML khi hết hạn. Phiên mới tạo instance mới và tra lại số
liệu ngân hàng. Xác minh danh tính do module bên ngoài phụ trách; package này
không chứa tool xác minh hay dữ liệu ngày sinh. Ứng dụng chatbot quản lý trạng
thái xác minh, thông tin khách nói và memory liên phiên.

## 2. Hợp đồng kết quả chung

Mọi tool trả `dict` có thể serialize thành JSON:

```json
{
  "tool_call_id": "tc_<mã từng lần gọi>",
  "tool": "get_active_promotions",
  "as_of": "2026-10-31",
  "data": {},
  "facts": [],
  "error": null
}
```

- `error: null`: tool thực thi thành công. Vẫn cần đọc trạng thái nghiệp vụ
  trong `data`, như `found`, `eligible`, `ok`.
- `error: "timeout"` hoặc `"error"`: lỗi giả lập từ hệ thống ngân hàng.
- `error: "invalid: ..."`: tham số sai, không tìm được dữ liệu hợp lệ hoặc
  bảng lãi/chính sách hết hạn. Không có fact trong phản hồi lỗi. Nếu chưa
  xác định được ngày hợp lệ (thiếu/sai ngày, tên tool hoặc đối số không hợp lệ),
  trường `as_of` trong kết quả lỗi là `null`, không dùng ngày của lượt trước.
- Không có ưu đãi là thành công với `data.promotions=[]` và `facts=[]`.
- Không tìm được khách là thành công với `data.found=false`.
- Không đủ điều kiện là thành công với `eligible=false` hoặc `ok=false`.
- Lỗi cấu hình file YAML bị thiếu/hỏng phát sinh khi khởi tạo `BankTools`;
  đội phát triển cần sửa cấu hình trước khi khởi động chatbot.

Ví dụ một fact lãi suất (ID và source_ref thực tế được sinh khi chạy):

```json
{
  "fact_id": "f_<12 ký tự hash>",
  "key": "promo_rate",
  "value": 6.9,
  "unit": "%/năm",
  "label": "HL-Q4-2026",
  "source_type": "bank_tool",
  "source_ref": "tc_<mã lần gọi>",
  "tool": "get_active_promotions",
  "observed_at": "2026-10-31",
  "valid_until": "2026-12-31",
  "confidence": 1.0,
  "status": "active",
  "metadata": {"product": "home_loan", "channel": "zalo"}
}
```

`fact_id` ổn định nếu cùng tool, key/label, tham số và ngày giả lập.
`tool_call_id` luôn mới để truy từng lần gọi. Tra cứu lại cùng fact trong một
instance cập nhật `source_ref` sang lần gọi mới; từng trace cũ vẫn giữ nguyên.
Fact dùng để tính trả góp/gửi tóm tắt phải được tra cùng ngày `as_of`; sang
ngày khác cần gọi lại tool nguồn, kể cả khi fact chưa đến `valid_until`.
Chốt cùng một phiên bản fixture khi so benchmark; sửa dữ liệu giữa các run
có thể làm cùng ID biểu diễn giá trị khác nhau.

`valid_until` là hạn hiệu lực dữ liệu/báo giá, không phải thời gian ưu đãi kéo
dài sau giải ngân. Ví dụ ưu đãi đăng ký hết tháng 12 vẫn có thể có kỳ ưu đãi 12 tháng.

Chỉ dùng các số trong `facts` để tạo placeholder `{fact:<fact_id>}` trong câu
trả lời. Số trong `data` như `diagnostics.dti`, `rounding_vnd`, các tham số nội bộ
không phải số đã được cấp fact để đọc trực tiếp cho khách.

Khi gắn fact vào memory, ứng dụng cần thêm `customer_id`, `session_id` và liên
kết trace tương ứng. Thư viện không tự lưu vào DB của harness.

## 3. Tham số và kết quả của 9 tool

Quy ước chung:

- Sản phẩm: `home_loan`, `personal_loan`, `credit_card`.
- Kênh: `hotline`, `zalo`, `web`.
- Phân khúc: `mass`, `priority`.
- Tiền: số nguyên VND. `2 tỷ` phải được agent chuẩn hóa thành `2000000000`.
- Kỳ hạn: số nguyên tháng. `20 năm` phải thành `240`.
- Lãi suất: phần trăm/năm. Truyền `6.9`, không truyền `0.069` hoặc `"6,9%"`.
- `as_of`: bắt buộc với cả 9 tool, định dạng `YYYY-MM-DD`.
- Lịch gọi lại: ISO 8601 có múi giờ.

| Tool | Tham số bắt buộc | Tham số tùy chọn | Dữ liệu/fact chính |
| --- | --- | --- | --- |
| `crm_lookup` | `as_of`, `identifier` | — | `found`; khi tìm thấy có `customer_id`, `cif`, `full_name`, `segment`; không có fact |
| `get_rate` | `as_of`, `product`, `amount`, `term`, `segment` | — | `rate_id`; facts `standard_rate`, `floating_margin` |
| `get_active_promotions` | `as_of`, `product`, `channel` | — | `promotions`; facts `promo_rate` hoặc `promo_fee_waiver`, kèm `promo_months` |
| `get_card_offer` | `as_of`, `income`, `segment` | — | `eligible`; facts `card_limit_min`, `card_limit_max`, `annual_fee`, `fee_waiver_spend` |
| `calc_installment` | `as_of`, `amount`, `term`, `rate`, `rate_fact_id` | `promo_months=0`, `after_rate`, `after_rate_fact_id` | facts `installment_standard` khi không ưu đãi, hoặc `installment_promo`; `installment_after` khi có giai đoạn sau; `total_payment` |
| `check_suitability` | `as_of`, `product`, `income`, `debt` | `amount`, `term`, `property_value` theo sản phẩm | `ok`, `reasons`, `diagnostics`; không có fact |
| `get_disclosures` | `as_of`, `product`, `topics` | — | `disclosures`: danh sách `{topic, text}`; không có fact |
| `send_summary` | `as_of`, `customer_id`, `channel`, `fact_ids` | — | `message_id`, `status=mock_sent`, `mock=true` |
| `schedule_callback` | `as_of`, `customer_id`, `time` | — | `task_id`, `time`, `status=mock_scheduled`, `mock=true` |

### Quy tắc riêng cần biết

**Tra khách:** `identifier` nhận customer ID, CIF hoặc external ID đầy đủ
như `demo:hoa:zalo`. Không tra gần đúng theo tên. `crm_lookup` không trả thu
nhập hoặc tiền trả nợ. Hồ sơ khách là snapshot demo tĩnh; `as_of` ghi ngày tra
cứu, không tạo lịch sử hồ sơ theo thời gian. Backend tích hợp module xác minh
của nhóm khác và chỉ hiển thị thông tin khi đã được phép.

**Lãi và ưu đãi:** `get_rate` chỉ dành cho hai sản phẩm vay. `floating_margin`
là biên độ trong dữ liệu, không cộng thêm vào `standard_rate`. Khi có nhiều
ưu đãi, kết quả trả tất cả, sắp theo `promo_id`; agent chọn một chương trình
phù hợp. Ghép `promo_rate` và `promo_months` có cùng `label`, không ghép giữa
hai chương trình. Phí được miễn trong promo thẻ có đơn vị `%`, không phải VND.

**Thẻ:** hạn mức tối đa minh họa = nhỏ hơn giữa trần gói và thu nhập nhân hệ
số. Thu nhập không đạt thì `eligible=false`, không phát fact. Ưu đãi miễn phí
được tra riêng bằng `get_active_promotions`; tool gói thẻ luôn trả phí cơ sở.

**Trả góp:** lấy `rate_fact_id` từ `get_rate` hoặc `get_active_promotions`
trong cùng instance. Tool từ chối ID tự đặt và lãi không khớp. Với lãi chuẩn,
fact phải đúng khoản tiền/kỳ hạn đang tính và `promo_months=0`. Với lãi ưu đãi,
`promo_months` phải đúng thời gian chương trình, giới hạn bởi kỳ hạn vay.
Nếu còn kỳ sau ưu đãi, bắt buộc truyền `after_rate` cùng `after_rate_fact_id`
loại `standard_rate` của đúng sản phẩm/số tiền/kỳ hạn. Tham số bổ sung này giúp
truy nguồn cả hai mức lãi, rõ hơn chữ ký tối thiểu trong artifact.

Tool tính khoản trả đều trên dư nợ giảm dần; sau ưu đãi, tính lại trên dư nợ
còn lại và số tháng còn lại. Lãi sau ưu đãi là giả định minh họa cố định, không
phải dự báo lãi tương lai. Từng số được làm tròn nghìn VND, nửa nghìn làm tròn
lên. Tổng lấy từ số chưa làm tròn nên có thể khác tổng các khoản hiển thị.
Không tính phí và không áp dụng cho dư nợ thẻ quay vòng.

**Phù hợp hồ sơ:** `debt` là **tổng tiền trả nợ hiện hữu mỗi tháng**, không phải
dư nợ gốc. Với khoản vay bắt buộc có `amount`, `term`; vay nhà thêm
`property_value`. Thẻ chỉ nhận `income`, `debt`. DTI gồm cả khoản trả dự kiến
của khoản vay mới ở mức `assessment_rate` trong fixture. LTV = khoản vay /
giá trị nhà. `ok=true` chỉ qua sàng lọc demo, chưa phải phê duyệt.

`reasons` có thể gồm `income_below_min`, `amount_out_of_range`,
`term_out_of_range`, `dti_too_high`, `ltv_too_high`. Thiếu dữ liệu cần thiết
thì trả `invalid`, thay vì kết luận khách không đủ điều kiện.

**Chính sách theo ngày:** luật suitability và disclosure đều có `valid_from`,
`valid_until`; ngoài khoảng này tool trả `invalid`, không dùng chính sách cũ.
Các file hiện chỉ có chính sách năm 2026; cần bổ sung trước khi demo năm khác.

**Disclosure:** các topic hợp lệ là `rate`, `installment`, `fee`, `promo`.
Agent/renderer chèn nguyên văn câu lưu ý tương ứng khi tư vấn. Tool không tự
chèn vào câu trả lời hay nội dung gửi đi.

**Gửi tóm tắt:** chỉ nhận fact từng được phát ra trong instance và còn hạn;
ưu đãi và kết quả trả góp từ ưu đãi phải đúng kênh gửi. Caller phải kiểm tra
fact thuộc đúng khách/phiên và chỉ gửi những fact đã tư vấn. Tool ghi snapshot
fact vào RAM, chưa dựng tin nhắn văn bản và chưa chèn disclosure để gửi thật.

**Hẹn gọi lại:** phải có múi giờ, ví dụ `2026-11-02T14:00:00+07:00`.
`as_of` chỉ có ngày nên tool yêu cầu ngày hẹn **sau** `as_of`; chưa hỗ trợ
lịch ngay trong ngày hoặc kiểm tra giờ làm việc. Hỏi khách làm rõ trước khi
chuyển lời hẹn tương đối thành thời gian cụ thể.

Hai tool hành động chưa có idempotency (chống tạo trùng khi gọi lại): mỗi lần
gọi thành công tạo ID mới. Agent cần tránh tự retry sau khi đã nhận thành công.

## 4. Luồng gọi mẫu

Ví dụ đầy đủ, chạy được: [examples/demo.py](../examples/demo.py).

Luồng vay mua nhà:

1. Gateway gọi `crm_lookup`; chỉ hiển thị lời mở trung tính trước xác minh.
2. Module xác minh bên ngoài xác nhận danh tính; chỉ tiếp tục khi backend cho phép.
3. Lấy nhu cầu, thu nhập, khoản trả nợ, giá trị nhà từ hội thoại/memory đã xác nhận.
4. `check_suitability`; thiếu slot thì hỏi, không phù hợp thì giải thích/chuyển người.
5. `get_rate` và `get_active_promotions`.
6. Nếu khách hỏi trả góp, gọi `calc_installment` với ID và giá trị fact đã nhận.
7. `get_disclosures` theo chủ đề.
8. Agent soạn placeholder → guard kiểm tra → renderer thay bằng giá trị fact.
9. Khách muốn nhận tóm tắt/hẹn lại thì gọi tool hành động tương ứng.

Ví dụ dispatcher nhận kết quả lựa chọn tool từ agent:

```python
import json
from bank_tools import BankTools, get_tool_definitions

bank = BankTools()
definitions = get_tool_definitions()

# Dữ liệu giả lập do model/framework trả về:
tool_name = "get_active_promotions"
tool_arguments = '{"product":"home_loan","channel":"zalo"}'

# Backend chèn/ghi đè ngày từ request context, không tin ngày model tự sinh.
call_date = "2026-10-31"  # Demo; ứng dụng thật lấy ngày nghiệp vụ hiện tại.
arguments = json.loads(tool_arguments)
arguments["as_of"] = call_date
result = bank.call(tool_name, arguments)
tool_result_text = json.dumps(result, ensure_ascii=False, allow_nan=False)
# Đưa tool_result_text vào message kết quả tool của framework đang dùng.
# Giữ đúng tool-call ID của MODEL khi gửi kết quả về model. tool_call_id
# trong result là ID TRACE nội bộ ngân hàng, không thay thế ID do model tạo.
```

`get_tool_definitions()` trả mỗi mô tả dạng `{name, description, parameters}`.
`parameters` là JSON Schema. Gắn định dạng này vào SDK/framework của đội qua
adapter phù hợp; thư viện không phụ thuộc một SDK hoặc model cụ thể. Không
truyền `inject_fault`, `get_tool_calls`, `get_mock_actions` cho model.
`bank.call` chỉ cho phép 9 tên đã đăng ký, không gọi tùy ý phương thức Python.

Tool chạy đồng bộ và dùng khóa theo instance. Với ứng dụng async, có thể dùng
`await asyncio.to_thread(bank.call, tool_name, tool_arguments)` để tránh chặn
event loop. Không tạo instance mới cho từng lần gọi vì sẽ mất cache fact mà
`calc_installment` và `send_summary` cần.

## 5. Xử lý lỗi và quan sát demo

```python
bank.inject_fault("get_rate", mode="timeout", count=1)
failed = bank.get_rate("home_loan", 2_000_000_000, 240, "mass", as_of="2026-10-31")
assert failed["error"] == "timeout"
assert failed["facts"] == []
# Lần kế tiếp chạy bình thường vì count đã dùng hết.

traces = bank.get_tool_calls()
actions = bank.get_mock_actions()  # {"messages": [...], "callbacks": [...]}

old_promo = bank.get_active_promotions("home_loan", "zalo", as_of="2026-09-30")
new_promo = bank.get_active_promotions("home_loan", "zalo", as_of="2026-10-31")
```

Nếu tool lỗi, hết hạn hoặc không có fact cần dùng: agent không tự điền số,
không lấy lại quote cũ từ lịch sử; hỏi thêm dữ liệu hoặc đề nghị kiểm tra lại/
chuyển chuyên viên. Nhiệm vụ guard, memory flush và handoff nằm ở harness.

Trace và hành động giả lập chỉ tồn tại trong RAM, mất khi kết thúc process.
Muốn hiển thị trên console hoặc lưu SQLite, ứng dụng đọc hai phương thức trên
và lưu theo schema của nhóm. Fixture chứa thu nhập/tiền trả nợ để dựng scenario,
không tự nạp các trường này vào prompt từ lookup.

## 6. Kiểm tra trước khi nối chatbot

```bash
python -m unittest discover -s tests -v
python -m examples.demo
```

Test bao gồm ngày bắt buộc/sai định dạng, ngày độc lập giữa các lời gọi,
chính sách hết hạn, định danh đa kênh, promo tại ngày biên, số tiền/kỳ hạn, hai giai
đoạn trả góp, lãi bằng không, fact không hợp lệ, DTI/LTV, lỗi có đếm, hành động
giả lập và demo đủ 9 tool. Đây là kiểm tra thư viện, chưa phải benchmark chất
lượng hội thoại, guardrail hoặc memory của chatbot.
