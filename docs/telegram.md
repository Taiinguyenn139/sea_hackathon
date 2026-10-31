# Cấu hình Telegram

Adapter Telegram đã có trong backend. Để chạy bot thật cần token từ BotFather,
webhook secret giống nhau ở backend và Telegram và URL HTTPS public.
Khách mới tự có hồ sơ riêng và có thể chat ngay, không cần quản trị viên liên kết.
Chỉ hỗ trợ tin nhắn văn bản trong chat riêng.

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

## Luồng demo không cần setup khách hàng

- Telegram: mở bot, bấm Start → bấm **Chia sẻ số điện thoại** → chat ngay.
- Website: **Tiếp tục bằng số điện thoại** → nhập số → **Bắt đầu trò chuyện**.
- Dùng cùng số điện thoại ở hai kênh để tiếp tục cùng memory. Khách có thể bắt
  đầu ở web hoặc Telegram; backend tự tạo hồ sơ nếu chưa có. Không cần OTP,
  chọn Hoa/Minh, nhập số demo trong chat hoặc admin tạo identity link.
- Website có liên kết **Tiếp tục trên Telegram** sau khi mở hồ sơ.
  `TELEGRAM_BOT_USERNAME` cấu hình tên bot cho liên kết này, mặc định `sea_bank_bot`.
- Khi chưa chia sẻ contact, bot yêu cầu chia sẻ số và chưa xử lý nhu cầu tư vấn.
  Chỉ contact có `contact.user_id` trùng người gửi trong chat riêng được nhận.
  Gõ số điện thoại trong tin nhắn không thay thế nút chia sẻ.
- Số được chuẩn hóa E.164: `0901234567`, `84901234567` và `+84901234567`
  dùng cùng khóa. `POST /auth/phone/login` nhận `{phone}` và trả session/brief
  ngay. `identity_status=identified` là hồ sơ hội thoại, không phải đăng nhập ngân hàng.
- Hợp đồng demo chủ động cho phép ai biết số điện thoại truy cập memory của số đó.
  Không sử dụng dữ liệu nhạy cảm thật hoặc coi đây là xác thực danh tính.
  Các endpoint mã xác nhận cũ vẫn tồn tại để tương thích nhưng UI demo không dùng.
- Khách đi từ website trước được tạo hồ sơ với liên kết Telegram tạm `web:<id>`.
  Khi chủ số chia sẻ contact trên Telegram, bot gắn vào chính hồ sơ đó, giữ
  memory web. Một số đã gắn với Telegram khác không tự chuyển chủ.
- `/start` mở phiên mới và giữ memory; không vượt qua handoff đang chờ chuyên viên.
  Transcript từng phiên riêng biệt; thông tin đã ghi nhớ dùng chung.

## Liên kết demo cũ

Các identity link cũ tới Hoa/Minh không còn quyết định hồ sơ của người chat bot.
Bot tự dùng hồ sơ cá nhân; người dùng chỉ cần chia sẻ số, không cần nhập số demo.
Nếu contact cùng chủ Telegram trùng với phone binding demo cũ, binding chuyển
sang hồ sơ cá nhân khi chia sẻ lại. Không sao chép memory demo dùng chung vào
hồ sơ cá nhân. Khách đã có hồ sơ riêng tiếp tục dùng memory hiện có.

## Kịch bản kiểm tra hai chiều

1. Telegram `/start`, chia sẻ contact, nhắn `Thu nhập chính xác 47 triệu`.
2. Web nhập cùng số, hỏi thu nhập: phải dùng 47 triệu, không yêu cầu mã.
3. Web nhắn `Thu nhập chính xác 53 triệu`; Telegram hỏi lại: phải dùng 53 triệu.
4. Với số mới khác, làm ngược lại: bắt đầu chat trên web, sau đó chia sẻ contact
   cùng số trên Telegram; memory web phải được giữ.
5. Reload web hoặc restart backend, nhập cùng số: memory vẫn còn.

Kiểm thử local bằng DB tạm và Telegram adapter: `uv run pytest -q`.
Kiểm thử browser/backend local hai chiều: `node web/tests/phone-demo-smoke.mjs`
sau khi `npm --prefix web run build`. Chưa gửi tới Telegram thật trong lệnh này.
