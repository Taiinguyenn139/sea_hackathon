# Bộ dữ liệu identity và hội thoại đa phiên

Bộ `identity_v1` gồm **10 người dùng giả lập, 40 phiên chat và 120 lời khách**.
Mỗi người có 4 phiên trên web, Telegram, Zalo và hotline; mỗi phiên có 3 lời khách.
Bộ đáp án riêng có 120 câu trả lời tham khảo và 40 checkpoint memory — trạng thái
thông tin cần nhớ sau từng phiên. Đây là dữ liệu dev để xây và kiểm tra agent,
chưa phải kết quả chạy chatbot thực tế hay benchmark độc lập.

## Các file cần dùng

| File/thư mục | Nội dung | Bên được đọc |
| --- | --- | --- |
| `simulators/datasets/identity_v1/manifest.json` | Danh mục file, số lượng, múi giờ và giới hạn tích hợp | Loader, simulator, evaluator |
| `simulators/datasets/identity_v1/identities.json` | Hồ sơ giả và liên kết tài khoản theo kênh | Backend xác thực; không đưa toàn bộ vào prompt |
| `simulators/datasets/identity_v1/offers.json` | Báo giá Bankwise và các ngân hàng hư cấu, có nguồn và hạn hiệu lực | Backend/context so sánh theo ngày |
| `simulators/datasets/identity_v1/journeys/0900000001.json` … `0900000010.json` | Lời khách, phiên, kênh, thời gian và trạng thái xác minh của từng phiên | Simulator phát lời khách |
| `eval/oracle_fixtures/identity_v1/0900000001.json` … `0900000010.json` | Câu trả lời tham khảo, điều kiện chấm và memory mong đợi | Chỉ evaluator |
| `simulators/identity_schemas.py` | Schema Pydantic kiểm tra cấu trúc bản ghi | Loader và người tạo dữ liệu |
| `simulators/identity_dataset.py` | API nạp dữ liệu và CLI kiểm tra | Người tích hợp |
| `harness/identity.py` | Registry identity cho backend, đọc file identity đầu vào | Backend xác minh và ánh xạ khách hàng |

Đường dẫn ở bảng tính từ thư mục gốc repo. Không đặt bộ này trong `data/` vì thư
mục đó dành cho dữ liệu phát sinh khi chạy ứng dụng và bị Git bỏ qua.

## Nhận diện một người qua nhiều phiên

`user_id` và `phone` cùng là chuỗi số điện thoại, ví dụ `"0900000001"`. Lưu bằng
chuỗi thay vì số nguyên để giữ ký tự `0` đầu. Đây chỉ là khóa nối dữ liệu giả;
không gọi, nhắn tin hoặc đăng ký tài khoản thật bằng những giá trị này.

Một người có nhiều `session_id`; một phiên có nhiều `message_id`. Mỗi lời khách
cũng mang `user_id`, `session_id`, `channel`, `at` để truy lại ai đã nói, trong
phiên nào và lúc nào. Thời gian dùng múi giờ Việt Nam `+07:00`; `as_of` là ngày
nghiệp vụ dạng `YYYY-MM-DD` dùng để kiểm tra hiệu lực báo giá.

`channel_links` nối từng kênh với một `external_id` có tiền tố `synthetic:`.
Trong ứng dụng thật, backend phải tự tra liên kết từ tài khoản kênh đã được xác
thực. Không dùng số điện thoại do khách gõ trong tin nhắn để đọc lịch sử riêng
của số đó.

`identity_status` và `verified_at` thuộc **từng phiên**. Một phiên đã xác minh
không làm phiên ở kênh khác tự được xác minh. Các trường này trong JSON chỉ mô
tả bối cảnh giả lập, không phải bằng chứng đăng nhập hoặc kết quả gọi dịch vụ
xác thực thật. `date_of_birth` phục vụ fixture identity; không chép ngày sinh
vào prompt, memory hay lịch sử tool.

Tất cả phiên trong bộ này có `logged_in: false`. Vì vậy:

