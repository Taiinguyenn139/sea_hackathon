# Dataset memory tích hợp với Memory Harness

Bộ mẫu dev synthetic dùng hợp đồng đang có trên `main`: FastAPI/Pydantic,
web/Telegram, hồ sơ `hoa`, sản phẩm `home_loan`, mock bank tools và HTTP eval
runner. Không cần thêm dependency hoặc một dịch vụ mock riêng.

## Nguồn dữ liệu và ranh giới

| Thành phần | File | Vai trò |
| --- | --- | --- |
| Ngân hàng giả lập | `bank_tools/fixtures/*.yaml` | Nguồn chính sách và giá cho tools |
| Identity demo | `simulators/datasets/identity_v1/identities.json` | Nguồn hồ sơ/ngày sinh cho verify_identity; alias hoa vẫn dùng được |
| Oracle giá | `eval/oracle_fixtures/bank.json` | Đáp án độc lập, không đọc kết quả tool làm đáp án |
| Journey ba phiên | `simulators/scenarios/dev/mortgage-memory-journey.json` | Lời khách và chỉ dẫn simulator/evaluator; agent tự trả lời |
| Schema scenario | `simulators/schemas.py` | Pydantic contract kiểm tra khi runner nạp scenario |
| Expected memory | `eval/oracle_fixtures/memory/mortgage-memory-journey.json` | Đáp án từng checkpoint, chỉ evaluator đọc |
| Schema và bộ so memory | `eval/memory_contract.py` | So các fact cần có với Call Brief qua API |
| Kiểm thử | `tests_eval/test_memory_dataset.py` | Schema, liên kết fixture, provenance, mutation và runner giả lập |

Thư mục `data/` được `.gitignore` dành cho dữ liệu runtime. Không đặt fixtures
cần commit ở đó. Không đưa oracle vào DB, prompt, Call Brief đầu vào hoặc tools.
Đây là dữ liệu dev công khai, không phải held-out.

## Journey

| Phiên | Kênh/ngày | Nội dung và expected memory sau end-session flush |
| --- | --- | --- |
| 1 | web · 27/09/2026 | Vay mua nhà 2 tỷ, 240 tháng, thu nhập 45 triệu, nhà 3 tỷ, trả nợ hiện hữu 0. Nêu mối lo lãi sau ưu đãi rồi cần bàn với chồng. Lưu nguồn từng fact và quote 6,5% từ HL-Q3-2026. |
| 2 | telegram · 28/09/2026 | Khách nói thu nhập 40 triệu và chưa xác nhận khác biệt. Hai fact 45/40 triệu phải conflicted; giữ amount và term; không có income active. |
| 3 | web · 31/10/2026 | Khách xác nhận thu nhập 40 triệu. Hai bản thu nhập trước superseded; bản xác nhận active. Quote Q3 expired nhưng còn trong lịch sử, Q4 trên web 7,1% trong 6 tháng active. Khách yêu cầu chuyên viên giải thích lãi sau ưu đãi và giấy tờ. |

Mỗi phiên được runner tạo bằng `/admin/sessions`, sau đó `/verify` dùng ngày
sinh giả của hồ sơ `hoa`: `1988-05-12`. Quyền truy cập trước xác minh vẫn được
kiểm tra bởi scenario `unverified-identity` đã có trên main.

Ngày đầu đổi từ 27/10 trong bản thiết kế cũ thành 27/09: promo Q3 trong fixture
hết hạn ngày 30/09. Giữ nguyên chính sách ngân hàng để benchmark nhất quán.
Kênh đổi từ hotline/Zalo sang web/Telegram đúng API hiện tại.

## Schema và chuẩn hóa

- Key dùng theo runtime: `amount`, `term`, `income`, `debt`, `property_value`,
  `objection`, `promo_rate`, `promo_months`.
- Fact.value là chuỗi: `"2000000000"`, `"240"`, `"6.9"`. Tiền dùng `đồng`,
  kỳ hạn `tháng`, lãi `%/năm`; không dùng phân số `0.069` cho 6,9%.
- `observed_at`, `valid_until`, `as_of` là ngày `YYYY-MM-DD`; hiệu lực ngân hàng
  bao gồm cả ngày cuối. Bank tools nhận ngày từ clock DB do `/admin/clock` đặt.
- Fact ngân hàng hết hạn có status `expired`, không phải `stale` như thiết kế v1.
- Fact ID do backend sinh. Oracle không hardcode ID. `source_step: [1, 0]`
  nghĩa là phiên thứ nhất, step đầu tiên; runner lấy `turn_id` thật từ public trace.
- Fact ngân hàng phải khớp receipt trong trace: ID, key, value, unit, channel,
  observed_at, valid_until và source_ref. Trạng thái hiện tại có thể expired dù
  receipt lúc phát hành là active.
- Checkpoint liệt kê **các fact bắt buộc cần kiểm tra**, không phải toàn bộ dump
  DB. Backend có thể có thêm fact lãi chuẩn, biên độ hoặc trả góp. Mỗi fact được
  yêu cầu phải khớp đúng một bản, và các slot trong `unique_active_keys` phải có
  đúng một fact customer active.
