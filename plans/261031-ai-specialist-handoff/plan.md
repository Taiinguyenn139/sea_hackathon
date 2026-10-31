---
title: "Chuyên viên tư vấn tiếp nhận hội thoại"
description: "Dùng LLM tiếp nhận hồ sơ bàn giao và tư vấn nhiều lượt trên Web và Telegram."
status: implemented-local
priority: P2
tags: [backend, frontend, llm, memory, handoff]
created: 2026-10-31
---

# Chuyên viên tư vấn tiếp nhận hội thoại

Triển khai vai trò chuyên viên dùng LLM để tiếp tục tư vấn ngay trong phiên chat sau khi nhận hồ sơ từ trợ lý. Chuyên viên đọc nhu cầu, lịch sử, thông tin mâu thuẫn và dữ liệu báo giá; tiếp tục trao đổi nhiều lượt rồi lưu kết quả hoặc chuyển yêu cầu cho nhân viên thật.

## Phạm vi đã thống nhất

- Nhãn trong demo: **Chuyên viên tư vấn**; không thêm chữ AI vào từng tin nhắn. Phần giới thiệu demo nói một lần rằng vai trò chuyên viên được mô phỏng bằng LLM; không tạo danh tính nhân viên thật hoặc tuyên bố có người thật đang trả lời.
- Web và Telegram dùng chung backend định tuyến, memory và quyền truy cập.
- Dùng model chính và dự phòng hiện có ở phiên bản đầu. Không yêu cầu nhà cung cấp hoặc model mới.
- Giữ luồng bàn giao người thật hiện có. Chưa triển khai chat trực tiếp với người thật, tổng đài hoặc phân công nhân viên.
- Đã triển khai và kiểm chứng local theo yêu cầu tiếp theo của người dùng. Đã commit tính năng; đang hoàn tất đồng bộ và kiểm chứng để mở PR theo yêu cầu tiếp theo. Chưa deploy.

## Hiện trạng cần xử lý

- `Harness.message` chỉ nhận tin khi phiên `active`; handoff hiện đặt phiên thành `waiting_human`.
- Graph chính tạo brief rồi dừng ở `wait_human` bằng LangGraph interrupt. Chuyên viên LLM cần luồng chạy riêng, không invoke graph đang chờ người thật như một lượt mới.
- Lịch sử trả về cặp `text/reply`; frontend gắn mọi phản hồi với tên trợ lý. Cần lưu vai trò người trả lời theo lượt và giữ khi reload.
- Planner hiện chỉ nhận tin nhắn mới và `known`; chuyên viên cần thêm lịch sử để hiểu câu hỏi nối tiếp.
- `Engine.complete` biến từng commitment thành callback lúc 09:00 theo ngày nghiệp vụ. Không dùng nguyên hành vi này cho kết luận do LLM sinh.
- Khôi phục hiện kiểm tra mọi handoff pending/completing như một handoff người thật. Cần phân nhánh phục hồi theo loại handoff.

## Kiến trúc và trạng thái

Giữ `session.status` cho vòng đời phiên; lưu vai trò đang phụ trách riêng. Dùng `assistant` và `ai_specialist` làm định danh nội bộ, độc lập với nhãn hiển thị.

| Tình huống | Session status | Responder | Handoff |
| --- | --- | --- | --- |
| Trợ lý tư vấn | active | assistant | Không có handoff đang mở |
| Chuyên viên đang nhận hồ sơ | active | ai_specialist | initializing |
| Chuyên viên tư vấn nhiều lượt | active | ai_specialist | in_progress |
| Kết thúc tư vấn chuyên sâu | active | assistant | completed |
| Chuyển sang nhân viên thật | waiting_human | assistant | pending, target=human |
| Phiên kết thúc | ended | Vai trò cuối được lưu | Không còn handoff đang xử lý |

Khách có thể tiếp tục chat trong trạng thái chuyên viên xử lý. Các yêu cầu khi đang initializing được tuần tự hóa hoặc trả trạng thái có thể thử lại; không chạy song song hai lượt nhận hồ sơ. Backend là nơi quyết định chuyển vai trò, không nhận quyền hoặc trạng thái nội bộ từ model.