- 6 tool công khai có thể dùng trước xác minh.
- `send_summary` và `schedule_callback` chỉ dùng sau khi backend xác nhận phiên
  đã qua `verify_identity`; vẫn cần đủ dữ liệu đầu vào của từng tool.
- `crm_lookup` luôn bị khóa trong bộ này vì xác minh danh tính không thay thế
  đăng nhập khách hàng. Đăng nhập console chuyên viên cũng không cấp quyền CRM
  cho khách.

## Các tình huống cần kiểm tra

| Nhóm | Diễn biến | Agent cần làm |
| --- | --- | --- |
| Hỏi lại người nhà (`family_decision`) | Khách cần bàn với vợ/chồng/người thân rồi quay lại báo quyết định hoặc sửa nhu cầu | Nhớ nhu cầu và vấn đề còn chờ, phân biệt dự định với quyết định đã xác nhận; không tự coi “để hỏi lại” là đồng ý vay |
| So sánh ngân hàng (`bank_comparison`) | Khách nêu báo giá khác, hỏi lãi ưu đãi và chi phí sau ưu đãi | So cùng sản phẩm, phân khúc, thời điểm, số tháng ưu đãi, lãi sau ưu đãi và phí; giữ nguồn của mỗi con số |
| Đổi kênh (`cross_channel`) | Khách tiếp tục trên Telegram/Zalo/hotline hoặc quay lại sau khi báo giá hết hạn | Kiểm tra danh tính phiên mới trước khi đọc memory riêng; giữ nguồn lời nói, xử lý thông tin sửa đổi và lấy báo giá đúng ngày/kênh |

Không chỉ so hai tỷ lệ lãi đầu kỳ để kết luận ngân hàng nào rẻ hơn. Báo giá phải
có cùng bối cảnh; các điều kiện chưa biết, như phí trả nợ trước hạn, phải ghi
“chưa rõ”, không đổi thành `0`. Lời khách kể về ngân hàng khác là thông tin
chưa được kiểm chứng. Chỉ coi một báo giá là nguồn fixture sau khi đối chiếu
`offer_id`, ngày hiệu lực và kênh; không tự nâng lời kể thành dữ liệu chính thức.

`offers.json` dùng ngân hàng hư cấu, đơn vị lãi `percent_per_year` (ví dụ `7.1`
nghĩa là 7,1%/năm), tiền `VND` và phương pháp trả góp `annuity`. Báo giá có
`source_type: synthetic_fixture`, `source_ref`, `as_of`, `valid_from`,
`valid_until`. Snapshot Bankwise khớp fixture nghiệp vụ của repo; dữ liệu đối
thủ là nguồn so sánh riêng, không phải kết quả của bank tool hiện tại.

Khi replay, báo giá cũ được giữ làm lịch sử nhưng hết hạn thì không dùng làm
báo giá hiện hành. Truyền ngày phiên vào bộ lọc offer và từng tool khi mô phỏng;
khi chạy thật, backend truyền ngày hiện tại, không lấy ngày do model tự đoán.

## Nạp và kiểm tra dữ liệu

Chạy từ thư mục gốc repo, sau khi cài dependencies theo README:

```sh
# Chỉ kiểm tra input; không đọc đáp án.
.venv/bin/python -m simulators.identity_dataset

# Kiểm tra cả câu trả lời tham khảo và checkpoint memory.
.venv/bin/python -m simulators.identity_dataset --check-expectations

# Xem lời khách của một người, theo các phiên của họ.
.venv/bin/python -m simulators.identity_dataset --user-id 0900000001

# Chỉ để người kiểm tra xem bản hội thoại tham khảo có ghép đáp án.
.venv/bin/python -m simulators.identity_dataset \
  --check-expectations --user-id 0900000001 --transcript
```

`--transcript` cần cả `--check-expectations` và `--user-id`. Bản ghép này là
**reference transcript**, không phải log bot đã trả lời. Dùng `--dataset-dir`
và `--expectations-dir` nếu muốn kiểm tra một bản sao ở đường dẫn khác.

API cho bên phát lời khách:

