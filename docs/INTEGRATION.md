# Tích hợp Bank Tools vào agent

## 1. Cài và khởi tạo

Chạy từ repo với môi trường chung của ứng dụng:

```bash
uv sync --frozen
uv run python -m examples.demo
```

Ví dụ dưới đây dùng package Python thuần trong repo. Giữ nguyên thư mục
`bank_tools/fixtures` khi tái sử dụng ở dự án khác.

```python
from bank_tools import AuthContext, BankTools, get_tool_definitions

# Một instance cho mỗi session; giữ lại qua các lượt trong session đó.
bank = BankTools()
context = None  # Backend lấy AuthContext của request; None nếu khách ẩn danh.
definitions = get_tool_definitions(context=context)
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
liệu ngân hàng. Xác minh danh tính do module bên ngoài phụ trách; module nghiệp vụ này
không cung cấp tool xác minh; main dùng dữ liệu identity riêng trong `simulators/datasets/identity_v1/identities.json`. Ứng dụng chatbot quản lý trạng
thái xác minh, thông tin khách nói và memory liên phiên.

## Phân quyền và tích hợp module xác thực

| Trạng thái backend | Tool được cung cấp cho agent |
| --- | --- |
| Chưa đăng nhập/chưa xác minh | 6 tool công khai |
| Đã xác minh danh tính, chưa đăng nhập | 6 tool công khai + `send_summary`, `schedule_callback` |
| Đã đăng nhập | Đủ 9 tool, bao gồm `crm_lookup` |

6 tool công khai gồm `get_rate`, `get_active_promotions`, `get_card_offer`,
`calc_installment`, `check_suitability`, `get_disclosures`. Các công cụ này
chỉ dùng bảng giá công khai và tham số khách tự cung cấp. Một phân khúc như
`priority` ở phép tính thử không chứng minh khách thực sự thuộc phân khúc đó.
Không tự đọc hồ sơ ngân hàng để điền thông tin khi khách chưa đăng nhập.

Backend lấy `customer_id` và trạng thái từ module login/xác minh bên ngoài,
rồi tạo `AuthContext` cho **request hiện tại**. Ví dụ các trạng thái demo:

```python
guest = None
verified = AuthContext(customer_id="cus_001", identity_verified=True)
logged_in = AuthContext(customer_id="cus_001", logged_in=True)

assert len(get_tool_definitions()) == 6
assert len(get_tool_definitions(context=verified)) == 8
assert len(get_tool_definitions(context=logged_in)) == 9

# Sau verify thành công: chưa cần đăng nhập để hẹn gọi lại.
callback = bank.call("schedule_callback", {
    "time": "2026-11-02T14:00:00+07:00", "as_of": "2026-10-31",
}, context=verified)
assert callback["error"] is None

# Chỉ verify thì chưa được đọc CRM.
denied = bank.call("crm_lookup", {"as_of": "2026-10-31"}, context=verified)
assert denied["error"].startswith("authentication_required:")

