# Agent chat và function calling

Khách nhập **Số điện thoại của bạn** và bấm **Bắt đầu chat** trên màn đầu để mở
phiên đã kết nối qua `POST /auth/phone/login`. Bot mở lời ngay, nhắc nhu cầu đã
được ghi nhớ nếu khách quay lại. Khi bật `DEMO_PHONE_IDENTITY`, đây là bước vào
chat bắt buộc. **Cuộc trò chuyện mới** quay lại form trống để dùng cùng số hoặc
đổi số; hội thoại và bản nháp cũ vẫn còn trong lịch sử.

Ngoài chế độ demo, khách cũng có thể gõ câu hỏi/chọn gợi ý trực tiếp. Khi đó tin
đầu tiên tạo phiên ẩn danh qua `POST /sessions` với `{"mode":"harness"}`, rồi
agent nhận số trong tin nhắn để chọn tool như mô tả bên dưới. Không cần chọn hồ
sơ, ngày sinh hay OTP. Tải lại giữ phiên và lời chào đã lưu.

## AI chọn tool, backend kiểm tra và thực thi

**Function calling** nghĩa là model trả về tên hàm cùng đối số có cấu trúc,
thay vì chỉ viết “đã xác minh” trong câu trả lời. Backend kiểm tra đề xuất đó,
gọi hàm thật, trả kết quả cho model rồi cho model tiếp tục lượt tư vấn.

1. Tin nhắn qua guard đầu vào. Khi chưa nhận diện, agent chỉ nhận catalog tool
   công khai và `verify_identity`; chưa được đọc memory riêng của một hồ sơ.
2. Khi khách tự cung cấp số điện thoại, AI có thể đề xuất
   `verify_identity(phone=..., as_of=...)`. Backend kiểm tra đó là một số duy
   nhất của chính khách trong **tin nhắn hiện tại**, rồi tra đủ 10 identity
   hư cấu. Số không tồn tại, số của người thân hoặc số model tự đoán không mở
   hồ sơ. Nhiều số thì cần hỏi lại.
3. Tool nhận diện thành công trả `user_id` và **call brief** — bản ngữ cảnh gồm
   nhu cầu, thông tin còn chờ xác nhận và lịch sử có nguồn. Model nhận kết quả
   tool thật, catalog quyền được cập nhật, rồi tiếp tục xử lý cả nhu cầu khách
   đã viết trong cùng tin nhắn.
4. Model gọi các bank tool cần thiết. Backend kiểm tra tên hàm, JSON/schema,
   quyền phiên, số liệu khách đã xác nhận, kênh và ngày nghiệp vụ. Model không
   được tự cấp `customer_id`, cờ đăng nhập hay dùng một đối số để mở quyền.
5. Phản hồi cuối dùng fact do backend phát hành; backend kiểm tra nguồn, hiệu
   lực và disclosure trước khi hiển thị số cho khách. UI cập nhật
   `identity_status`, `user_id` và brief từ response; UI không tự quyết định
   danh tính từ nội dung khách gõ.

Ví dụ thử với hồ sơ thứ hai:

> Số điện thoại của tôi là 0900000002. Tôi muốn tiếp tục trao đổi khoản vay mua nhà.

Kết quả tool phải nối tới **Trần Minh An**, không mặc định hồ sơ Hoa. Ở ngày
nghiệp vụ 31/10/2026, khi bật lịch sử identity, brief của An có 9 lời khách từ
3 phiên lịch sử đã xác minh: khoản vay 4 tỷ, 300 tháng, thu nhập 100 triệu/tháng,
nợ 10 triệu/tháng và nhà 6 tỷ, cùng bối cảnh bàn với vợ, so ngân hàng và phí
còn thiếu. Đây là nhu cầu hư cấu từng được khai, cần xác nhận lại nếu khách
thay đổi; không phải báo giá hay phê duyệt tín dụng hiện hành.

Một phiên sau khi đã gắn thành công với một người không đổi sang người khác
bằng một số mới; cần bắt đầu chat mới. Phiên web cũ còn chưa xác minh và từng
được chọn sẵn profile được chuyển về ẩn danh, giữ transcript công khai; không
tự nối với hồ sơ đã chọn trước đây. Phiên đã nhận diện và phiên OTP đã có giữ
ranh giới danh tính hiện có.

Nhận diện phone demo là **đối chiếu lời khai với fixture**, không chứng minh
quyền sở hữu số điện thoại hay đăng nhập ngân hàng. Backend OTP Telegram vẫn
được giữ riêng để tương thích các client đang dùng
`/auth/phone/start` và `/auth/phone/confirm`; màn chat chính không còn đường
vào OTP. Claim phone demo không thay thế xác nhận contact hoặc OTP của luồng đó.

