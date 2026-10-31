# Hướng dẫn cho AI agent

Áp dụng khi agent làm việc trong repository này. Đọc [README.md](README.md) và
[CONTRIBUTING.md](CONTRIBUTING.md) trước khi triển khai; tuân thủ quy ước làm việc
chung của team.

## Mục tiêu

- Ưu tiên hoàn thành phạm vi được giao và giữ luồng demo hoạt động ổn định.
- Thực hiện thay đổi nhỏ, dễ review; tránh refactor hoặc thêm dependency ngoài
  phạm vi khi chưa có lý do cần thiết.
- Giao tiếp bằng tiếng Việt, giữ nguyên tên API, biến và thuật ngữ trong code
  khi cần để bảo đảm chính xác.

## Trước khi sửa code

- Đọc yêu cầu, tài liệu và code liên quan; xác định tiêu chí hoàn thành.
- Kiểm tra branch và `git status` để nhận biết thay đổi đang có của thành viên khác.
- Không ghi đè, xóa, revert hoặc stash thay đổi của người khác.
- Nêu ngắn gọn phạm vi sẽ sửa. Nếu yêu cầu chưa rõ, tiếp tục phần độc lập đã rõ;
  hỏi lại khi quyết định còn thiếu có thể làm sai chức năng hoặc phá hợp đồng API.
- Bám theo cấu trúc, convention và công cụ hiện có. Không tự giả định stack hay
  lệnh chạy khi repository chưa định nghĩa.

## Git, pull và push

- Làm trên branch theo task: `feat/<ten-task>`, `fix/<ten-loi>`,
  `docs/<noi-dung>` hoặc `chore/<noi-dung>`. Không push trực tiếp vào `main`.
- Nếu đã ở branch/worktree được giao, tiếp tục tại đó; không tự chuyển branch
  hoặc tạo worktree khác làm gián đoạn công việc hiện tại.
- Khi cần tạo branch mới, cập nhật `main` bằng `git pull --ff-only origin main`
  trên working tree sạch, rồi tạo branch từ đó.
- Trước review, đồng bộ bằng `git fetch origin` và `git merge origin/main`
  khi trạng thái làm việc cho phép; chạy lại kiểm tra sau khi xử lý conflict.
- Không tự chọn toàn bộ “ours/theirs” khi conflict ảnh hưởng logic chưa hiểu rõ.
- Không dùng `reset --hard`, xóa branch, viết lại lịch sử hoặc force-push để
  dọn trạng thái. Rebase branch cá nhân đã chia sẻ phải tuân thủ CONTRIBUTING.md.
- Commit có mục đích rõ ràng, theo dạng `feat: ...`, `fix: ...`, `docs: ...`
  hoặc `chore: ...`.
- Chỉ commit, push, mở PR hoặc merge khi thuộc phạm vi người dùng đã giao.
  Không tự coi yêu cầu sửa file là yêu cầu xuất bản thay đổi.

## Quy tắc triển khai

- Giữ hợp đồng API/schema đã thống nhất; báo rõ thay đổi có ảnh hưởng frontend,
  backend, dữ liệu hoặc môi trường chạy.
- Xử lý các trạng thái thành công, rỗng, loading và lỗi khi có liên quan.
- Không đưa secret, `.env` thật, token, dữ liệu cá nhân hoặc log nhạy cảm vào repo.
  Dùng `.env.example` với giá trị giả để mô tả biến môi trường.
- Nếu thêm dependency hoặc migration, giải thích lý do và cập nhật hướng dẫn
  cài đặt/chạy tương ứng. Dùng package manager và lockfile hiện có.
- Sau giờ chốt tính năng của team, chỉ xử lý lỗi chặn demo theo quy trình review.

## Kiểm tra trước khi bàn giao

- Đọc lại diff để loại bỏ thay đổi ngoài phạm vi, file sinh tự động không cần thiết
  và thông tin nhạy cảm; chạy `git diff --check`.
- Dùng lệnh chính thức trong README hoặc cấu hình dự án để chạy lint, kiểm tra
  kiểu dữ liệu, test và build phù hợp với thay đổi.
- Thêm regression test cho bug fix khi khả thi; kiểm tra logic nghiệp vụ quan trọng
  với đường đi chính và trường hợp lỗi đáng kể.
- Kiểm tra thủ công luồng bị ảnh hưởng nếu có môi trường chạy; với UI, kiểm tra
  kích thước màn hình dùng để demo.
- Nếu chưa có công cụ test hoặc không thể chạy kiểm tra, ghi rõ giới hạn và lý do.
  Không báo “pass” cho kiểm tra chưa thực hiện.
- Với chỉnh sửa tài liệu đơn thuần, kiểm tra nội dung, liên kết và diff; không cần
  thêm test ứng dụng chỉ để kiểm tra văn bản.