```python
from simulators.identity_dataset import load_dataset

dataset = load_dataset()  # Mặc định chỉ nạp input, không nạp đáp án.
user_id = "0900000001"
identity = dataset.identities[user_id]

for session in dataset.sessions_for(user_id):
    print(session.session_id, session.channel, session.identity_status)
    for message in session.messages:
        print(message.message_id, message.at, message.text)

print(dataset.summary())
```

`dataset.journeys` cũng được tra bằng `user_id`; `dataset.offers` được tra bằng
`offer_id`. `messages_for(user_id)` trả các lời khách của người đó.
`active_offers(product, channel, as_of, segment="mass")` lọc báo giá đúng sản
phẩm, kênh, ngày hiệu lực và phân khúc; không tự cộng báo giá đối thủ vào kết
quả tool của Bankwise.

API chỉ dành cho evaluator:

```python
from simulators.identity_dataset import (
    load_dataset,
    load_expectations,
    reference_transcript,
)

dataset = load_dataset()
expectations = load_expectations(dataset)
transcript = reference_transcript(dataset, expectations, "0900000001")
```

Không truyền `expectations`, `reference_text`, `checks` hoặc memory đáp án cho
agent. Nếu bot được đọc đáp án trước khi trả lời thì kết quả đánh giá không còn
đo được khả năng ghi nhớ và suy luận của bot.

## Backend `verify_identity` dùng bộ identity

Backend đọc đủ 10 hồ sơ từ `identities.json` qua registry `harness/identity.py`.
Registry này chỉ đọc dữ liệu identity đầu vào, không đọc journey, câu trả lời
tham khảo hoặc checkpoint đáp án. Việc có hồ sơ demo không có nghĩa là người
gọi đã được xác minh.

`POST /sessions` nhận số điện thoại ở trường `demo_profile`, gồm dạng `0...`
hoặc `+84...`; response có `user_id` đã chuẩn hóa về chuỗi 10 chữ số. Ví dụ
`"0900000001"` và `"+84900000001"` cùng chỉ một hồ sơ. Đây là chuẩn hóa khóa,
không phải kiểm tra ai sở hữu số điện thoại. Danh sách `/demo/profiles` dùng
phone làm ID để chọn cả 10 hồ sơ demo.

Hai alias `hoa`/`minh` vẫn được chấp nhận để dữ liệu memory cũ tiếp tục dùng
được. Bên trong DB, hai người đầu giữ khóa `customer_id` cũ tương ứng
`hoa`/`minh`; 8 người còn lại dùng phone làm khóa runtime. Bên ngoài, `user_id`
của cả 10 người luôn là phone. Trường `runtime_profile` chỉ phục vụ tương thích
dữ liệu, không cấp quyền và không cần đổi hay migrate memory cũ.

Tạo phiên chưa mở quyền memory riêng. Khi gọi
`POST /sessions/{session_id}/verify`, backend so ngày sinh với hồ sơ đã gắn
vào phiên do server tạo; không cho yêu cầu xác minh tự đổi sang một khách khác.
Kết quả chỉ xác minh phiên đó. Một phiên mới, kể cả cùng người hoặc cùng kênh,
phải qua bước này lại trước khi dùng lịch sử riêng hoặc hai tool sau xác minh.

Luồng demo giới hạn 5 lần thử trong 15 phút theo khách hàng và nguồn gọi; ngày
sinh được che khỏi audit tool, không ghi vào prompt hoặc memory. Đây là so
khớp fixture ngày sinh cho demo, chưa có OTP/SMS thật hay kiểm chứng quyền sở
hữu số điện thoại. `logged_in` vẫn tách riêng, nên CRM tiếp tục khóa.

Các đường đọc Brief theo khách hàng và tạo liên kết kênh của admin cũng chuẩn
hóa phone sang khóa runtime; chúng vẫn yêu cầu quyền admin hiện có. Liên kết
kênh chỉ chọn hồ sơ cho phiên, không thay thế bước xác minh phiên đó.