## Quyền và số liệu của tools

| Trạng thái backend | Bank tools được cấp | Tool nhận diện |
| --- | --- | --- |
| Chưa nhận diện | 6 public: `get_rate`, `get_active_promotions`, `get_card_offer`, `calc_installment`, `check_suitability`, `get_disclosures` | `verify_identity` |
| Đã qua `verify_identity` | 6 public và `send_summary`, `schedule_callback` | `verify_identity`, vẫn kiểm tra đúng hồ sơ phiên |
| Có customer login hợp lệ | Thêm `crm_lookup` | Quyền do backend xác nhận |

Vì vậy catalog thông thường có 7 function trước nhận diện và 9 sau nhận diện
(gồm identity tool). Luồng chat hiện tại không tạo customer login; CRM tiếp
tục khóa. Đăng nhập console chuyên viên cũng không phải customer login.

Backend truyền `as_of` từ clock nghiệp vụ cho mỗi tool và kiểm tra đúng kênh.
Một phép tính trả góp phải dùng `rate_fact_id` thật từ receipt cùng ngày, đúng
số tiền/kỳ hạn; ưu đãi và lãi sau ưu đãi phải khớp nguồn đã lấy. Không dùng
`offer_id` trong dataset thay cho `fact_id`, không biến lãi khách kể của ngân
hàng khác thành lãi Bankwise.

**Receipt** là kết quả có `tool_call_id`, `tool`, `as_of`, `data`, `facts`,
`error`, giúp truy lại mỗi con số. Model dùng placeholder `{fact:ID}` để
backend hiển thị số từ fact hợp lệ; số viết trực tiếp, fact lạ/hết hạn, lời
nhận diện chưa có kết quả hoặc số liệu ngân hàng thiếu disclosure đều bị kiểm
tra. Tool lỗi hay model hết ngân sách dẫn tới phản hồi an toàn, không tự tạo
kết quả tool thay thế.

`send_summary`/`schedule_callback` là mô phỏng. Hành động cần yêu cầu ở cuộc
chat hiện tại; lịch gọi phải có ngày giờ cụ thể do khách chọn. Yêu cầu cũ trong
lịch sử không tự tạo cuộc hẹn hoặc gửi lại tóm tắt. Receipt/checkpoint và
`client_message_id` giúp retry không lặp hành động đã hoàn tất.

## Lịch sử đầu vào và memory đang chạy

[History projector](../harness/memory/dataset_history.py) đọc identity/journey
đầu vào của `identity_v1`; không đọc `eval/oracle_fixtures`, `reference_text`,
đáp án assistant hoặc expected memory. Nó chọn đúng người, chỉ lấy lời khách
ở phiên fixture đã xác minh và không vượt ngày `as_of` theo giờ Việt Nam.
Với mỗi người, ngày đủ muộn có tối đa 9 lời lịch sử; phiên chưa xác minh trong
bộ 4 phiên bị loại khỏi ngữ cảnh riêng.

Fact lịch sử mang `source_origin=dataset_history`, `source_ref` tới message,
`source_at`, kênh và `dataset_id`. Call brief kết hợp chúng với memory runtime
đã xử lý của cùng người. Số mới cùng giá trị không tạo hai fact active; thông
tin runtime thay đổi mà chưa xác nhận được giữ thành conflict để hỏi lại, còn
lời xác nhận mới có thể thay thông tin cũ. Không cộng hỗ trợ tiền trả trước
của người thân thành thu nhập tháng.

Projector không seed 40 phiên vào SQLite, không chép báo giá làm bank fact,
không tạo callback/outbox hoặc chạy lại bất cứ hành động lịch sử nào. Một báo
giá hiện hành vẫn phải lấy lại qua tool đúng ngày/kênh. Zalo/hotline trong
dataset vẫn là mô phỏng, chưa có adapter live mới.

## Cấu hình local

Tạo `.env` từ [`.env.example`](../.env.example) nếu chưa có, rồi cấu hình key và
hai model trong file riêng này hoặc môi trường chạy. Không ghi các giá trị
thật vào source, prompt, Git hay log.

```dotenv
LLM_BACKEND=openai
OPENAI_API_KEY=<key của môi trường chạy>
OPENAI_MODEL=<model chính hỗ trợ function calling>
OPENAI_BACKUP_MODEL=<model dự phòng khác model chính>
IDENTITY_HISTORY_ENABLED=true
LLM_CALL_BUDGET_SECONDS=20
```