- `conflict_keys` phải khớp cả facts và danh sách conflicts trên Brief. Các fact
  conflicted/expired/stale được yêu cầu phải có trong warnings của Brief.
- Schema không chấp nhận trường lạ, ngày sai, checkpoint có fact tương lai,
  source step thiếu, nguồn khác ngày/kênh, hoặc fact hết hạn vẫn active.

Pydantic là dependency đã có trong `pyproject.toml`/`uv.lock`. Khi cần JSON Schema
cho editor hoặc UI, dùng `Scenario.model_json_schema()` và
`MemoryContract.model_json_schema()`; không duy trì bản schema viết tay thứ hai.

## Tích hợp evaluate

Runner vẫn dùng các kịch bản dev hiện có. Thêm tùy chọn:

```sh
uv run python -m eval.runner --backend rules --split dev \
  --memory-oracles eval/oracle_fixtures/memory
```

Lệnh dành cho môi trường benchmark cô lập đã được team/CI chuẩn bị; runner
có quyền reset nên vẫn chỉ nhận backend localhost. Không chạy trên môi trường
demo dùng chung. Đợt thay đổi này không setup hoặc chạy ứng dụng local.

Khi bật tùy chọn:

1. Validate oracle và liên kết scenario trước khi gọi backend.
2. Giữ trace IDs/receipts từng lượt để truy nguồn, không cấp đáp án cho agent.
3. Sau `/sessions/{id}/end`, đọc `/customers/{profile}/brief` bằng quyền admin
   của runner, so checkpoint với memory đã flush.
4. Ghi `memory_checks` trong từng result; checkpoint sai tăng `failures` và
   khiến gate `behavior_checks` của harness thất bại. Lỗi nội dung memory không
   biến thành lỗi HTTP; run vẫn có thể completed với gate không đạt.
5. Lưu `memory_oracle_hash` và danh sách scenario được phủ trong metadata.

Mẫu chỉ yêu cầu memory checkpoints ở mode `harness`. Hai baseline tiếp tục
được chấm các KPI hội thoại; mảng `memory_checks` trống của baseline nghĩa là
không chấm memory, không phải đã đạt. Khi không truyền `--memory-oracles`,
không đọc oracle memory và không phát sinh request Brief bổ sung.

Hiện mới có oracle memory cho một journey. Không suy rộng kết quả này thành
coverage toàn bộ dev suite. Với held-out, runner yêu cầu `memory_oracle_hash`
khớp manifest đã đóng băng nếu bật memory checks; bộ đóng băng hiện có chưa tự
thêm trường này, đội đánh giá cần đưa nó vào manifest trước khi phê duyệt.

Handoff dùng cơ chế có sẵn: runner đọc summary, chấm các trường bắt buộc, rồi
complete/retry complete để kiểm tra idempotency. Checkpoint cuối đọc **sau**
completion và end-session. Mẫu không khẳng định có người thật đã nhận hồ sơ.
Không đặt stage `apply` và không xóa concern bằng một câu mẫu, nên yêu cầu gặp
chuyên viên không tự được tính là khách đã chuyển đổi thành công.

## Giới hạn phát hiện khi đọc code

Các điểm sau chưa được sửa trong task data; không báo rằng mẫu đã kiểm chứng chúng:

- Extractor chưa nhận `40tr`, `2 tỉ`; mẫu tích hợp dùng `40 triệu`, `2 tỷ`.
  Cần journey edge-case riêng để đo mở rộng chuẩn hóa tiếng Việt.
- `objection` là một slot; mối lo trước chuyển sang superseded khi khách nêu mối
  lo khác. Oracle phản ánh lịch sử hiện có, chưa chứng minh nhiều mối lo cùng active.
- Câu có cả số sai và số đính chính có thể bị parser lấy số cuối. Mẫu dùng một
  câu xác nhận rõ `40 triệu`, không giấu lỗi này bằng kết quả benchmark giả.
- Các từ `gọi lại`/`hẹn` kích hoạt lịch gọi ra; backend chưa phân biệt chắc chắn
  khách tự gọi lại. Journey tích hợp chưa chấm commitment đã hoàn thành của bản v1.
- Handoff hiện lấy bot_said trong phiên chuyển; open_questions dựa vào conflict
  keys, chưa đảm bảo giữ mọi câu hỏi tự do từ tất cả phiên.
- Danh sách customer_claims của runner hiện áp dụng toàn journey, chưa giới hạn
  theo thời điểm tiết lộ. Memory checker có kiểm tra future facts; điều này không
  thay thế việc cần nâng cấp oracle số theo từng step.

## Trạng thái kiểm tra

Đã chuẩn bị tests để CI chạy với `uv run pytest -q` và lint chung của repo.
Không cài dependency, không chạy backend, benchmark hay test suite local trong
đợt tích hợp theo yêu cầu của người dùng. Kiểm tra tĩnh JSON/Python và Git diff
được báo riêng trong PR; kết quả CI lấy từ GitHub Actions.
