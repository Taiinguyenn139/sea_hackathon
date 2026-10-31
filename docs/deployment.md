# Triển khai GCP

Ứng dụng hiện dùng SQLite và checkpoint trên đĩa. Chọn một VM có Persistent Disk để giữ dữ liệu qua các lần thay container; không đặt database trên filesystem tạm của Cloud Run. Đây là cấu hình demo một máy, chấp nhận gián đoạn ngắn khi cập nhật và chưa có HA. Backup cùng đĩa chỉ phục vụ rollback, không bảo vệ trước mất toàn bộ đĩa.

## Điểm vào

| Công việc | Nguồn sở hữu |
|---|---|
| Build ứng dụng | [Dockerfile](../Dockerfile) |
| Kiểm tra PR, deploy khi main thay đổi | [GitHub workflow](../.github/workflows/deploy-gcp.yml) |
| Tạo hạ tầng và quyền OIDC/IAP | [provision-gcp.sh](../deploy/provision-gcp.sh) |
| Mount đĩa, cài Docker và thứ tự khởi động | [startup.sh](../deploy/startup.sh) |
| Cấu hình hostname/HTTPS | [configure-proxy.sh](../deploy/configure-proxy.sh) |
| Backup, kiểm tra và chuyển phiên bản | [release.sh](../deploy/release.sh) |
| Gắn biến GitHub với project đã chọn | [configure-github.sh](../deploy/configure-github.sh) |
| Kiểm chứng container bằng dữ liệu tạm | [test-container.py](../deploy/test-container.py) |

## Khởi tạo lần đầu

Chủ dự án cần chọn chính xác project trước khi chạy provisioning. Quy ước team chọn GCP Secret Manager để lưu credential. Tài khoản đang active trong gcloud không thay thế quyết định project. Các script nhận project rõ ràng và yêu cầu `GCP_ACCOUNT`; không dùng project mặc định của máy.

1. Chạy script provisioning từ repository root với project đã chọn. Script tạo tài nguyên tính phí; VM, disk, IP và image vẫn có thể phát sinh phí khi không có người dùng.
2. Chờ startup hoàn tất. Từ SSH qua IAP, kiểm tra `systemctl status google-startup-scripts.service` và `mountpoint /var/lib/memory-harness`.
3. Chạy [initialize-secrets.sh](../deploy/initialize-secrets.sh) một lần để tạo credential trực tiếp trong Secret Manager. Copy [load-secrets.py](../deploy/load-secrets.py) lên VM qua IAP rồi chạy bằng root để cấp file cấu hình runtime, mode `600`. Không đặt file này vào Git, build context hoặc log. Hợp đồng tên biến nằm ở [config.py](../harness/config.py); production bắt buộc `ADMIN_TOKEN` và `STAFF_PASSWORD`. Giá trị secret chỉ được truyền qua stdin/file được bảo vệ. Telegram cần credential bot thật để hoạt động; không có credential thì chỉ kiểm chứng được web và adapter bằng test.
4. Chọn hostname trỏ đến IP tĩnh của VM; chạy script proxy trên VM. Khi chưa có domain riêng, hostname demo theo IP qua sslip.io là một lựa chọn phụ thuộc DNS bên thứ ba. Cổng 80/443 phải tới được VM để cấp TLS.
5. Khi bootstrap, credential và proxy đã sẵn sàng, chạy script cấu hình GitHub với project và URL HTTPS. Script này bật `GCP_DEPLOY_ENABLED` ở cấp repository. Chạy workflow trên `main` để deploy lần đầu và kiểm tra kết quả.

Giữ `GCP_DEPLOY_ENABLED` unset hoặc `false` trong lúc setup: CI vẫn chạy, job deploy bỏ qua. Sau khi bật, mỗi push/merge vào `main` qua kiểm tra sẽ tự deploy; không cần người duyệt từng lần. Demo environment có thể có quy tắc riêng nếu chủ repo thêm sau này.

## Tra cứu và xử lý sự cố

Project, region, zone, instance, URL và OIDC provider lấy từ **GitHub → Settings → Environments → demo → Variables**; không lưu bản sao trạng thái cloud trong tài liệu công khai. Tài khoản Google để vận hành do chủ dự án cung cấp. Nếu biến chưa tồn tại, setup chưa hoàn tất; không suy đoán project từ cấu hình local.

- **CI/build/deploy:** GitHub → Actions → “Test and deploy GCP”, chọn đúng commit. `gh run list --workflow deploy-gcp.yml --limit 5` tìm các run gần nhất; dùng run ID để xem job lỗi. Quyền đọc repo đủ để xem run công khai; thay biến yêu cầu quyền quản trị repo.
- **Runtime:** dùng `gcloud compute ssh INSTANCE --project PROJECT --account ACCOUNT --zone ZONE --tunnel-through-iap`, thay các giá trị từ nguồn trên. Trên VM, `sudo docker logs --since 10m --tail 100 memory-harness-app` đọc log app có giới hạn; thay tên bằng `memory-harness-proxy` để xem lỗi TLS/proxy. Không xuất log chứa dữ liệu khách hàng hoặc credential ra issue công khai.
- **Xác nhận phiên bản:** gọi `APP_URL/healthz` bằng HTTPS và đối chiếu `revision` với commit của run. Có container hoặc image trong registry chưa đủ để xác nhận deploy thành công.
- **Rollback:** script release tự phục hồi image/data cũ nếu candidate thất bại trước khi mở traffic. Sau khi đã mở traffic, không tự khôi phục snapshot cũ vì có thể làm mất lượt mới. Khi cần quay về phiên bản đã biết, chọn digest và commit từ run đã thành công rồi chạy cùng script release qua IAP; script giữ backup dữ liệu hiện tại trước khi thử. Phục hồi snapshot thủ công cần xác định rõ mốc dữ liệu và khả năng mất lượt phát sinh sau mốc đó.
- **Backup và đĩa:** dữ liệu nằm ở `/var/lib/memory-harness/data`, snapshot phục vụ rollback ở `/var/lib/memory-harness/backups`. Kiểm tra dung lượng bằng `df -h /var/lib/memory-harness`; không xóa snapshot đang được dùng để phục hồi. Chưa cấu hình lịch backup ngoài máy hoặc chính sách tự xóa image/snapshot; chủ vận hành cần quyết định retention trước khi dùng lâu dài.

