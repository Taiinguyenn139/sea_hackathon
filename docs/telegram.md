# Cấu hình Telegram

## Chat web chính và các tích hợp nhận diện riêng

Chat web chính hiển thị lời chào của bot và cho nhập tin nhắn ngay. Lần gửi đầu gọi `POST /sessions` với
`{mode: "harness"}` để tạo phiên ẩn danh. Khách gửi số của mình trong chat; AI
quyết định gọi `verify_identity` và dùng kết quả tool để kết nối hồ sơ, call brief
và memory. Không có form số điện thoại, chọn hồ sơ hoặc OTP trên màn hình này,
kể cả khi bật `DEMO_PHONE_IDENTITY=true`.

Mỗi phiên giữ transcript, bản nháp, checkpoint và handoff riêng. Phiên web demo
mới bắt đầu ẩn danh để nhận diện người khác; lịch sử phiên cũ vẫn còn. Những
phiên đã kết nối cùng hồ sơ đọc và đóng góp memory chung theo quyền backend.
Nhận diện demo không thay thế đăng nhập khách hàng để dùng CRM.

Bot chủ động hỏi nhu cầu ngay sau **Start** hoặc khi kết nối số điện thoại.
Khách đã nhận diện quay lại được nhắc nhu cầu còn hiệu lực và hỏi muốn tiếp tục
hay cập nhật. Không dùng giá cũ làm báo giá hiện tại; thông tin mâu thuẫn cần xác
nhận. Khách Telegram đã kết nối không cần chia sẻ lại số khi `/start`.
Web mới có lời chào chung; sau khi khách gửi số, AI nhận diện bằng tool rồi dùng
brief để tiếp nối nhu cầu. Các API tích hợp nhận diện riêng trả lời mở đầu kèm
ngữ cảnh ngay khi tạo phiên. Lời mở đầu được lưu như tin của trợ lý, không tạo
memory của khách; tải lại web hoặc gửi lại cùng update không nhân đôi lời chào.

