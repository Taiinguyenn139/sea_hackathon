# GCP deployment readiness — 2026-10-31

## Đã kiểm chứng tại máy phát triển

- Image Docker production build thành công, gồm FastAPI và frontend đã bundle.
- Smoke test qua HTTP đạt: health/revision, chặn POST trước promotion, báo giá có nguồn, SQLite giữ dữ liệu qua restart và trả frontend HTML.
- Backend/eval: 59 tests đạt; Ruff và Mypy đạt. Frontend: 5 unit tests và TypeScript check đạt.
- Không còn dev server hoặc container smoke test của phiên này sau kiểm tra.
- CI có verify trước deploy, dùng digest bất biến và OIDC; các script shell đã được kiểm tra cú pháp.
- [CI trên main](https://github.com/Taiinguyenn139/sea_hackathon/actions/runs/41167046271) tại commit `b8029246c74cf4dacdaa343bf5ab050281aa83e0`: verify thành công, gồm Docker build và container smoke; deploy skipped đúng điều kiện chưa bật.
- [PR #2](https://github.com/Taiinguyenn139/sea_hackathon/pull/2) đã được một phiên khác merge. Phiên deploy không tự merge PR. Branch local hiện là `chore/gcp-deployment`, fast-forward đến commit main nêu trên.
- Bảo vệ main đã bật: 1 approve, dismiss stale reviews, check `verify` bắt buộc và strict, enforce admins, cấm force-push/xóa branch, yêu cầu giải quyết conversation. Environment `demo` chỉ cho branch `main`; các biến GCP và URL đã được cấu hình sau khi người dùng chọn project.

## Giới hạn và việc còn lại

- Chủ dự án đã chọn My First Project và xác nhận tài khoản đang đăng nhập. Provisioning hoàn tất: VM, Persistent Disk, mạng/firewall, Artifact Registry, IAM/OIDC và Secret Manager.
- Credential được tạo trực tiếp trong Secret Manager, materialize bằng identity của VM; không in giá trị ra log hoặc đưa vào Git.
- Hostname HTTPS tạm theo IP qua sslip.io đã hoạt động. URL và định danh cloud lấy từ GitHub environment `demo`.
- Workflow đã có trên main, `GCP_DEPLOY_ENABLED=true`. [Lần deploy đầu tiên](https://github.com/Taiinguyenn139/sea_hackathon/actions/runs/41167699165) có cả verify và deploy thành công; HTTPS trả revision `b8029246c74cf4dacdaa343bf5ab050281aa83e0`.
- Telegram thật và LLM thật cần credential riêng; các test hiện dùng dữ liệu giả và backend rules.

Hướng dẫn vận hành: [docs/deployment.md](../../docs/deployment.md).

## Kiểm chứng sau triển khai

- HTTPS health/revision và frontend HTML: đạt.
- Tạo phiên khách hàng, xác minh, nhận báo giá có nguồn và kết thúc phiên qua public API: đạt.
- Staff chưa đăng nhập bị từ chối; đăng nhập bằng credential từ Secret Manager, cookie Secure/HttpOnly, benchmark API và logout: đạt. Không lưu credential vào file test hoặc output.
- Docker chạy app healthy, proxy đang chạy; Persistent Disk mounted, marker promotion và mốc backup tồn tại.
- Lần SSH IAP đầu tiên gặp lỗi lookup khi VM vừa tạo; lần thử sau thành công, không mở SSH ra Internet.
- Chưa thực hiện diễn tập rollback có lỗi trên VM thật; script đã được review và kiểm tra cú pháp. Backup hiện nằm cùng persistent disk, chưa có bản sao ngoài máy.
- Telegram thật vẫn cần bot token. Backend hiện là rules; LLM thật và held-out chưa được kiểm chứng.