Graph chuyên viên chạy theo lượt: load context → plan → tools → draft → guard → render. Dùng namespace checkpoint riêng theo session và handoff. Tái sử dụng thành phần nghiệp vụ của graph chính qua các hàm/module chung thay vì sao chép toàn bộ engine.

## Giai đoạn 1 — Dữ liệu và hợp đồng API

Files: `harness/db.py`, `harness/schemas.py`, `harness/config.py`, `harness/api.py`, `web/src/api.ts`.

- [x] Thêm cấu hình `AI_SPECIALIST_ENABLED`, mặc định tắt. Bật tính năng chỉ khi backend LLM được cấu hình hợp lệ; tests dùng provider stub xác định trước.
- [x] Thêm bảng metadata định tuyến theo session và metadata handoff gồm target, trạng thái, phiên bản prompt và liên kết handoff kế tiếp nếu có.
- [x] Ưu tiên bảng bổ sung để giữ tương thích các lệnh positional INSERT đang có. Migration đọc lại được database cũ, không tạo thêm handoff.
- [x] Handoff cũ mặc định target=human; lượt cũ mặc định responder=assistant.
- [x] Bổ sung responder vào receipt/result và trace của từng lượt; API lịch sử đọc trường này với giá trị mặc định cho dữ liệu cũ.
- [x] Thêm endpoint chủ phiên `POST /sessions/{sid}/specialist/start` nhận reason và idempotency key; trả handoff, trạng thái phiên và lời tiếp nhận đã lưu.
- [x] Thêm endpoint chủ phiên `POST /sessions/{sid}/specialist/complete` với idempotency key. Tin nhắn tiếp theo vẫn qua API messages/stream hiện có.
- [x] Giữ API human handoff cũ. Trả metadata responder trong session, JSON reply và kết quả SSE; phát metadata vai trò trước text snapshot để frontend gắn đúng nhãn khi streaming.
- [x] Khi tắt tính năng: chặn khởi tạo mới; phiên chuyên viên đang mở vẫn có thể hoàn tất hoặc chuyển người thật. Nếu không còn cấu hình LLM hợp lệ, trả lỗi phục hồi được và vẫn cho phép quay lại trợ lý bằng thao tác xác định trước.

Đạt khi migration và hợp đồng cũ vẫn hoạt động; không cần sửa frontend cũ để đọc được dữ liệu trước thay đổi.

## Giai đoạn 2 — Định tuyến và vòng đời bàn giao

Files: `harness/service.py`, `harness/agent/engine.py`, `harness/api.py`, module quản lý handoff bổ sung nếu cần.

- [x] Tách tạo hồ sơ bàn giao khỏi interrupt và khỏi logic hoàn tất người thật.
- [x] Start xử lý memory còn tồn, lưu snapshot brief và transcript, ghim phiên bản prompt rồi chuyển responder trong transaction ngắn. Không gọi provider khi đang giữ transaction SQLite.
- [x] Dùng cùng customer lock và quy tắc idempotency hiện tại. Tối đa một bên đang tiếp nhận/điều khiển một phiên.
- [x] Chuyên viên gửi lời tiếp nhận ngay sau khi start. Lưu lời này như sự kiện hội thoại có vai trò, không giả một lời khách rồi enqueue vào customer memory.
- [x] Định tuyến tin nhắn thông thường theo responder. Không để từ khóa chuyên viên trong tin nhắn gây handoff đệ quy khi chuyên viên đã phụ trách.
- [x] Nút tư vấn chuyên sâu đi vào specialist. Yêu cầu rõ ràng gặp người thật đi vào human handoff; yêu cầu mơ hồ có thể giới thiệu lựa chọn thay vì ngầm tuyên bố đã kết nối người thật.
- [x] Chuyển người thật từ specialist tạo snapshot cập nhật, giữ liên kết hồ sơ và kết quả tư vấn trước đó; không chạy lại tools có side effect.
- [x] Hoàn tất chuyên viên lưu receipt và chuyển về assistant một lần. Chủ phiên có nút quay lại trợ lý, không phụ thuộc thành công của LLM để thoát chế độ này.
- [x] Khôi phục sau restart theo target và trạng thái; preflight giữ nguyên yêu cầu checkpoint của human, không áp dụng sai vào specialist.

Đạt khi chạy được start → nhiều lượt → complete → trợ lý và specialist → human, kể cả retry hoặc restart giữa các bước.

## Giai đoạn 3 — Agent chuyên viên và memory

