# Kiểm chứng chuyên viên tư vấn

Đã triển khai local luồng chuyên viên dùng LLM tiếp nhận hồ sơ và tư vấn nhiều lượt trên Web/Telegram. Nhãn demo là **Chuyên viên tư vấn**. Chưa commit hoặc phát hành lên môi trường deploy.

## Kết quả

| Kiểm tra | Kết quả |
| --- | --- |
| `uv run pytest -q` | 397 tests và 120 subtests đạt |
| `uv run pytest -q tests/test_specialist.py` | 12 tests chuyên viên đạt, nằm trong suite đầy đủ |
| Ruff cho harness, bank_tools, eval, simulators, tests, tests_eval, script E2E và verify-dev | Đạt |
| `uv run mypy harness` | Đạt; giữ note hiện có về untyped functions ở llm.py |
| `npm --prefix web run lint` | Đạt |
| `npm --prefix web run test` | 15 tests đạt |
| `npm --prefix web run build` | Đạt |
| Playwright specialist + chat-sessions + chat-delivery | 10 tests đạt ở lần chạy trước khi thêm case console |
| Playwright specialist + product | 13 tests đạt, gồm case console |
| Playwright specialist sau chỉnh screenshot reduced motion | 3 tests đạt |
| `git diff --check` | Đạt |

## Phạm vi được kiểm chứng

- FastAPI và SQLite thật trong database tạm; chỉ transport LLM bên ngoài được stub ở integration/E2E. Không dùng database hoặc credential tư vấn thật.
- Nhận hồ sơ có nhu cầu đã biết, tư vấn nhiều lượt, streaming có metadata vai trò trước text, reload giữ vai trò từng tin.
- Không tạo tin nhắn khách giả hoặc memory job từ lời tiếp nhận; giữ nguồn customer cho các facts do khách đính chính.
- Xác nhận thông tin mâu thuẫn giữa kênh; giới hạn lịch sử theo ngân sách ký tự; guard chặn số không có nguồn.
- Duplicate start/message/complete, complete đồng thời, khôi phục initializing sau restart, hủy khởi tạo không bị hồi sinh bởi key cũ.
- Chuyển người thật giữ hồ sơ/checkpoint; khôi phục nếu bị dừng sau checkpoint human nhưng trước khi đóng hồ sơ specialist, không gọi provider ở preflight.
- Tắt cờ không tạo yêu cầu mới nhưng giữ quyền kết thúc phiên hiện tại; kết thúc không cần LLM và không tự sinh callback.
- Telegram commands và replay; Web ở 375px và 1440px; console không cộng specialist đang xử lý vào hàng chờ người thật.

## Cách bật và giới hạn còn lại

Đặt `AI_SPECIALIST_ENABLED=true`, `LLM_BACKEND=openai` và cấu hình model chính/dự phòng như README, rồi khởi động lại backend. Tính năng mặc định tắt. SQLite tự tạo hai bảng bổ sung, dữ liệu cũ vẫn dùng giá trị mặc định.

Môi trường kiểm chứng chưa có đủ `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_BACKUP_MODEL`, nên chưa chạy smoke với LLM thật. Các test xác nhận tích hợp và các điều kiện an toàn; chưa đo chất lượng lời tư vấn, latency hoặc chi phí từ model thật. Không có thay đổi secret, gọi điện, gửi tin cho người ngoài hoặc deploy trong lần triển khai này.

Receipt kết thúc là bản tổng hợp xác định từ phản hồi và receipts đã lưu, không phải bản tóm tắt sinh bởi một lần gọi LLM mới. Nếu memory đang chờ xử lý, receipt ghi rõ `memory_pending`; dữ liệu tư vấn tiếp theo vẫn đi qua cơ chế flush memory hiện có.