profile = bank.call("crm_lookup", {"as_of": "2026-10-31"}, context=logged_in)
assert profile["data"]["customer_id"] == "cus_001"
```

`AuthContext` là đối tượng Python bất biến, không phải token hoặc bằng chứng
xác thực tự thân. Các ví dụ trên mô phỏng kết quả của module bên ngoài; backend
thật chỉ tạo context sau khi đã xác thực kết quả login/verify thuộc đúng phiên,
khách hàng và còn hiệu lực. Không dùng JSON từ frontend/model để dựng context,
không dựa vào lời khách nói “tôi đã xác minh”. Module bên ngoài chịu trách nhiệm
kiểm tra token, giới hạn phiên, hết hạn xác minh và thu hồi quyền.

Mỗi lần gọi, backend truyền `context=` riêng. `BankTools` không nhớ quyền đăng
nhập của lần trước. Khi logout/xác minh hết hạn, truyền `None` và cập nhật
`get_tool_definitions(context=None)` cho model. Không đưa AuthContext, token
hoặc đối tượng session vào prompt; trace tool cũng không ghi AuthContext.

Schema cho model không có `customer_id`, `context`, `logged_in`,
`identity_verified`. Với tool riêng tư, dispatcher tự lấy `customer_id` từ
context. Nếu một lời gọi cố chỉ định customer khác, runtime trả `forbidden`.
Các cờ xác thực trong JSON tham số tool bị từ chối, không được diễn giải thành
quyền. Việc ẩn tool chỉ giúp agent chọn đúng; kiểm tra quyền khi thực thi mới
là bước chặn truy cập, và áp dụng cả khi gọi hàm trực tiếp:

```python
profile = bank.crm_lookup("cus_001", as_of="2026-10-31", context=logged_in)
blocked = bank.crm_lookup("cus_002", as_of="2026-10-31", context=logged_in)
assert blocked["error"].startswith("forbidden:")
```

Giữ một `BankTools` riêng cho mỗi phiên hội thoại của một khách. Không chia sẻ
fact cache, trace hoặc mock actions giữa các khách. `get_tool_calls()` và
`get_mock_actions()` là API nội bộ backend; chỉ cung cấp dữ liệu của phiên
hiện tại cho console được cấp quyền, không đăng ký hai phương thức này cho model.

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
- `error: "authentication_required: ..."`: chưa đăng nhập hoặc chưa qua xác minh theo quyền tool.
- `error: "forbidden: ..."`: `customer_id` không khớp khách trong context, hoặc context không tin cậy khi gọi trực tiếp.
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
- Kênh: `hotline`, `zalo`, `web`, `telegram` (mở rộng của main).
- Phân khúc: `mass`, `priority`.
- Tiền: số nguyên VND. `2 tỷ` phải được agent chuẩn hóa thành `2000000000`.
- Kỳ hạn: số nguyên tháng. `20 năm` phải thành `240`.
- Lãi suất: phần trăm/năm. Truyền `6.9`, không truyền `0.069` hoặc `"6,9%"`.
- `as_of`: bắt buộc với cả 9 tool, định dạng `YYYY-MM-DD`.
- Lịch gọi lại: ISO 8601 có múi giờ.

Bảng dưới là tham số model gửi cho dispatcher. Với ba tool riêng tư,
backend truyền thêm `context=`; dispatcher chèn `customer_id`. Nếu gọi hàm
Python trực tiếp, truyền cả `customer_id` và `context=`.

| Tool | Tham số bắt buộc | Tham số tùy chọn | Dữ liệu/fact chính |
| --- | --- | --- | --- |
| `crm_lookup` | `as_of` | — | `found`; khi tìm thấy có `customer_id`, `cif`, `full_name`, `segment`; không có fact |
| `get_rate` | `as_of`, `product`, `amount`, `term`, `segment` | — | `rate_id`; facts `standard_rate`, `floating_margin` |
| `get_active_promotions` | `as_of`, `product`, `channel` | — | `promotions`; facts `promo_rate` hoặc `promo_fee_waiver`, kèm `promo_months` |
| `get_card_offer` | `as_of`, `income`, `segment` | — | `eligible`; facts `card_limit_min`, `card_limit_max`, `annual_fee`, `fee_waiver_spend` |
| `calc_installment` | `as_of`, `amount`, `term`, `rate`, `rate_fact_id` | `promo_months=0`, `after_rate`, `after_rate_fact_id` | facts `installment_standard` khi không ưu đãi, hoặc `installment_promo`; `installment_after` khi có giai đoạn sau; `total_payment` |
| `check_suitability` | `as_of`, `product`, `income`, `debt` | `amount`, `term`, `property_value` theo sản phẩm | `ok`, `reasons`, `diagnostics`; không có fact |
| `get_disclosures` | `as_of`, `product`, `topics` | — | `disclosures`: danh sách `{topic, text}`; không có fact |
| `send_summary` | `as_of`, `channel`, `fact_ids` | — | `message_id`, `status=mock_sent`, `mock=true` |
| `schedule_callback` | `as_of`, `time` | — | `task_id`, `time`, `status=mock_scheduled`, `mock=true` |

### Quy tắc riêng cần biết

**Tra khách:** chỉ sau đăng nhập, chỉ đọc hồ sơ có `customer_id` khớp phiên.
Agent không còn tra tùy ý bằng tên, CIF hay external ID. Các identity link
trong fixture chỉ để nhóm tích hợp xây mapping định danh ở backend/module
xác minh. `crm_lookup` không trả thu nhập hoặc tiền trả nợ. Hồ sơ là snapshot
demo tĩnh; `as_of` ghi ngày tra cứu, không tạo lịch sử hồ sơ theo thời gian.

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

**Gửi tóm tắt:** yêu cầu đăng nhập hoặc đã xác minh danh tính của chính
khách trong phiên; chỉ nhận fact từng được phát ra trong instance và còn hạn;
ưu đãi và kết quả trả góp từ ưu đãi phải đúng kênh gửi. Caller phải kiểm tra
fact thuộc đúng khách/phiên và chỉ gửi những fact đã tư vấn. Tool ghi snapshot
fact vào RAM, chưa dựng tin nhắn văn bản và chưa chèn disclosure để gửi thật.

**Hẹn gọi lại:** yêu cầu đăng nhập hoặc đã xác minh danh tính của chính
khách trong phiên; phải có múi giờ, ví dụ `2026-11-02T14:00:00+07:00`.
`as_of` chỉ có ngày nên tool yêu cầu ngày hẹn **sau** `as_of`; chưa hỗ trợ
lịch ngay trong ngày hoặc kiểm tra giờ làm việc. Hỏi khách làm rõ trước khi
chuyển lời hẹn tương đối thành thời gian cụ thể.

Hai tool hành động chưa có idempotency (chống tạo trùng khi gọi lại): mỗi lần
gọi thành công tạo ID mới. Agent cần tránh tự retry sau khi đã nhận thành công.

## 4. Luồng gọi mẫu

Ví dụ đầy đủ, chạy được: [examples/demo.py](../examples/demo.py).

Luồng vay mua nhà:

1. Khách ẩn danh được tư vấn/tính thử bằng 6 tool công khai từ thông tin tự cung cấp.
2. Khi đăng nhập thành công, backend truyền context và có thể gọi `crm_lookup` cho chính khách đó.
3. Chỉ qua verify_identity của module ngoài thì được gửi tóm tắt/hẹn lại, chưa được đọc CRM.
4. Lấy nhu cầu, thu nhập, khoản trả nợ, giá trị nhà từ hội thoại/memory đã được phép dùng.
5. `check_suitability`; thiếu slot thì hỏi, không phù hợp thì giải thích/chuyển người.
6. `get_rate`, `get_active_promotions`; nếu cần, gọi `calc_installment` bằng fact đã lấy.
7. `get_disclosures`; agent soạn placeholder → guard kiểm tra → renderer điền giá trị.
8. Khách muốn nhận tóm tắt/hẹn lại: module ngoài login/verify nếu cần, cập nhật context và danh sách tool trước khi gọi.

Ví dụ dispatcher nhận kết quả lựa chọn tool từ agent:

```python
import json
from bank_tools import AuthContext, BankTools, get_tool_definitions