Theo [giới hạn Telegram](https://core.telegram.org/bots), bot không thể nhắn riêng
cho người chưa từng bấm Start/gửi tin. Hiện không gửi nhắc theo lịch hoặc khi
khách chỉ mở lại cửa sổ Telegram.

Các đường tích hợp backend vẫn được giữ:

- `POST /auth/phone/login` nhận `{phone}`, trả session/brief ngay, không OTP.
  `PhoneLogin` là component riêng cho hợp đồng này; chat chính không sử dụng.
- Khi `DEMO_PHONE_IDENTITY=true`, `/auth/phone/login` và API legacy
  `POST /demo/phone/session` mở hồ sơ demo theo số, transcript có lời mở đầu của bot. Số nhập qua
  API không tạo một tin nhắn; số trong bộ mẫu dùng hồ sơ có sẵn, số mới có hồ sơ
  demo riêng. Telegram demo nhận contact hoặc số bằng văn bản theo cùng liên kết.
- Khi tắt flag, `/auth/phone/login` mở hồ sơ hội thoại cá nhân. Telegram yêu cầu
  contact do chính người gửi chia sẻ trước khi xử lý tư vấn. Các endpoint OTP cũ
  vẫn tồn tại để tương thích, không thuộc luồng chat chính.

Số được chuẩn hóa E.164, ví dụ `0901234567` và `+84 90 123 4567` cùng số.
Các API demo chỉ dựa trên số, không chứng minh quyền sở hữu số; chỉ dùng dữ liệu
hư cấu. Bảng `demo_phone_links` lưu liên kết demo riêng, không tự gộp memory từ
một hồ sơ khác. Adapter Telegram thật cần token BotFather, webhook secret khớp
backend và URL HTTPS public. Phần dưới hướng dẫn cấu hình adapter và API riêng.

## Token bot

Mở [@BotFather](https://t.me/BotFather), gửi `/newbot`, đặt tên và username
theo hướng dẫn. Nếu đã có bot, dùng `/mybots` để quản lý token của bot đó.
Lưu token vào file riêng, không gửi vào chat hoặc commit vào Git. Có thể nhập
ẩn trong terminal tại repository root:

```sh
python3 - <<'PY'
import getpass
import os
from pathlib import Path

folder = Path("secrets")
folder.mkdir(mode=0o700, exist_ok=True)
path = folder / "telegram-token.txt"
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.fchmod(fd, 0o600)
with os.fdopen(fd, "w") as output:
    output.write(getpass.getpass("BotFather token (hidden): ").strip() + "\n")
print("Token saved; value hidden.")
PY
```

Thư mục `secrets/` đã được `.gitignore` loại trừ. Token là credential của bot,
không phải số điện thoại, mã OTP hoặc mật khẩu tài khoản Telegram.

## Cấu hình backend

- `TELEGRAM_BOT_TOKEN`: token của bot vừa tạo.
- `TELEGRAM_WEBHOOK_SECRET`: chuỗi ngẫu nhiên dài ít nhất 32 ký tự, chỉ gồm
  chữ cái, chữ số, `_` và `-`. Tạo bằng `secrets.token_urlsafe(48)` và lưu trực
  tiếp vào file cấu hình hoặc secret store, không in ra log.
- `ADMIN_TOKEN`: credential quản trị backend, chỉ dùng khi muốn liên kết chat
  với một hồ sơ ngân hàng demo đã có.

Local dùng `.env` theo README; backend cần URL HTTPS public qua tunnel để nhận
webhook. Nạp các biến **trước** khi khởi động backend; Settings đọc môi trường
khi import module.

GCP dùng secret `memory-harness-runtime` trong project lấy từ GitHub environment
`demo`. Thêm hai biến Telegram vào payload hiện có, giữ nguyên credential
admin/staff và các biến khác. Không thay toàn bộ payload bằng hai biến Telegram.
Sau đó dùng [load-secrets.py](../deploy/load-secrets.py) trên VM để cập nhật
`/etc/memory-harness/runtime.env` và tạo lại container qua quy trình
[release.sh](../deploy/release.sh). `docker restart` đơn thuần không nạp lại
`--env-file`. Xem [deployment.md](deployment.md) để lấy project, VM, zone và
quy trình release; không cần tạo hạ tầng mới để bật Telegram.

## Đăng ký và kiểm tra webhook

Chạy tại repository root, với `TELEGRAM_WEBHOOK_SECRET` đã nạp vào môi trường và
giống giá trị của backend:

```sh
python3 scripts/setup_telegram.py \
  --app-url https://YOUR_BACKEND_HOST \
  --token-file secrets/telegram-token.txt
```

Script kiểm tra health, gọi webhook bằng update không chứa tin nhắn để xác nhận
secret, xác minh token qua `getMe`, đăng ký webhook rồi đọc lại `getWebhookInfo`.
Script giữ các update đang chờ, chỉ đăng ký loại `message` và dùng một kết nối
webhook. Nếu bot đang trỏ đến backend khác, script dừng để giữ cấu hình đó.

Kiểm tra lại mà không thay đăng ký webhook:

```sh
python3 scripts/setup_telegram.py \
  --app-url https://YOUR_BACKEND_HOST \
  --token-file secrets/telegram-token.txt \
  --check-only
```

## Luồng khi `DEMO_PHONE_IDENTITY=false`

- Telegram: mở bot, bấm Start → bấm **Chia sẻ số điện thoại** → chat ngay.
- Client tích hợp riêng: gọi `POST /auth/phone/login` với số để lấy phiên.
  Chat web chính vẫn tạo phiên ẩn danh và nhận số trong tin nhắn qua AI/tool.
- Dùng cùng số điện thoại ở hai kênh để tiếp tục cùng memory. Khách có thể bắt
  đầu từ API web riêng hoặc Telegram; backend tạo hồ sơ nếu chưa có, không cần
  OTP, chọn Hoa/Minh hay quản trị viên tạo identity link.
- Response API riêng có `telegram_url` cho client muốn hiển thị liên kết bot.
  `TELEGRAM_BOT_USERNAME` cấu hình tên bot, mặc định `sea_bank_bot`.
- Khi chưa chia sẻ contact, bot yêu cầu chia sẻ số và chưa xử lý nhu cầu tư vấn.
  Chỉ contact có `contact.user_id` trùng người gửi trong chat riêng được nhận.
  Gõ số điện thoại trong tin nhắn không thay thế nút chia sẻ.
- Số được chuẩn hóa E.164: `0901234567`, `84901234567` và `+84901234567`
  dùng cùng khóa. `POST /auth/phone/login` nhận `{phone}` và trả session/brief
  ngay. `identity_status=identified` là hồ sơ hội thoại, không phải đăng nhập ngân hàng.
- Hợp đồng demo chủ động cho phép ai biết số điện thoại truy cập memory của số đó.
  Không sử dụng dữ liệu nhạy cảm thật hoặc coi đây là xác thực danh tính.
  Các endpoint mã xác nhận cũ vẫn tồn tại để tương thích; chat chính không dùng.
- Khách đi từ website trước được tạo hồ sơ với liên kết Telegram tạm `web:<id>`.
  Khi chủ số chia sẻ contact trên Telegram, bot gắn vào chính hồ sơ đó, giữ
  memory web. Một số đã gắn với Telegram khác không tự chuyển chủ.
- `/start` mở phiên mới và giữ memory; không vượt qua handoff đang chờ nhân viên thật.
  Transcript từng phiên riêng biệt; thông tin đã ghi nhớ dùng chung.

## Liên kết demo cũ

Khi `DEMO_PHONE_IDENTITY=false`, các identity link cũ tới Hoa/Minh không còn quyết định hồ sơ của người chat bot.
Bot tự dùng hồ sơ cá nhân; người dùng chỉ cần chia sẻ số, không cần nhập số demo.
Nếu contact cùng chủ Telegram trùng với phone binding demo cũ, binding chuyển
sang hồ sơ cá nhân khi chia sẻ lại. Không sao chép memory demo dùng chung vào
hồ sơ cá nhân. Khách đã có hồ sơ riêng tiếp tục dùng memory hiện có.

## Chuyên viên tư vấn mô phỏng

Gửi `/start` mở phiên mới và giữ memory đã ghi nhận. Trong lúc chờ nhân viên thật,
`/start` không bỏ qua quy trình bàn giao. Khi bật `AI_SPECIALIST_ENABLED=true`
với backend OpenAI, `/specialist` mở tư vấn chuyên sâu trong cùng phiên,
`/assistant` lưu kết quả và quay lại trợ lý, `/human` chuyển vào hàng chờ người
thật. Nhãn **Chuyên viên tư vấn** là vai trò mô phỏng bằng LLM. Tin nhắn thường
được gửi tới vai trò đang phụ trách. Nếu tiếp nhận lỗi, gửi tin mới để thử lại
hoặc dùng `/assistant` để thoát mà không cần LLM. `/start` khi chuyên viên mô phỏng
đang hỗ trợ sẽ kết thúc phần tư vấn đó và tạo phiên mới.

## Kịch bản kiểm tra API riêng và Telegram hai chiều

1. Telegram `/start`, chia sẻ contact, nhắn `Thu nhập chính xác 47 triệu`.
2. Gọi `/auth/phone/login` bằng cùng số: brief phải dùng 47 triệu, không yêu cầu mã.
3. Gửi `Thu nhập chính xác 53 triệu` qua API message của phiên đó; Telegram
   hỏi lại phải dùng 53 triệu.
4. Với số mới khác, làm ngược lại: mở phiên bằng API web riêng, rồi chia sẻ
   contact cùng số trên Telegram; memory web phải được giữ.
5. Gọi API mở phiên khác với cùng số: memory vẫn còn, transcript phiên mới riêng.

Kiểm thử local bằng DB tạm và Telegram adapter: `uv run pytest -q`.
Kiểm thử API/backend local hai chiều: `node web/tests/phone-demo-smoke.mjs`. Chạy thêm
`DEMO_PHONE_IDENTITY=true node web/tests/phone-demo-smoke.mjs` để kiểm tra cấu hình
deployment demo. Các lệnh này dùng DB tạm, backend rules và Telegram webhook giả lập; không mở
trình duyệt hoặc gửi tới bot thật, không chứng minh AI đã chọn gọi tool. Kiểm tra
luồng AI thực theo [AGENT_CHAT.md](AGENT_CHAT.md).
