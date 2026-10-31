# Dự án hackathon

Quy ước làm việc của team: [CONTRIBUTING.md](CONTRIBUTING.md).

Bankwise là chatbot fintech dùng dữ liệu ngân hàng hư cấu, gồm web chat,
console chuyên viên, benchmark và adapter Telegram. Backend FastAPI/SQLite;
frontend React/Vite. Chạy một backend process để giữ hợp đồng SQLite/checkpoint.

## Các cuộc trò chuyện trên web

- Sidebar liệt kê các phiên, đặt tên theo câu hỏi đầu tiên được chấp nhận; có tìm kiếm và đổi tên. Trên điện thoại dùng nút mở danh sách phiên trong header.
- **Cuộc trò chuyện mới** tạo transcript và capability riêng, giữ cùng danh tính đã kết nối. Không kết thúc phiên cũ hay hủy handoff đang chờ. Memory của cùng khách hàng tiếp tục tuân theo quyền truy cập hiện có.
- Chuyển phiên giữ bản nháp, lỗi gửi lại và nội dung từng phiên trong bộ nhớ UI. Trong lúc đang gửi, thao tác tạo/chuyển phiên tạm khóa để không gắn phản hồi vào sai hội thoại.
- Danh sách/tên/capability lưu trong `sessionStorage` của tab hiện tại; tải lại tab lấy transcript từ backend. Chưa có tài khoản hoặc API liệt kê toàn bộ phiên khách hàng, nên không đồng bộ danh sách sang thiết bị/tab khác. Bản nháp chưa gửi không được lưu lâu dài; không lấy lại được danh sách khi đóng tab/xóa storage.
- `POST /sessions/{sid}/new` yêu cầu capability của chính phiên nguồn, chỉ tạo phiên cho cùng khách hàng. API không cung cấp danh sách phiên công khai hoặc cho phép token một phiên đọc transcript phiên khác.

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
cũng không phân loại lại. Với OpenAI, một turn tư vấn được chấp nhận, không có
memory backlog, gọi LLM bốn lần: input classifier, plan, draft và memory extraction.
Đầu vào bị luật chặn không gọi LLM; bị classifier chặn chỉ gọi classifier một lần.
Đây là số bước logic: mỗi bước có thể gửi tối đa hai HTTP attempts khi primary
thất bại và cần backup. Turn bình thường không backlog có tối đa tám attempts;
riêng classifier có tối đa hai. Identity/brief có thể flush memory trước khi trả
lời; các lượt đã có memory backlog cần tính thêm các job đó.
Tin nhắn có số điện thoại cũng đi qua guard trước nhánh nhận diện; nhánh này
không chạy plan/draft hoặc enqueue nhu cầu vào memory. Contact được chia sẻ qua
nút Telegram tiếp tục dùng kiểm tra quyền sở hữu riêng, không gửi classifier.

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

## Cải thiện tư vấn từ kết quả QA/QC

Đăng nhập chuyên viên, mở `/benchmark`, chọn lượt dev đã hoàn tất và xem
**Cải thiện khả năng bán hàng** bên dưới bảng KPI. Hệ thống ưu tiên lỗi giá,
nguồn dữ liệu, thiếu đề xuất, hỏi lặp và thiếu ngữ cảnh bàn giao; mỗi đề xuất
có dẫn chứng hội thoại, bước sửa và lời tư vấn mẫu có thể chỉnh sửa.
Tỷ lệ chuyển đổi thấp chỉ tạo giả thuyết cần rà soát; các ca từ chối vì không
phù hợp, chưa xác thực hoặc lỗi công cụ không được đưa vào dẫn chứng cơ hội bán.

Lưu lời tư vấn, ghi chú và tiến độ (`Đề xuất` → `Đang thực hiện` → `Chờ đánh giá
lại`) hoặc xuất kế hoạch JSON đã lưu. Kế hoạch được giữ trong SQLite và chưa
tự áp dụng vào chatbot. Đội ngũ cập nhật code/playbook, chạy lại benchmark rồi
chọn lượt sau thay đổi để đối chiếu KPI. Hai lượt phải là dev đã hoàn tất,
cùng backend, seed, số lần lặp, oracle, scenario, memory oracle và dữ liệu ngân
hàng. Chỉ xác nhận có KPI cải thiện khi không có KPI suy giảm, không mất mẫu
và các quality gate của lượt sau đều đạt. Kết quả mô phỏng chưa chứng minh
doanh số thực tế hay quan hệ nhân quả của từng thay đổi.

API dành cho chuyên viên: `GET /eval/runs/{rid}/improvements`,
`PATCH /eval/runs/{rid}/improvements/{action_id}` (cần CSRF) và
`GET /eval/runs/{rid}/comparison?candidate_id=...`.
Bảng `sales_improvements` được tạo tự động khi mở database hiện có, không cần
dependency hoặc cấu hình mới. Dữ liệu held-out và lượt chưa hoàn tất không
được dùng để lập kế hoạch cải thiện.

## Quản lý kịch bản và luyện tập

Mở `/sales` từ menu chuyên viên hoặc màn hình benchmark. Tất cả thao tác dùng
đăng nhập console hiện tại; tên người duyệt là thông tin ghi nhận nội bộ,
không phải danh tính được xác thực bằng tài khoản cá nhân.

### Chatbot

1. Chọn lượt dev và đề xuất `next_step` hoặc `relevant_offer`, phiên bản gốc,
   tình huống và lời tư vấn mới. Bản nháp bất biến; sửa tiếp bằng bản nháp mới.
   Lỗi số liệu, nguồn, memory, tool và bàn giao cần task kỹ thuật riêng.