bank = BankTools()
context = None  # Backend lấy AuthContext của request; None nếu khách ẩn danh.
definitions = get_tool_definitions(context=context)

# Dữ liệu giả lập do model/framework trả về:
tool_name = "get_active_promotions"
tool_arguments = '{"product":"home_loan","channel":"zalo"}'

# Backend chèn/ghi đè ngày từ request context, không tin ngày model tự sinh.
call_date = "2026-10-31"  # Demo; ứng dụng thật lấy ngày nghiệp vụ hiện tại.
arguments = json.loads(tool_arguments)
arguments["as_of"] = call_date
result = bank.call(tool_name, arguments, context=context)
tool_result_text = json.dumps(result, ensure_ascii=False, allow_nan=False)
# Đưa tool_result_text vào message kết quả tool của framework đang dùng.
# Giữ đúng tool-call ID của MODEL khi gửi kết quả về model. tool_call_id
# trong result là ID TRACE nội bộ ngân hàng, không thay thế ID do model tạo.
```

`get_tool_definitions(context=...)` lọc theo quyền và trả mỗi mô tả dạng `{name, description, parameters}`.
`parameters` là JSON Schema. Gắn định dạng này vào SDK/framework của đội qua
adapter phù hợp; thư viện không phụ thuộc một SDK hoặc model cụ thể. Không
truyền `inject_fault`, `get_tool_calls`, `get_mock_actions` cho model.
`bank.call` chỉ cho phép 9 tên đã đăng ký, không gọi tùy ý phương thức Python.

Tool chạy đồng bộ và dùng khóa theo instance. Với ứng dụng async, có thể dùng
`await asyncio.to_thread(bank.call, tool_name, arguments, context=context)` để tránh chặn
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

Khi gặp `authentication_required`, agent hướng khách qua bước login hoặc
verify bên ngoài phù hợp với tool. Khi gặp `forbidden`, không truy cập khách
khác và không retry bằng cách đổi mã khách. Mọi lỗi quyền đều có `data={}`,
`facts=[]` và không tạo mock action.

## 6. Kiểm tra trước khi nối chatbot

```bash
python -m unittest discover -s tests -v
python -m examples.demo
```

Test bao gồm ngày bắt buộc/sai định dạng, ngày độc lập giữa các lời gọi,
chính sách hết hạn, hồ sơ đúng khách sau đăng nhập, promo tại ngày biên, số tiền/kỳ hạn, hai giai
đoạn trả góp, lãi bằng không, fact không hợp lệ, DTI/LTV, lỗi có đếm, hành động
giả lập và demo đủ 9 tool. Đây là kiểm tra thư viện, chưa phải benchmark chất
lượng hội thoại, guardrail hoặc memory của chatbot. `tests/test_authorization.py`
kiểm tra riêng ma trận 6/8/9 tool, truy cập khác khách, giả cờ xác thực, gọi
hàm trực tiếp, logout và tác động của lời gọi bị từ chối.

## Tích hợp sẵn trên main: Bankwise

Agent ở `harness/agent/engine.py` dùng adapter SQLite trong
`bank_tools/service.py`; package thuần vẫn được import bằng `from bank_tools import BankTools`.
Adapter lưu kết quả vào tool_calls/facts để console và benchmark truy vết được nguồn.
Backend truyền `as_of` từ ngày nghiệp vụ `/admin/clock` vào **mọi lần gọi**, kể cả xác minh.
Ngày demo mặc định là 2026-10-31. Bảng giá hết hiệu lực sẽ báo lỗi để yêu cầu cập nhật dữ liệu.

- `POST /sessions/{sid}/verify` dùng 10 hồ sơ trong
  `simulators/datasets/identity_v1/identities.json` qua `harness/identity.py`;
  có giới hạn thử sai, che ngày sinh trong audit log và xác minh riêng từng phiên.
- `GET /sessions/{sid}/tools` dùng Bearer session_token trả 6 hoặc 8 schema theo trạng thái xác minh.
  Không nhận cờ đăng nhập/xác minh hay customer_id từ frontend để cấp quyền tool.
- `user_id` bên ngoài là số điện thoại dạng chuỗi; registry nối trực tiếp với
  `bank_customer_id` (`cus_001`…`cus_010`) và đối chiếu CIF từ fixture CRM. Hai alias
  `hoa`/`minh` giữ khóa memory cũ; 8 người còn lại dùng phone làm khóa runtime.
  Xem [bộ identity đa phiên](IDENTITY_DATASET.md) để tạo phiên và nạp dữ liệu.
- Main hiện chỉ có đăng nhập **chuyên viên**, chưa có đăng nhập khách hàng. Vì vậy CRM
  luôn bị khóa trong chat hiện tại. Khi bổ sung module login khách hàng, backend phải
  kiểm tra phiên đó rồi tạo `AuthContext(logged_in=True)` tại ranh giới tin cậy.
- Khách chưa xác minh được hỏi dữ liệu công khai và tính thử bằng thông tin tự cung cấp;
  không đọc memory liên phiên hay ghi thông tin vào hồ sơ khách. Facts công khai của
  khách này được lưu riêng theo guest session.
- Agent dùng gói `mass` để tính minh họa công khai, không suy đoán phân khúc từ hồ sơ demo.
  Kiểm tra khoản vay cần số tiền, số tháng, thu nhập, số tiền trả nợ hiện hữu và giá nhà
  (vay nhà). Không có nợ cần được khách xác nhận rõ; không tự điền số không.
- Telegram là kênh riêng, có các dòng promotion riêng trong YAML; không ánh xạ sang zalo/hotline.
  Web Q4/2026 là 7,1% trong 6 tháng; Telegram Q4/2026 là 6,9% trong 12 tháng.
- `send_summary` và `schedule_callback` là hành động **mô phỏng**. Adapter lưu bền vào
  outbox/callbacks và trả trạng thái mock; không tự gửi qua nhà cung cấp. Lịch hẹn cần
  ngày tương lai và giờ cụ thể. Cú pháp “ngày mai lúc 14:30” được hiểu ở múi giờ Việt Nam.
  Gửi lại cùng client_message_id trả lại kết quả cũ để tránh tạo hành động trùng.
- Facts từ tool dùng số JSON; SQLite memory dùng chuỗi số. Evaluator đối chiếu giá trị
  số đồng thời vẫn kiểm tra ID, đơn vị, ngày, kênh và receipt nguồn.

Kiểm tra tích hợp: `uv run pytest -q`; benchmark có memory:

```bash
uv run python scripts/verify-dev.py --repetitions 1 \
  --memory-oracles eval/oracle_fixtures/memory --output data/bankwise-eval
```
