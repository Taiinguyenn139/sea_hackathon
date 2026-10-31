# Dự án hackathon

Quy ước làm việc của team: [CONTRIBUTING.md](CONTRIBUTING.md).

Bankwise là chatbot fintech dùng dữ liệu ngân hàng hư cấu, gồm web chat,
console chuyên viên, benchmark và adapter Telegram. Backend FastAPI/SQLite;
frontend React/Vite. Chạy một backend process để giữ hợp đồng SQLite/checkpoint.

## Chạy local

Cần Python 3.12, uv và Node.js 24. Docker cần cho kiểm thử image triển khai.

```sh
uv sync --frozen
npm --prefix web ci
cp .env.example .env
set -a
source .env
set +a
uv run uvicorn harness.api:app --host 127.0.0.1 --port 8000
```

Ở terminal thứ hai, chạy `npm --prefix web run dev` rồi mở địa chỉ Vite hiển thị.
[.env.example](.env.example) mô tả cấu hình local; không dùng credential mẫu để deploy.
Backend `rules` chạy không cần API key. Telegram và backend LLM cần credential thật
qua kho secret, không đưa giá trị vào Git hoặc chat.

[Luồng agent chat](docs/AGENT_CHAT.md) bắt đầu trực tiếp từ tin nhắn đầu tiên,
tự tạo phiên ẩn danh và để AI chọn gọi `verify_identity` khi khách cung cấp số
điện thoại của mình. Không còn chọn hồ sơ hoặc nhập mã OTP trên màn chat chính.
Để chạy luồng AI, cấu hình `LLM_BACKEND=openai`, `OPENAI_API_KEY`,
`OPENAI_MODEL`, `OPENAI_BACKUP_MODEL` và `IDENTITY_HISTORY_ENABLED=true`
trong `.env`; hai model phải khác nhau. `rules` dành cho kiểm tra offline, không chứng minh AI đã chọn tool.

[Cấu hình Telegram](docs/telegram.md): tạo bot, nạp credential và đăng ký webhook.
Khách mới có hồ sơ riêng và chat ngay; chia sẻ số điện thoại để tiếp tục cùng
ngữ cảnh trên web. Liên kết hồ sơ demo là tùy chọn. Script kiểm tra kết
nối và đăng ký webhook mà không in credential.

## Kiểm tra an toàn đầu vào

Web chat, nội dung yêu cầu handoff và Telegram đều được kiểm tra trước khi ghi
turn, enqueue memory hoặc gọi agent, áp dụng cho cả `harness`, `no_memory` và
`full_history`. Telegram kiểm tra trước khi lưu inbox; tin bị chặn chỉ lưu mã
guard và placeholder, không lưu nguyên văn hoặc các trường dư của provider.

- `G8`: chặn các giá trị OTP/PIN/CVV, mật khẩu, khóa API/private key, số thẻ,
  số tài khoản và giấy tờ định danh nhận diện được. Các luật chuẩn hóa Unicode,
  đọc nhãn và kiểm tra checksum số thẻ. Câu hỏi về OTP/mật khẩu không chứa bí mật,
  thu nhập, khoản vay, kỳ hạn và nhận diện bằng số điện thoại demo vẫn được xử lý.
- `G9`: chặn các mẫu prompt injection trực tiếp như ghi đè chỉ dẫn, giả vai system,
  yêu cầu lộ prompt nội bộ hoặc tắt kiểm soát. Khi `LLM_BACKEND=openai`, mỗi đầu
  vào vượt qua luật còn được phân loại ngữ nghĩa bằng một lần gọi LLM, chỉ nhận
  text hiện tại, không nhận lịch sử hoặc tools. Classifier dùng primary/backup
  và ngân sách thời gian như các bước LLM khác, ghi log `purpose=input_guard`.
  Các token số điện thoại Việt Nam nhận diện được được thay bằng `[phone]` trước
  khi gửi classifier. Backend `rules` chỉ dùng luật.
- `G10`: nếu bộ phân loại LLM hết ngân sách/attempts hoặc gặp lỗi không thể retry,
  chặn yêu cầu hiện tại và đề nghị thử lại. Không tiếp tục agent khi chưa kiểm
  tra được đầu vào.

