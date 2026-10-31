# Quy ước làm việc của team

Bản đề xuất để team thống nhất trước khi bắt đầu cuộc thi. Mục tiêu là tích hợp
nhanh, hạn chế đụng code và luôn giữ một phiên bản chạy được để demo.

## 1. Chia việc và phối hợp

- Mỗi task có một người phụ trách, đầu ra rõ ràng và tiêu chí hoàn thành.
- Đưa task lên bảng chung với trạng thái: Todo → Doing → Review → Done.
- Trước khi làm, nhận task và báo phạm vi file/module dự kiến sửa. Nếu hai người
  cùng sửa một khu vực, thống nhất cách chia trước.
- Thống nhất API, schema và dữ liệu mẫu giữa frontend/backend trước khi triển khai.
  Báo ngay khi cần đổi giao diện dùng chung.
- Cập nhật ngắn khi bắt đầu, khi bị kẹt và khi mở PR. Báo blocker sớm, không đợi
  đến cuối buổi mới nói.
- Chỉ định một người điều phối tích hợp và một người dự phòng. Người điều phối
  ưu tiên task, xử lý phụ thuộc và quyết định phạm vi cho bản demo.

## 2. Branch và commit

- `main` luôn là phiên bản chạy được; không push trực tiếp vào `main`.
- Mỗi task dùng một branch ngắn hạn từ `main`: `feat/<ten-task>`,
  `fix/<ten-loi>`, `docs/<noi-dung>` hoặc `chore/<noi-dung>`.
- Một PR giải quyết một mục tiêu. Tránh gộp refactor không liên quan vào tính năng.
- Commit rõ mục đích, ví dụ: `feat: thêm màn hình đăng nhập` hoặc
  `fix: xử lý danh sách rỗng`.
- Không commit secret, `.env` thật, dữ liệu cá nhân, dependencies hay file build.
  Cung cấp `.env.example` với giá trị giả khi cần.

## 3. Pull, push và đồng bộ

Trước khi nhận task, bảo đảm working tree sạch rồi tạo branch:

```sh
git switch main
git pull --ff-only origin main
git switch -c feat/ten-task
```

Trong lúc làm, commit theo từng phần có ý nghĩa và push branch để team có thể
xem tiến độ hoặc tiếp quản. Commit chưa hoàn thiện cần ghi rõ trong draft PR;
chưa đánh dấu sẵn sàng merge.

```sh
git push -u origin feat/ten-task
```

Trước khi chuyển PR sang review, commit hoặc stash thay đổi đang làm rồi đồng bộ
`main` vào branch của mình:

```sh
git fetch origin
git merge origin/main
# Giải quyết conflict nếu có, sau đó chạy lại các kiểm tra liên quan.
git push
```

- Dùng merge để đồng bộ branch đã chia sẻ, tránh viết lại lịch sử của người khác.
- Không dùng `git push --force` trên branch chung hoặc `main`.
- Nếu chủ động rebase branch cá nhân đã push, chỉ dùng `--force-with-lease` và
  bảo đảm không có người khác đang làm trên branch đó.
- Khi conflict liên quan logic của người khác, trao đổi với tác giả; không chọn
  toàn bộ “ours/theirs” chỉ để hết conflict.

## 4. Pull request và review

PR cần có mô tả ngắn theo mẫu:

```text
Mục tiêu / task:
Thay đổi chính:
Cách kiểm tra và kết quả:
Ảnh/video nếu thay đổi giao diện:
Hạn chế hoặc phần còn thiếu:
```

- Tác giả tự đọc diff, kiểm tra file thừa và chạy các kiểm tra trước khi nhờ review.
- Cần ít nhất một thành viên khác approve; tác giả không tự approve.
- Reviewer ưu tiên tính đúng, khả năng chạy demo, lỗi tích hợp và dữ liệu nhạy cảm.
  Góp ý thẩm mỹ không cấp thiết có thể tạo task sau.
- Chỉ merge khi đã xử lý góp ý chặn, hết conflict và các kiểm tra bắt buộc đã đạt.
  Nếu sửa logic sau khi được approve, cần review lại phần thay đổi.
- Dùng squash merge để mỗi PR thành một commit rõ nghĩa trên `main`; xóa branch
  sau khi merge nếu không còn ai sử dụng.

## 5. Test và điều kiện hoàn thành

Repo hiện chưa có ứng dụng hoặc lệnh kiểm tra. Người khởi tạo stack chịu trách nhiệm
bổ sung các lệnh chính xác vào README và thiết lập CI phù hợp. Không ghi “test pass”
nếu chưa chạy; nêu rõ kiểm tra nào chưa có hoặc chưa thực hiện.

Trước mỗi lần merge:

- Chạy lint, kiểm tra kiểu dữ liệu (nếu stack có), test tự động hiện có và build.
- Kiểm tra thủ công luồng vừa sửa với trường hợp thành công, dữ liệu rỗng/không hợp
  lệ và lỗi API nếu có liên quan.
- Với bug fix, thêm regression test khi khả thi. Với logic nghiệp vụ quan trọng,
  có test cho đường đi chính và trường hợp lỗi đáng kể.
- Với UI, kiểm tra kích thước màn hình dùng để demo và trạng thái loading/error.
- Nếu thay đổi cấu hình, dependency hoặc database, ghi cách cập nhật môi trường,
  migration và dữ liệu mẫu để người khác chạy lại được.

Task chỉ chuyển sang Done khi PR đã merge, tài liệu cần thiết đã cập nhật và luồng
liên quan hoạt động trên môi trường tích hợp. Sau khi merge, tác giả kiểm tra lại
luồng đó; build thành công chưa đủ để kết luận tính năng hoạt động.

## 6. Giữ bản demo ổn định

- Thống nhất giờ chốt tính năng theo hạn nộp; đề xuất dừng thêm tính năng mới
  trước demo 1–2 giờ để tập trung tích hợp và diễn tập.
- Trước giờ chốt, chạy thử toàn bộ kịch bản demo với dữ liệu mẫu và tài khoản cần
  thiết. Ghi rõ người trình bày, người thao tác và phương án dự phòng.
- Đánh dấu commit đã kiểm tra bằng tag, ví dụ `demo-ready-1`, để có mốc khôi phục.
- Sau giờ chốt, chỉ nhận sửa lỗi chặn demo có review và kiểm tra luồng bị ảnh hưởng.
- Nếu merge làm hỏng bản tích hợp, ưu tiên revert PR gây lỗi, khôi phục bản chạy
  được rồi sửa trên branch riêng. Không reset hoặc force-push lịch sử `main`.

## 7. Thiết lập trên dịch vụ Git

Người quản lý repo cấu hình khi bắt đầu làm việc chung:

- Bảo vệ `main`: yêu cầu PR và ít nhất một approve, chặn force-push và xóa branch.
- Khi đã có CI, yêu cầu các job kiểm tra phù hợp thành công trước khi merge.
- Chọn squash merge và tự động xóa branch đã merge nếu nền tảng hỗ trợ.

Các thiết lập trên là việc cần thực hiện trên dịch vụ Git; tài liệu này chưa tự
áp dụng chúng.