Ba biến `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_BACKUP_MODEL` đều bắt buộc
khi bật OpenAI; hai tên model phải khác nhau và tài khoản phải có quyền dùng
chúng. `IDENTITY_HISTORY_ENABLED` mặc định bật cho OpenAI, có thể đặt `false`
để chỉ dùng memory runtime. Với `rules`, lịch sử mặc định tắt, có thể bật rõ
ràng để kiểm tra projector bằng fixture.

Chạy backend sau khi nạp `.env`, theo hướng dẫn local trong README. Mỗi lượt
message dùng ngân sách đồng bộ chung 60 giây cho classifier, agent và các bước
memory/handoff đồng bộ; agent vẫn có giới hạn 8 vòng model và 12 lần tool.
`LLM_CALL_BUDGET_SECONDS=20` là ngân sách cho một bước LLM, chia cho primary
và backup, đồng thời bị giới hạn bởi phần ngân sách chung còn lại. Job memory
chạy nền sau response dùng ngân sách riêng. Đây là giới hạn công việc đồng bộ,
không bảo đảm chính xác thời gian truyền HTTP hoặc xử lý ở hệ thống bên ngoài.

`LLM_BACKEND=rules` là đường kiểm tra offline, không phải bằng chứng AI đã
chọn `verify_identity` hoặc bank tool. Khi bật OpenAI mà provider/tool-loop
thất bại, backend báo gián đoạn thay vì âm thầm giả làm câu trả lời AI bằng rules.

## Kiểm tra

```sh
uv run pytest -q tests/test_agent_tools.py tests/test_agent_identity.py tests/test_dataset_callbrief.py
uv run ruff check harness bank_tools eval simulators tests tests_eval
uv run mypy harness
npm --prefix web run lint
npm --prefix web run test
npm --prefix web run build
```

Các test backend dùng provider mock để kiểm tra chu trình function calling,
đối số/quyền/ngày, nhận diện, call brief và retry; chúng không gọi OpenAI thật.
Các E2E frontend dùng response API giả lập để kiểm tra bootstrap ẩn danh,
phone thứ hai, phone không có hồ sơ, public chat, retry và navigation. Lệnh
`npm --prefix web run test:e2e` cần trình duyệt Playwright đã được cài.

`web/tests/live-smoke.mjs` chạy backend `rules`, xác minh fixture bằng endpoint
trực tiếp rồi kiểm tra luồng ứng dụng. Log của script ghi rõ đây không phải
test AI tự quyết định gọi tool. Tài liệu này không khẳng định đã chạy smoke
OpenAI thật hoặc các test UI trên máy local; kết quả thực tế cần đọc từ lần
chạy kiểm tra tương ứng.

### Smoke OpenAI thật khi đã có cấu hình

```sh
# Chỉ đọc và kiểm tra cấu hình; không gọi mạng hoặc chạy backend.
.venv/bin/python scripts/smoke-agent.py --env-file .env --check-config

# Opt-in riêng: chạy model thật, có thể phát sinh phí API.
.venv/bin/python scripts/smoke-agent.py --env-file .env --run
```

Script đọc các dòng `KEY=value`, hỗ trợ quote/comment và `export`, không chạy
shell, không nội suy biến hay lệnh trong `.env`. Biến môi trường có giá trị
ưu tiên hơn file. Thiếu key/model hoặc hai model trùng nhau thì trả exit code
`2`, chỉ in tên biến; kiểm tra cấu hình không xác nhận key còn quota hay model
đã được cấp quyền.

`--run` tạo DB/log tạm và backend localhost trên cổng ngẫu nhiên, credential
admin/staff ngẫu nhiên, tắt Telegram và bật history; không dùng cổng 8000 hoặc
DB demo. Clock của smoke là 31/10/2026 để khớp tập fixture. Hai lượt kiểm tra
AI thực sự gọi identity cho An rồi gọi rate/promotion/disclosure với nhu cầu
đã xác nhận. Script chấm receipt, nguồn fact và phân khúc priority, không
hardcode giá lãi. Thứ tự gọi tool có thể thay đổi; thiếu receipt bắt buộc thì
smoke thất bại, không tự gửi thêm lời hỏi để che một lượt chưa hoàn thành.

Terminal chỉ nhận tổng kết số lượng/tên tool hoặc mã kiểm tra thất bại, không
in key, token, prompt, phản hồi model hay log provider. Tiến trình và thư mục
tạm được dọn khi kết thúc. Script đã được chuẩn bị; chưa chạy `--run` khi chưa
có cấu hình và sự cho phép gọi model thật.