## Review và bàn giao

- Báo ngắn gọn: đã thay đổi gì, lý do, kiểm tra đã chạy và vấn đề còn lại.
- Khi được giao mở PR, dùng mẫu trong CONTRIBUTING.md; bổ sung ảnh/video nếu
  thay đổi giao diện và có thể thu thập được.
- PR cần ít nhất một thành viên khác approve và các kiểm tra bắt buộc đạt trước
  khi squash merge. Agent không tự thay thế review của thành viên team.
- Phân biệt rõ “đã sửa local”, “đã mở PR” và “đã merge”; không báo task Done nếu
  chưa đạt điều kiện hoàn thành trong CONTRIBUTING.md.
- Nếu bản tích hợp bị hỏng, ưu tiên đề xuất revert PR gây lỗi để khôi phục demo,
  sau đó sửa trên branch riêng.

## CI/CD và deploy lên GCP

Deploy dự án qua pipeline GitHub Actions. Workflow được lưu trong
`.github/workflows/` và là cách triển khai chung của team.

### Luồng pipeline

- **Pull request vào `main`:** cài dependencies theo lockfile, chạy lint, kiểm tra
  kiểu dữ liệu, test và build theo stack thực tế. PR không deploy lên môi trường
  demo và không được cấp quyền deploy GCP.
- **Push vào `main` sau khi merge PR:** chạy các kiểm tra bắt buộc, build artifact,
  deploy lên môi trường demo GCP rồi chạy smoke test cho luồng chính.
- Chỉ deploy khi các bước kiểm tra và build thành công. Cấu hình concurrency để
  mỗi môi trường chỉ có một job deploy tại một thời điểm; tránh bản cũ ghi đè
  bản mới khi có nhiều commit liên tiếp.
- Gắn artifact/revision với commit SHA, ghi URL và kết quả smoke test trong kết
  quả workflow để team truy vết được bản đang chạy.
- Khi deploy lỗi, dừng pipeline và báo rõ bước lỗi. Lưu mốc bản thành công trước
  đó và cung cấp cách chạy lại/rollback qua pipeline theo dịch vụ đã chọn.
  Migration database cần phương án khôi phục riêng.

### Tài khoản và xác thực

- `nhithanhtai2010@gmail.com` là tài khoản dùng để thiết lập GCP ban đầu: project,
  billing, API, IAM và kết nối GitHub Actions khi có quyền phù hợp.
- Pipeline dùng deployment service account qua GitHub OIDC và Workload Identity
  Federation; không đăng nhập Gmail trong CI và không lưu khóa JSON dài hạn.
- Giới hạn trust của federation vào đúng repository và nhánh/môi trường deploy;
  chỉ job deploy có quyền `id-token: write`, các job khác dùng quyền tối thiểu.
- Deployment service account và runtime service account có quyền phù hợp với
  nhiệm vụ riêng; không cấp Owner chỉ để vượt qua lỗi quyền.
- Lưu project ID, region, tên dịch vụ, workload identity provider và deployment
  service account trong GitHub Actions variables/environment configuration.
  Lưu secret ứng dụng trong Secret Manager và không in giá trị ra log.

### Yêu cầu khi triển khai pipeline

- Xác định stack, lệnh kiểm tra/build, project ID, region và dịch vụ GCP từ cấu
  hình team đã chọn. Không tự chọn project hoặc mặc định sử dụng Cloud Run.
- Kiểm tra billing, API, IAM và chính sách truy cập trước lần deploy đầu tiên.
- Dùng một môi trường demo cho cuộc thi trước; chỉ thêm staging/production khi
  có nhu cầu được thống nhất.
- README phải ghi cách cấu hình variables, kết nối GCP, kích hoạt workflow,
  xem log, kiểm tra URL và rollback. Không commit secret hoặc credential.
- Cấu hình bảo vệ `main` yêu cầu các CI checks tương ứng thành công trước merge.
- Hiện repo chưa có code ứng dụng, stack hoặc cấu hình GCP đích. Phần này là yêu
  cầu cho pipeline khi khởi tạo ứng dụng; chưa có workflow CI/CD hoạt động.
  Không tạo workflow placeholder luôn xanh hoặc báo deploy thành công khi chưa
  có ứng dụng được kiểm tra thực tế.

Tài liệu chính thức:
[Workload Identity Federation cho deployment pipelines](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines).

## Khi khởi tạo ứng dụng

Nếu repo chưa có code, người/agent được giao khởi tạo phải bổ sung README với:
yêu cầu môi trường, cách cài đặt, biến môi trường mẫu, lệnh chạy, lint, kiểm tra
kiểu dữ liệu, test và build thực tế. Thiết lập CI theo stack đã chọn khi nằm trong
phạm vi task; không mô tả CI hoặc branch protection là đã bật khi chưa xác minh.
