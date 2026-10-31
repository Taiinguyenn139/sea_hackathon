---
phase: 2
title: "UI, kiểm chứng và phát hành"
status: pending
priority: P2
effort: 2h
dependencies: [1]
---

# UI, kiểm chứng và phát hành

Ownership: web/src/api.ts, web/src/pages/customer-chat.tsx, web/tests/api.test.ts, web/tests/e2e/product.spec.ts, docs/deployment.md. Giữ layout hiện có, không thêm thư viện UI.

- [ ] Typed SSE reader, fragmented UTF-8, status/text/result/error và EOF/cancel handling.
- [ ] UI partial snapshots, optimistic messages, abort cleanup, no duplicate retry và final session metadata.
- [ ] Unit/UI tests và frontend lint/test/build đạt; independent review khi diff vượt ngưỡng.
- [ ] Smoke OpenAI thật và SSE qua GCP proxy; event đầu và partial text trước final, không dùng fault injection production.
- [ ] PR/CI/release, cập nhật runbook và plan/journal; giữ secret version 5.
