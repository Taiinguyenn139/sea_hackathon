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

## Khách mới bắt đầu chat

Gửi `/start` trong chat riêng hoặc nhắn thẳng nhu cầu, ví dụ:
`Tôi cần tư vấn thẻ tín dụng, thu nhập 40 triệu/tháng`.

Backend tự tạo hồ sơ khách riêng theo chat Telegram và dùng chung bộ tư vấn,
công cụ ngân hàng, memory và console chuyên viên của web. Khách có thể cập nhật
thông tin tài chính trong hội thoại, ví dụ `Thu nhập chính xác 50 triệu`.
Mỗi chat có bộ nhớ riêng; không tự gán khách mới vào Hoa hoặc Minh.

Gửi `/start` để mở phiên mới vẫn giữ các thông tin đã ghi nhớ. Hồ sơ và memory
được lưu trong SQLite nên tiếp tục dùng được sau khi backend khởi động lại.
Trong lúc chuyên viên tiếp nhận, `/start` không bỏ qua quy trình bàn giao.

Hồ sơ khách mới có trạng thái `guest`, không có CIF và không được đánh dấu đã
xác minh tài khoản ngân hàng. Public API tạo phiên web vẫn chỉ chấp nhận hồ sơ
demo; không thể nhập ID hồ sơ Telegram để truy cập thông tin của người khác.

Schema phiên bản 3 tự thêm bảng `guest_customers` khi backend khởi động, giữ
nguyên dữ liệu hiện có. Quy trình release backup database trước khi chạy bản mới.
Reset hội thoại qua API quản trị giữ hồ sơ khách và liên kết định danh, đồng
thời xóa các thông tin hội thoại như với khách demo.

## Liên kết hồ sơ ngân hàng demo (tùy chọn)

Chỉ dùng khi cần cho một chat đại diện hồ sơ demo đã có. Quản trị viên xác nhận
chat của người dùng và lấy `message.chat.id` từ update nhận được trên VM;
không công khai nội dung update hoặc credential. Liên kết này thay hồ sơ khách
độc lập bằng hồ sơ demo cho các phiên tiếp theo; không tự chuyển memory cũ
sang hồ sơ ngân hàng.

Với `ADMIN_TOKEN` đã nạp vào môi trường, liên kết chat đã xác nhận:

```sh
python3 scripts/setup_telegram.py \
  --app-url https://YOUR_BACKEND_HOST \
  --token-file secrets/telegram-token.txt \
  --chat-id YOUR_PRIVATE_CHAT_ID \
  --profile hoa
```

Lệnh này gọi endpoint quản trị `/admin/identity-links/telegram`. Hồ sơ ngân hàng
demo đã liên kết vẫn yêu cầu xác minh ngày sinh trước khi tư vấn.

Gửi `/start` lần nữa, sau đó ngày sinh demo `1988-05-12` cho hồ sơ `hoa`
(hoặc `1992-08-20` cho `minh`). Khi bot xác nhận đã xác minh, thử nhắn
`Tôi cần tư vấn thẻ tín dụng, thu nhập 40 triệu/tháng` và kiểm tra phản hồi.
Đăng ký webhook thành công chưa thay thế việc thử luồng này trên Telegram thật.

Tham khảo: [hướng dẫn tạo bot](https://core.telegram.org/bots/tutorial#obtain-your-bot-token)
và [hợp đồng webhook Telegram](https://core.telegram.org/bots/api#setwebhook).