GitHub dùng OIDC để lấy credential ngắn hạn. VM được đọc Artifact Registry và secret `memory-harness-runtime`, còn service account deploy có quyền SSH quản trị VM của ứng dụng. Credential được quản lý tại Secret Manager trong đúng project; chỉ tài khoản được cấp quyền đọc secret mới truy xuất được. Không tạo hoặc lưu service-account JSON key. Tham khảo hợp đồng chính thức về [Workload Identity Federation](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines), [quyền Secret Manager](https://docs.cloud.google.com/secret-manager/docs/access-control) và [filesystem Cloud Run](https://docs.cloud.google.com/run/docs/container-contract).

## OpenAI chính và backup

Backend OpenAI dùng `OPENAI_MODEL=gpt-6-luna`, `OPENAI_BACKUP_MODEL=gpt-6.1-sol` và `OPENAI_API_KEY` phía server. Cấu hình `LLM_BACKEND=openai` yêu cầu đủ cả ba, hai model phải khác nhau; thiếu cấu hình làm startup thất bại rõ ràng. `rules` vẫn là chế độ chủ động chạy không credential.

Mỗi bước phân loại (`plan`), soạn trả lời (`draft`) và trích xuất memory (`extract`) gọi primary trước. Primary thành công không gọi backup. Timeout, lỗi mạng, lỗi model, 5xx, rate limit tạm thời hoặc JSON/schema không hợp lệ có thể gọi backup đúng một lần. Lỗi xác thực hoặc quota/billing chung tài khoản kết thúc ngay vì đổi model cùng key không giải quyết được. Tôn trọng Retry-After trong ngân sách; không retry vòng lặp.

`LLM_CALL_BUDGET_SECONDS` mặc định 20 giây, tối đa 120, áp dụng cho cả hai attempts của một bước. Primary được dùng nửa thời gian còn lại để dành phần cho backup. Deadline hủy async I/O đang chạy. Một lượt chat có thể có nhiều bước nên latency toàn lượt cần đo riêng. Cả hai model cùng OpenAI không bảo vệ khỏi outage hoặc quota chung.

Khi hết hai model, khách nhận đúng câu: “Hiện tại hệ thống tư vấn đang tạm gián đoạn. Anh/chị vui lòng thử lại sau ít phút.” Không tạo tư vấn bằng rules do lỗi API, không gọi lại bank tools và không đưa chi tiết lỗi nội bộ cho khách. Guard số liệu, nguồn span, phép tính ngân hàng và template bảo vệ vẫn tất định.

Memory gọi provider ngoài transaction SQLite. Job được claim bằng lease rồi kiểm tra lại trước commit; request gặp claim đang chạy sẽ chờ ngoài DB lock, tối đa ngân sách gọi, để bàn giao hoặc lượt tiếp theo đọc được memory vừa cập nhật. Khi provider lỗi, giữ payload/thứ tự để thử lại và cooldown 30 giây để tránh gọi liên tục. Candidate startup recovery dùng parser tất định, không gọi primary/backup trước promotion.

Để bật trên VM hiện tại:

1. Phát hành image hỗ trợ backup với backend rules hiện có, xác nhận revision.
2. Tạo version mới của secret tổng hợp `memory-harness-runtime`, giữ nguyên tất cả entry hiện hữu; thêm key, hai model và `LLM_CALL_BUDGET_SECONDS=20`, đổi `LLM_BACKEND=openai`. Mỗi biến chỉ có một dòng, không thêm dấu nháy. Giá trị chỉ qua Console hoặc stdin/file bảo vệ, không đưa vào lệnh shell, Git hay chat.
3. Chạy loader trên VM bằng root để thay `/etc/memory-harness/runtime.env`, sau đó phát hành lại cùng image bằng `release.sh`. Secret mới không tự thay environment của container đang chạy. Release gate, backup SQLite/checkpoint và kiểm tra revision vẫn áp dụng.
4. Kiểm tra health/revision và một phiên tổng hợp tối thiểu. Trong Docker logs, tìm `llm_completion` với `purpose`, `model`, `attempt=primary|backup`, `status=success|error|failover|exhausted` và duration. Logs chỉ ghi metadata/usage, không prompt, câu trả lời hay credential. `/healthz` chỉ báo config/liveness, không chứng minh completion thành công.

Thử lỗi cưỡng bức và eval chỉ chạy trên server localhost có DB tạm; runner eval reset dữ liệu. Không inject lỗi hoặc reset production để kiểm tra backup. Trước smoke ghi dữ liệu production, giữ backup nhất quán theo quy trình release.

Rollback cấu hình bằng cách xuất bản lại payload đã biết tốt thành version mới nhất rồi chạy loader/release image tương thích. Loader luôn đọc latest, không tự quay về version cũ. Giữ version secret và digest/revision trước đó để phục hồi; không khôi phục snapshot DB cũ sau khi mở traffic nếu sẽ làm mất lượt mới.
