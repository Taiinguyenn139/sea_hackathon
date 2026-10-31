# Cấu hình Telegram

## Chat web chính và các API nhận diện demo

Màn hình chat web chính luôn mở khung soạn tin, kể cả khi bật
`DEMO_PHONE_IDENTITY=true`. Lần gửi đầu gọi `POST /sessions` với
`{mode: "harness"}` để mở phiên ẩn danh. Khách gửi số điện thoại của mình trong
chat; AI chọn gọi `verify_identity`, rồi dùng kết quả tool để cập nhật hồ sơ và
call brief. Gửi số là một tin nhắn trong transcript. Không có ô nhập số riêng,
bước chọn hồ sơ hoặc biểu mẫu OTP trên màn hình này.

Mỗi phiên giữ transcript, bản nháp, checkpoint và handoff riêng. Phiên web demo
mới bắt đầu ẩn danh để có thể nhận diện một khách khác; phiên cũ và handoff cũ
vẫn còn trong lịch sử. Các phiên đã kết nối cùng hồ sơ đọc và đóng góp memory
chung theo quyền backend. Một phiên đã gắn hồ sơ không tự đổi sang người khác.

`DEMO_PHONE_IDENTITY=true` vẫn bật cơ chế demo ở backend:

- API legacy `POST /demo/phone/session` nhận `{phone}`, mở phiên đã kết nối với
  transcript trống; số nhập qua API này không tạo tin nhắn. Màn hình chat chính
  không gọi endpoint này.
- Adapter Telegram demo có thể nhận số bằng văn bản và giữ liên kết cho các
  lượt tiếp theo. Số được chuẩn hóa, ví dụ `0901234567` và `+84 90 123 4567`
  là cùng số. Số trong dữ liệu mẫu dùng hồ sơ có sẵn; số mới có hồ sơ demo riêng.
- Web và Telegram đã kết nối cùng hồ sơ dùng chung memory, còn transcript vẫn
  riêng từng phiên. Liên kết contact/OTP đã có không tự gộp memory từ hồ sơ khác.

Đây là nhận diện dành cho demo, không chứng minh quyền sở hữu số hoặc thay thế
đăng nhập khách hàng để dùng CRM. Flag mặc định tắt ngoài cấu hình demo.
Bảng `demo_phone_links` (schema 5) lưu liên kết demo riêng; contact/OTP phía dưới
vẫn là luồng backend riêng và không xuất hiện trong màn hình chat chính.

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

Trước khi chia sẻ số điện thoại, hồ sơ có trạng thái `guest`. Sau khi chia sẻ
số của chính mình, khách có hồ sơ `identified` lưu bền vững với số điện thoại
là khóa liên kết duy nhất. Hồ sơ này không tự nhận CIF hoặc trạng thái đã xác
minh tài khoản ngân hàng. Mã khách nội bộ được giữ nguyên để không mất memory
đã tạo trước khi chia sẻ số.

Schema phiên bản 4 tự thêm bảng hồ sơ, xác nhận số điện thoại và quyền phiên
web; bổ sung `reply_markup` cho outbox. Dữ liệu cũ được giữ nguyên. Quy trình
release backup database trước khi chạy bản mới.
Reset hội thoại qua API quản trị giữ hồ sơ khách và liên kết định danh, đồng
thời xóa các thông tin hội thoại như với khách demo.

## API contact/OTP tiếp tục trên web (luồng tích hợp riêng)

Luồng này áp dụng khi không dùng nhận diện demo; API vẫn được giữ cho client
tích hợp riêng, còn màn hình chat chính không hiển thị biểu mẫu này.

1. Trên Telegram, bấm **Chia sẻ số điện thoại** ở lời chào hoặc gửi `/phone`
   để hiện lại nút. Khi tắt flag demo, bot không tự nhận số từ tin nhắn thường.
2. Backend chỉ nhận contact có `contact.user_id` trùng người gửi trong chat
   riêng. Gửi số bằng văn bản hoặc contact của người khác không liên kết hồ sơ.
3. Số được chuẩn hóa bằng `phonenumbers` về E.164; mặc định vùng Việt Nam khi
   nhập số trong nước. Ví dụ `0901234567` và `+84901234567` là một khóa.
   Cột `customer_profiles.phone_e164` có ràng buộc duy nhất.
4. Client tích hợp gọi API yêu cầu mã với cùng số rồi xác nhận mã nhận qua
   Telegram. Backend mới cấp capability phiên thuộc hồ sơ đó và bản tóm tắt
   nhu cầu đã ghi nhận; client chịu trách nhiệm cung cấp giao diện riêng.
5. Web và Telegram dùng cùng `customer_id`, bộ tư vấn và memory. Thông tin
   xác nhận mới ở một kênh được dùng cho lần tư vấn tiếp theo ở kênh còn lại.
   Transcript riêng từng phiên vẫn tách biệt; ngữ cảnh dùng chung qua memory.

API public `/auth/phone/start` nhận `{phone}` và trả `{challenge_id, expires_in}`.
API `/auth/phone/confirm` nhận `{challenge_id, code}`; chỉ sau khi xác nhận mới
trả capability phiên web và brief. Không thể lấy ngữ cảnh bằng cách chỉ nhập
một số điện thoại. Mã sáu chữ số hết hạn sau 10 phút, dùng một lần, tối đa năm
lần thử; mỗi số được yêu cầu ba mã trong 15 phút, mỗi IP tối đa mười yêu cầu.
Yêu cầu mã mới làm mã cũ mất hiệu lực. Mã đi qua outbox bền vững của Telegram.

Bản demo chưa tích hợp SMS OTP hoặc đăng nhập khách hàng ngân hàng, nên dùng
Telegram để xác nhận quyền truy cập số đã chia sẻ. Chat web demo tạo phiên
ẩn danh rồi nhận số khách gửi trong chat qua tool `verify_identity`.
Luồng nhận diện demo không thay thế mã Telegram của hồ sơ đã liên kết thực. Public API
`/sessions` không cho nhập ID hồ sơ Telegram để chiếm phiên khách khác.
Nếu một số đã liên kết với hồ sơ khác, bot giữ nguyên các hồ sơ và yêu cầu
hỗ trợ; không tự gộp lịch sử của hai người. Chưa có luồng tự đổi số điện thoại.

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
demo đã liên kết cho phép hỏi dữ liệu công khai; gửi số điện thoại khớp hồ sơ
để nhận diện demo trước khi dùng lịch sử ngân hàng hoặc hai tool yêu cầu định danh.

Gửi `/start` lần nữa, sau đó gửi `0900000001` cho hồ sơ `hoa`
(hoặc `0900000002` cho `minh`). Khi bot xác nhận đã nhận diện hồ sơ demo, thử nhắn
`Tôi cần tư vấn thẻ tín dụng, thu nhập 40 triệu/tháng` và kiểm tra phản hồi.
Đăng ký webhook thành công chưa thay thế việc thử luồng này trên Telegram thật.

Tham khảo: [hướng dẫn tạo bot](https://core.telegram.org/bots/tutorial#obtain-your-bot-token)
và [hợp đồng webhook Telegram](https://core.telegram.org/bots/api#setwebhook).
