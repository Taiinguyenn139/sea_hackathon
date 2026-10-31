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
thực. Luồng nhận diện bằng lời khai phone bên dưới chỉ mở hồ sơ hư cấu của
môi trường demo, không chứng minh quyền sở hữu số điện thoại thật.

`identity_status` và `verified_at` thuộc **từng phiên**. Một phiên đã xác minh
không làm phiên ở kênh khác tự được xác minh. Các trường này trong JSON chỉ mô
tả bối cảnh giả lập, không phải bằng chứng đăng nhập hoặc kết quả gọi dịch vụ
xác thực thật. `date_of_birth` chỉ còn là metadata hư cấu, không dùng để nhận
diện; không chép ngày sinh vào prompt, memory hay lịch sử tool.

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

## Runtime: từ phiên ẩn danh tới call brief

Màn chat chính cho gửi câu hỏi hoặc chọn gợi ý ngay, không có bộ chọn profile,
form ngày sinh, OTP entry hay panel identity. Frontend tự gọi `POST /sessions`
với `{"mode":"harness"}` ở lần gửi đầu, rồi gửi tin nhắn bằng capability token
của phiên đó. Endpoint public tạo phiên ẩn danh; không dùng `demo_profile` từ
client để chọn sẵn Hoa hoặc An. Alias `hoa`/`minh` vẫn được giữ trong registry
cho memory cũ và các client/admin tương thích.

Khi bật `LLM_BACKEND=openai`, **AI tự quyết định gọi function
`verify_identity`** từ lời khách hiện tại. Backend tra đủ 10 hồ sơ bằng
`harness/identity.py`, xác nhận số tự khai là duy nhất và đúng đối số tool,
rồi mới gắn phiên với người đó. Ví dụ `0900000002` hoặc `+84900000002` nối tới
Trần Minh An, không mặc định Hoa. Một tin có nhiều số, số của người thân, số
không có trong fixture hoặc số model tự đoán không mở hồ sơ. Ngày sinh không
tham gia luồng này và không được trả trong API danh sách hồ sơ.

Kết quả tool thật trả `identity_status`, `user_id` và call brief; agent nhận
kết quả để tiếp tục cả nhu cầu trong cùng tin nhắn. Đây không còn là nhánh
server thấy regex phone rồi tự kết thúc lượt. [Hướng dẫn agent chat](AGENT_CHAT.md)
mô tả cấu hình, catalog quyền, giới hạn và kiểm tra receipt chi tiết.

Bên trong DB, hai người đầu giữ `customer_id` là `hoa`/`minh` để tiếp tục memory
cũ; 8 người khác dùng phone làm khóa runtime. `user_id` bên ngoài luôn là phone
sau nhận diện. Trước đó `user_id` có thể là `null`. `runtime_profile` chỉ ánh xạ
khóa cũ, không cấp quyền và không làm bỏ bước nhận diện.

Phiên đã gắn thành công không đổi người bằng một số mới; cần bắt đầu chat mới.
Phiên web cũ còn chưa xác minh, từng được chọn profile, được đưa về ẩn danh và
giữ transcript công khai. Không tự chuyển trạng thái xác minh giữa phiên hoặc
kênh. Các giới hạn thử sai vẫn là 5 lần/15 phút theo identity và nguồn gọi;
đối số phone trong audit tool được che. Retry cùng `client_message_id` trả kết
quả đã lưu và không tự tạo thêm hành động.

Đây là nhận diện hồ sơ hư cấu bằng lời khai, không phải customer login hoặc
kiểm chứng sở hữu số. CRM vẫn khóa. Backend OTP Telegram tại
`/auth/phone/start`, `/auth/phone/confirm` còn được giữ để tương thích; màn chat
chính không còn đường vào OTP. `POST /sessions/{id}/verify` nhận payload phone
cho client/fixture trực tiếp, nhưng gọi endpoint đó không chứng minh AI đã
chọn function. Các đường Brief/admin link vẫn yêu cầu quyền hiện có.

### Lịch sử nào được đưa vào brief?

Khi `IDENTITY_HISTORY_ENABLED=true`, projector ở
`harness/memory/dataset_history.py` đọc các file **đầu vào**, chỉ lấy lời khách
của đúng người từ các phiên fixture đã xác minh và có thời gian không vượt
ngày nghiệp vụ `as_of` tại Việt Nam. Không đọc oracle, đáp án assistant hoặc
expected memory. Cờ `verified` trong journey chỉ chọn sự kiện lịch sử hư cấu,
không xác minh phiên đang hỏi.