Files: `harness/agent/specialist.py` mới, `harness/agent/engine.py`, `harness/llm.py`, `harness/memory/service.py`.

- [x] Prompt chuyên viên: tiếp nối nhu cầu đã biết, hỏi thông tin còn thiếu, xác nhận mâu thuẫn, giải thích phương án và xử lý băn khoăn.
- [x] Context gồm brief lúc bàn giao, facts còn hiệu lực, memory cập nhật, tin nhắn mới và lịch sử liên quan. Planner cũng nhận ngữ cảnh hội thoại.
- [x] Đặt ngân sách context cho chuyên viên: ưu tiên tin mới, facts và câu hỏi chưa giải quyết; giới hạn transcript theo ngân sách rõ ràng. Không triển khai vector database trong phạm vi này.
- [x] Tái sử dụng tool dispatcher và quyền hiện có. Số liệu qua fact IDs, kiểm tra hiệu lực, tính phù hợp và disclosures như luồng tư vấn chính.
- [x] Input guard áp dụng start reason và tin nhắn. Endpoint kết thúc chỉ nhận handoff_id và idempotency_key, không có nội dung tự do để đưa vào LLM. Transcript và brief không có quyền thay chỉ dẫn hệ thống.
- [x] Tin mới do khách cung cấp dùng cùng memory queue. Nhận định hoặc bản tổng kết do chuyên viên viết được lưu ở handoff, không tự biến thành facts nguồn customer.
- [x] Receipt kết thúc có cấu trúc cố định và tham chiếu lượt/fact, gồm phản hồi đã lưu, facts nguồn khách đang active, điểm còn mở và bước tiếp theo. Dùng trích xuất xác định trước, không thêm lần gọi LLM hoặc tạo báo giá mới. Nếu memory chưa xử lý hết thì ghi memory_pending=true.
- [x] Callback chỉ được tạo qua tool khi có yêu cầu của khách và thời gian hợp lệ. Summary chỉ tham chiếu receipt hành động đã thực hiện; không tự tạo hành động từ commitment tự do.
- [x] Lỗi model dùng chính sách primary/backup và ngân sách hiện tại; giữ state để retry. Khi summary lỗi vẫn có thể lưu receipt kết thúc tối thiểu từ dữ liệu đã có.

Đạt khi chuyên viên tiếp nối được hồ sơ mà không hỏi lại dữ liệu đã đủ, không tạo số liệu hoặc lịch gọi lại ngoài dữ liệu hợp lệ.

## Giai đoạn 4 — Web, Telegram và console

Files: `web/src/pages/customer-chat.tsx`, `web/src/pages/staff-console.tsx`, `web/src/api.ts`, `web/src/chat-history.tsx`, `harness/channels/telegram.py`.

- [x] Thêm hành động mở tư vấn chuyên sâu; lời tiếp nhận có nhãn Chuyên viên tư vấn, giữ nguyên phiên.
- [x] Hiển thị vai trò theo từng tin, gồm snapshot streaming, result, retry và lịch sử tải lại. Không dùng duy nhất responder hiện tại để đổi tên tất cả tin cũ.
- [x] Mở composer cho specialist; chỉ khóa khi gửi, initializing hoặc waiting_human theo trạng thái thực tế.
- [x] Nút Quay lại trợ lý và Yêu cầu nhân viên thật gọi đúng luồng và cập nhật session từ backend.
- [x] Sidebar, polling và tạo phiên mới không suy luận waiting_human chỉ từ việc có handoff_id. Phiên mới bắt đầu ở trợ lý, dùng chung memory được phép, không tự nhận handoff của phiên cũ.
- [x] Telegram dùng cùng router, thông báo lúc chuyển vai trò, hỗ trợ kết thúc/chuyển người thật bằng command hoặc nút xác định. Retry provider update không tạo lại lời tiếp nhận.
- [x] Console phân biệt hồ sơ mô phỏng và yêu cầu nhân viên thật, xem transcript/tổng kết/nguồn. Không đếm specialist đang xử lý vào số yêu cầu người thật chờ nhận.

Đạt khi luồng Web và Telegram có cùng hành vi, refresh không mất vai trò hoặc làm khóa nhầm phiên.

## Giai đoạn 5 — Kiểm chứng và chuẩn bị phát hành