Guard trả `guard_events` với `stage=input`, `action=block` và câu trả lời cố định,
không lặp lại dữ liệu bị chặn. Web chat không lưu turn bị chặn hoặc giữ
`client_message_id` của turn đó; handoff trả HTTP 400 nếu reason bị chặn.
Retry của turn đã hoàn tất không gọi lại classifier; Telegram retry cùng update
cũng không phân loại lại. Với OpenAI, số lần gọi model phụ thuộc các vòng agent
và tool cần dùng; không còn cố định một bước plan rồi một bước draft. Mỗi lượt
tool-loop có tối đa 8 vòng model, 12 lần thực thi tool và ngân sách 60 giây.
Mỗi lần gọi LLM dùng ngân sách riêng, chia cho primary/backup; classifier và
các job memory có ngân sách riêng ngoài vòng tool-loop này. Đầu vào bị luật
chặn không gọi LLM; bị classifier chặn không vào agent.
Tin nhắn có số điện thoại vẫn qua guard rồi được AI xem xét gọi
`verify_identity`; backend kiểm tra số đó có phải lời tự khai trong lượt hiện
tại hay không. Nhu cầu trong cùng tin nhắn được tiếp tục xử lý sau nhận diện.
Contact được chia sẻ qua nút Telegram tiếp tục dùng kiểm tra quyền sở hữu riêng,
không gửi classifier.

Đây là phòng vệ nhiều lớp, chưa bảo đảm phát hiện mọi prompt injection hoặc mọi
loại PII. Nội dung vượt qua luật được gửi tới classifier khi bật OpenAI; các luật
chưa bao phủ mọi cách che giấu/mã hóa bí mật. Không tự làm sạch lịch sử đã lưu.
Quyền truy cập, xác minh danh tính, kiểm tra tool và guard đầu ra vẫn cần được giữ.
Hướng dẫn tham khảo: [OWASP Prompt Injection Prevention](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html).

## Kiểm tra và build

```sh
uv run pytest -q
uv run ruff check harness bank_tools eval simulators tests tests_eval
uv run mypy harness
npm --prefix web run lint
npm --prefix web run test
npm --prefix web run build
docker build -t memory-harness:deploy-check .
python3 deploy/test-container.py
```

Kết quả benchmark đi kèm là tập dev với dữ liệu giả. Chưa có kết quả held-out độc lập.

## Dữ liệu memory

[Hướng dẫn dataset](docs/DATASET.md) mô tả journey ba phiên, schema scenario,
expected memory và cách bật checkpoint checks trong eval runner bằng
`--memory-oracles eval/oracle_fixtures/memory`. Dữ liệu dùng chung mock ngân hàng
hiện có; expected memory chỉ dành cho evaluator.

[Bộ identity đa phiên](docs/IDENTITY_DATASET.md) cung cấp 10 người dùng theo số
điện thoại, 40 phiên trên 4 kênh, lời khách và đáp án riêng cho các tình huống
hỏi người nhà, so ngân hàng và tiếp tục hội thoại sau khi đổi kênh. Backend dùng
cùng nguồn identity để nhận diện số điện thoại gửi trong từng phiên chat demo;
AI nhận call brief từ lịch sử đầu vào đúng ngày và memory runtime sau khi tool
nhận diện thành công. Không yêu cầu ngày sinh; CRM vẫn cần đăng nhập riêng.

## Deploy

[Hướng dẫn GCP](docs/deployment.md) sở hữu cách cấu hình variables, OIDC/Secret Manager,
kích hoạt workflow, xem log, kiểm tra URL và rollback. [Workflow](.github/workflows/deploy-gcp.yml)
kiểm tra PR; sau merge vào `main`, chỉ deploy khi `GCP_DEPLOY_ENABLED=true` và môi trường
`demo` đã được cấu hình. URL demo nằm trong biến `APP_URL` của environment này;
kiểm tra health và revision theo hướng dẫn vận hành để xác nhận phiên bản đang chạy.

## Tools ngân hàng

[Hướng dẫn tích hợp](docs/INTEGRATION.md) mô tả 9 tool từ nhánh tuannv, quyền truy cập,
ngày gọi bắt buộc và adapter agent trên main. [Dữ liệu giả lập](docs/DATA.md) giải thích
fixtures nghiệp vụ. Backend nhận diện phone demo dùng
`simulators/datasets/identity_v1/identities.json` qua registry `harness/identity.py`;
alias `hoa`/`minh` được giữ để dùng tiếp memory cũ.