Ở ngày 31/10/2026, An có 9 lời khách lịch sử từ 3 phiên đã xác minh. Phiên chưa
xác minh với yêu cầu sửa khoản vay thành 99 triệu bị loại khỏi memory riêng.
Các nhu cầu 4 tỷ/300 tháng, thu nhập 100 triệu, nợ 10 triệu và giá nhà 6 tỷ có
nguồn message, thời gian và kênh riêng; cùng bối cảnh hỏi vợ, so ngân hàng và
phí còn chờ. Đây là customer facts cũ, không phải điều khoản ngân hàng hay
phê duyệt mới.

Brief kết hợp lịch sử đó với memory runtime đã xử lý của cùng người. Fact có
`source_origin`, `source_ref`, `source_at`, kênh và trạng thái để truy lại nguồn.
Runtime xác nhận mới có thể thay thế giá trị cũ; thay đổi chưa được xác nhận
cần giữ conflict và hỏi lại. Projector không seed 40 phiên, không phát lại câu
trả lời mẫu, không tạo callback/outbox và không nhập báo giá thành bank fact.
Báo giá hiện hành phải lấy lại bằng tool đúng ngày/kênh.

Ví dụ gọi luồng chat local bằng `httpx`, sau khi cấu hình OpenAI theo
[hướng dẫn](AGENT_CHAT.md#cấu-hình-local):

```python
from uuid import uuid4

import httpx

with httpx.Client(base_url="http://127.0.0.1:8000", timeout=120) as client:
    created = client.post("/sessions", json={"mode": "harness"})
    created.raise_for_status()
    session = created.json()
    response = client.post(
        f"/sessions/{session['session_id']}/messages",
        headers={"Authorization": "Bearer " + session["session_token"]},
        json={
            "text": "Số điện thoại của tôi là 0900000002. Tôi muốn tiếp tục khoản vay mua nhà.",
            "client_message_id": str(uuid4()),
        },
    )
    response.raise_for_status()
    print(response.json()["identity_status"], response.json()["user_id"])
```

Kết quả nhận diện thành công là `verified` và `0900000002`; nếu tool/provider
bị lỗi, kiểm tra trace thay vì tự coi câu trả lời văn bản là bằng chứng thành
công. Không ghi capability token hoặc đối số nhận diện thô vào log. Khi sửa
inputs, kiểm tra dataset rồi khởi động lại backend để registry/cache nạp lại.

```sh
uv run pytest -q tests/test_agent_tools.py tests/test_agent_identity.py tests/test_dataset_callbrief.py
uv run pytest -q tests_eval/test_identity_dataset.py tests/test_identity_verification.py
```

Các test provider mock/fixture không phải kết quả gọi OpenAI thật. Backend
`rules` dùng kiểm tra offline, không chứng minh AI chọn tool hoặc đạt bộ 40
phiên. Chưa khẳng định smoke OpenAI hoặc test UI local đã chạy thành công.

## Tích hợp vào chatbot sau này

1. Dùng phiên ẩn danh và để agent gọi `verify_identity` qua function calling;
   backend kiểm tra lời khai và thực thi. Thiết lập liên kết tài khoản kênh
   bằng quyền admin khi cần; không lấy cờ `verified` trong file khách gửi để
   cấp quyền.
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
40 phiên và không tự chuyển thành `Scenario` cho `eval.runner`. Runtime có
thể project lời khách lịch sử vào call brief khi bật flag, không nhập chúng
thành turn/action trong DB. Backend nhận diện đủ 10 hồ sơ qua tool; loader kiểm
tra dữ liệu không có nghĩa agent đã vượt qua 40 phiên kiểm thử.

Web và Telegram có adapter ứng dụng; Zalo và hotline trong bộ này chỉ là mô
phỏng. Bộ này chưa triển khai thêm hai adapter đó, không gửi OTP/SMS, tóm tắt
thật hoặc gọi điện thật. Các offers đối thủ vẫn cần nguồn context/tra cứu riêng
khi tích hợp; projector không coi chúng là bank facts. Việc sửa source hay
bật flag không tự triển khai lại ứng dụng hoặc tạo một lần chạy benchmark.

Bộ này bổ sung cho [journey memory tích hợp hiện có](DATASET.md). Hợp đồng và
quyền của bank tool được mô tả trong [hướng dẫn tích hợp](INTEGRATION.md).