- [x] Backend integration: đủ context trước chuyển, specialist nhiều lượt, kết thúc, chuyển human, tạo phiên mới, auth và tách khách hàng.
- [x] Recovery: DB cũ, restart khi initializing/in_progress/completing, feature flag tắt, graph human đang interrupt.
- [x] Idempotency: duplicate start/message/complete, các yêu cầu complete đồng thời, Telegram replay, không lặp side effects. Message và complete dùng chung customer lock; cơ chế disconnect SSE được giữ và kiểm tra bởi suite streaming hiện có.
- [x] LLM stub: primary lỗi, backup lỗi, schema sai, context quá dài, đầu vào bị chặn, số liệu sai, fact hết hạn, hồ sơ không phù hợp.
- [x] Memory: mâu thuẫn giữa kênh, sửa thông tin khi specialist phụ trách, summary không ghi facts giả, memory vẫn dùng được sau complete.
- [x] Frontend tests và E2E với backend cách ly: tên người trả lời, streaming, reload, sidebar, composer, console counts và nút chuyển vai trò.
- [x] Chạy pytest, Ruff, mypy; frontend lint, tests và build theo README. Chạy Playwright phù hợp phạm vi thay đổi.
- [ ] Smoke với LLM thật trong môi trường demo khi có cấu hình: lời tiếp nhận, hội thoại nhiều lượt, thất bại provider, kết thúc; ghi nhận độ trễ và usage theo vai trò, không log secret.
- [x] Cập nhật README và hướng dẫn Telegram: cấu hình, kịch bản demo, giới hạn mô phỏng và cách tắt tính năng.

Không đưa scenario dùng để tinh chỉnh specialist vào held-out. Gắn phiên bản prompt và cấu hình chạy vào kết quả đánh giá; đánh giá khả năng tiếp nối, hỏi lặp, nguồn số liệu, memory và chất lượng bàn giao.

## Kịch bản nghiệm thu demo

1. Khách cung cấp nhu cầu vay, thu nhập và kỳ hạn cho trợ lý.
2. Khách chọn chuyên viên tư vấn; chuyên viên tiếp nhận bằng thông tin đã biết.
3. Khách hỏi tiếp về băn khoăn lãi suất, bổ sung hoặc đính chính một thông tin.
4. Chuyên viên xác nhận khi cần, tra tools và trả lời có căn cứ.
5. Tải lại trang: vẫn đúng phiên, lịch sử và vai trò; không tạo lại handoff.
6. Khách quay lại trợ lý: có receipt kết thúc, memory mới vẫn được dùng.
7. Trong ca riêng, khách yêu cầu người thật: hồ sơ vào hàng chờ console và giữ đủ ngữ cảnh specialist.

## Thứ tự thực hiện và đầu ra

Triển khai tuần tự: giai đoạn 1 → 2 → 3 → 4 → 5. Các giai đoạn triển khai local đã hoàn tất; smoke bằng credential thật còn chờ môi trường demo. Không cần tăng số process, thêm queue service hoặc mở rộng kiến trúc multi-agent ngoài vai trò specialist.

Đầu ra gồm migration, backend chuyên viên, Web/Telegram, console cập nhật, tests và hướng dẫn demo. Phát hành dùng pipeline hiện có khi được giao bước phát hành; không đổi secret hoặc deploy trong lúc lập kế hoạch.


## Kết quả triển khai

- Backend: agent chuyên viên dùng chung engine nghiệp vụ, checkpoint riêng, tiếp nhận/lưu receipt có idempotency và khôi phục sau restart.
- Web/Telegram: tư vấn nhiều lượt, nhãn người trả lời theo turn, quay lại trợ lý và chuyển người thật. Console tách yêu cầu mô phỏng khỏi bộ đếm hàng chờ người thật.
- Receipt kết thúc dùng dữ liệu đã lưu, không gọi thêm LLM; complete được chống lặp theo handoff_id. Đây là lựa chọn để khách vẫn thoát được khi provider lỗi.
- Không có đủ OPENAI_API_KEY/OPENAI_MODEL/OPENAI_BACKUP_MODEL trong môi trường chạy hiện tại. Smoke thật chưa chạy; không thay cấu hình hoặc secret để giả lập kết quả này.
- Xem [báo cáo kiểm chứng](verification.md) và README để bật tính năng.