Ví dụ gọi API local bằng `httpx` (dependency đã có trong repo):

```python
import httpx

with httpx.Client(base_url="http://127.0.0.1:8000") as client:
    created = client.post("/sessions", json={"demo_profile": "0900000001"})
    created.raise_for_status()
    session = created.json()
    headers = {"Authorization": "Bearer " + session["session_token"]}
    verified = client.post(
        f"/sessions/{session['session_id']}/verify",
        headers=headers,
        json={"dob": "1988-05-12"},  # Ngày sinh giả của identity Hoa trong dataset.
    )
    verified.raise_for_status()
    print(verified.json()["identity_status"])
```

Token chỉ dùng cho phiên vừa tạo; không ghi token hoặc ngày sinh vào prompt/log.
Khi sửa `identities.json`, kiểm tra dataset rồi khởi động lại backend để registry
nạp dữ liệu mới. Hai bộ kiểm tra liên quan chạy bằng:

```sh
.venv/bin/pytest tests_eval/test_identity_dataset.py tests/test_identity_verification.py -q
```

## Tích hợp vào chatbot sau này

1. Dùng registry identity của backend và endpoint xác minh phiên. Thiết lập
   liên kết tài khoản kênh bằng quyền admin trong môi trường thử nghiệm; không
   lấy cờ `verified` trong file khách gửi để cấp quyền.
2. Adapter ánh xạ người đã được xác thực sang `user_id`, tạo phiên tương ứng và
   truyền riêng `user_id`/`session_id`. Giữ lịch sử theo người, vẫn giữ nguồn
   phiên/kênh của từng lời nói. Chỉ đọc memory riêng sau kiểm tra quyền phiên.
3. Simulator phát từng `message.text`, lưu phản hồi thực của bot cùng thời gian
   và trace tool. Không phát câu trả lời tham khảo thay cho bot.
4. Khi cần so ngân hàng, cấp offer đang hiệu lực làm context có nguồn, hoặc
   xây một nguồn tra cứu riêng. Chín bank tool hiện tại chưa có tool tra lãi
   ngân hàng khác. Mọi bank tool vẫn cần `as_of`; customer ID và quyền được
   truyền từ backend, không lấy từ JSON do model tự tạo.
5. Sau mỗi phiên, evaluator đọc memory thực, so với checkpoint: thông tin nào
   còn `active`, thông tin nào đã `superseded`, thông tin nào đang `conflicted`,
   câu hỏi nào còn chờ và báo giá nào đã `expired`. Truy lại bằng
   `source_message_id`, không chỉ so một câu tóm tắt gần giống.

Các checkpoint dùng `cross_session_memory_allowed` để chỉ rõ phiên nào được
đọc memory cũ. Lời khách ở phiên chưa xác minh có thể dùng để trao đổi ngay
trong phiên đó; không vì thế mà cho phép đọc hay ghi đè memory riêng đã xác
minh của người khác.

## Giới hạn tích hợp hiện tại

Đây là schema dataset riêng. CLI không seed database đang chạy, không tự tạo
40 phiên, không nạp sẵn lịch sử giả vào memory và không tự chuyển thành
`Scenario` cho `eval.runner`. Backend đã có thể chọn và xác minh cả 10 hồ sơ;
loader kiểm tra dữ liệu không có nghĩa là agent đã vượt qua 40 phiên kiểm thử.

Web và Telegram có adapter ứng dụng; Zalo và hotline trong bộ này chỉ là mô
phỏng. Bộ này chưa triển khai thêm hai adapter đó, không gửi OTP/SMS, tóm tắt
thật hoặc gọi điện thật. Các offers đối thủ vẫn cần nguồn context/tra cứu riêng
khi tích hợp; registry identity không tự đưa chúng vào prompt. Thay đổi source
chưa tự triển khai lại hay thay dữ liệu của bản demo đang chạy.

Bộ này bổ sung cho [journey memory tích hợp hiện có](DATASET.md). Hợp đồng và
quyền của bank tool được mô tả trong [hướng dẫn tích hợp](INTEGRATION.md).