2. Chạy đánh giá cách ly. Worker xử lý tuần tự trên app và database tạm, không
   gọi reset vào database tư vấn. Kết quả được lưu trong job, không ghi đè các
   file benchmark đã xuất bản. Job đang chạy khi khởi động lại được đánh dấu
   gián đoạn; người dùng chạy lại thủ công.
3. Chỉ mở duyệt khi KPI mục tiêu tăng theo đúng chiều, không có KPI suy giảm,
   không mất mẫu và các quality gate đều đạt. Điều kiện/model/dữ liệu của
   baseline phải khớp môi trường hiện tại; không dùng held-out để điều chỉnh.
4. Nhập người phụ trách, nhận xét, duyệt rồi kích hoạt bằng thao tác riêng.
   Phiên chat mới dùng bản mới; phiên đang mở giữ nguyên phiên bản đã chọn.
   Có lịch sử kích hoạt và nút quay lại bản đã áp dụng hoặc `v1`.

Kịch bản theo băn khoăn ưu tiên hơn kịch bản theo giai đoạn; mỗi lượt chọn
một mục đầu tiên để kết quả có thể lặp lại. Lời bổ sung không thay thế dữ liệu
tool, xác thực, từ chối hồ sơ không phù hợp hay công bố bắt buộc. Trạng thái
`approved` nghĩa là đã duyệt; phiên bản đang dùng được hiển thị riêng ở đầu trang.

Nếu baseline đi kèm repo không còn khớp dữ liệu, tạo lượt mới bằng backend
cách ly có sẵn (lệnh ghi kết quả vào `eval/results`, không commit tự động):

```sh
uv run python scripts/verify-dev.py --repetitions 1 --seed 42 --memory-oracles eval/oracle_fixtures/memory
```

### Chuyên viên

Tạo hồ sơ mã/tên, chọn dẫn chứng QA/QC, soạn câu hỏi và từ khóa cần có.
Người phụ trách rà soát bài nháp rồi phát hành. Chọn hồ sơ để nộp bài hoặc
luyện lại; mỗi lần nộp có lịch sử riêng. Câu trả lời mẫu không hiện trong
biểu mẫu làm bài; console vẫn là không gian dùng chung cho người phụ trách.

Checklist đối chiếu số liệu theo tình huống, ngày/kênh và oracle hiện có;
phát hiện từ khóa thiếu và dấu hiệu hỏi lại để người chấm rà soát. Nếu thiếu
ngữ cảnh oracle, giao diện nói rõ cần đối chiếu thủ công. Checklist không tự
chấm kỹ năng hay kết luận tính phù hợp ngữ nghĩa.

Người phụ trách chấm 5 tiêu chí (0–4 mỗi tiêu chí), nhận xét có dẫn chứng và
xác nhận đã rà soát điều kiện an toàn. Điểm tổng là tổng điểm nhân 5; đạt từ
75/100 khi đã xác nhận an toàn và không còn số liệu chưa khớp được phát hiện.
Điểm và nhận xét không sửa đè; muốn đánh giá lại thì tạo lần nộp mới.

API có CSRF cho thao tác ghi: `GET /sales/overview`,
`POST /sales/playbooks`, các thao tác `/{id}/evaluate`, `/{id}/review`,
`/{id}/activate`, `/{id}/rollback` dưới `/sales/playbooks`; và
`POST /sales/practice/profiles`, `/sales/practice/exercises`,
`/sales/practice/exercises/{id}/publish`, `/sales/practice/exercises/{id}/attempts`,
`/sales/practice/attempts/{id}/grade`.

SQLite tự tạo bảng `sales_objects` và `session_playbooks` khi mở database cũ.
Phiên cũ chưa có bản ghim dùng `v1`. Không cần dependency mới. Khi kiểm tra
E2E song song với worktree khác, dùng `PLAYWRIGHT_PORT=5291 npm --prefix web run test:e2e`.

## Dữ liệu memory

[Hướng dẫn dataset](docs/DATASET.md) mô tả journey ba phiên, schema scenario,
expected memory và cách bật checkpoint checks trong eval runner bằng
`--memory-oracles eval/oracle_fixtures/memory`. Dữ liệu dùng chung mock ngân hàng
hiện có; expected memory chỉ dành cho evaluator.

[Bộ identity đa phiên](docs/IDENTITY_DATASET.md) cung cấp 10 người dùng theo số
điện thoại, 40 phiên trên 4 kênh, lời khách và đáp án riêng cho các tình huống
hỏi người nhà, so ngân hàng và tiếp tục hội thoại sau khi đổi kênh. Backend dùng
cùng nguồn identity để nhận diện số điện thoại gửi trong từng phiên chat demo;
không yêu cầu ngày sinh và CRM vẫn cần đăng nhập riêng.

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

## Nhận diện cùng hồ sơ trên web và Telegram trong bản demo

Demo GCP bật `DEMO_PHONE_IDENTITY=true`: nhập số điện thoại vào ô ở màn hình đầu
trên web, bấm **Bắt đầu chat**, rồi nhập cùng số trong chat Telegram. Hai kênh dùng
chung hồ sơ và memory, không cần OTP. `0901234567` và `+84 90 123 4567` là cùng
một số. Số trong bộ mẫu nối vào hồ sơ có sẵn; số mới tạo hồ sơ demo riêng.

Số nhập ở màn hình đầu không tạo tin nhắn. Mỗi phiên có transcript, bản nháp và
luồng xử lý riêng; các phiên cùng hồ sơ vẫn đóng góp và đọc memory chung.
Tạo phiên mới không sao chép hội thoại hoặc handoff của phiên trước. Muốn đổi sang số khác sau
khi đã kết nối, mở phiên mới rồi nhập số khác. Chi tiết ở [Telegram](docs/telegram.md).
Ngoài deployment demo, cấu hình mặc định tắt và giữ luồng contact/OTP hiện có.
