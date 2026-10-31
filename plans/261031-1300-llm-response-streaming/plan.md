---
title: "Streaming phản hồi LLM có guard"
description: "OpenAI streaming qua SSE tới web chat, giữ guard, failover và idempotency."
status: in-progress
priority: P2
effort: 5h
branch: feat/llm-response-streaming
tags: [backend, frontend, llm]
created: 2026-10-31
---

# Streaming phản hồi LLM có guard

Người dùng yêu cầu streaming thay cho đợi toàn bộ response. Triển khai và phát hành trên GCP hiện tại với cặp OpenAI đã chốt; không đổi secret hoặc nhà cung cấp.

## Hợp đồng

- Draft dùng OpenAI `stream=true`; plan/extract vẫn là JSON nội bộ. Không giả lập typing bằng delay.
- Partial JSON dùng pydantic-core allow_partial trailing-strings. Chỉ phát đoạn kết thúc câu đã qua cùng fact/number/TTL checks; số phải được render từ facts. Disclosure có ngay khi câu báo giá được hiển thị.
- Primary lỗi sau khi đã stream thì reset bản nháp trước backup. Kết quả cuối qua toàn bộ schema/guard và có quyền thay thế prefix. Hai model lỗi dùng DEFAULT_REPLY đã chốt.
- POST `/sessions/{sid}/messages/stream` có bearer owner auth và client_message_id như route JSON; SSE events status/text/result/error. Route JSON và Telegram không đổi.
- Bounded queue; disconnect ngừng gửi nhưng worker vẫn hoàn tất/persist trong ngân sách hiện có, tránh replay side effects khi retry cùng message ID.
- UI optimistic user message và assistant placeholder, cập nhật text theo snapshot; retry không nhân đôi, session change/unmount abort reader. EOF thiếu result là lỗi có thể retry.

## Phases

1. [Provider, guard và SSE API](./phase-01-start.md).
2. [UI, kiểm chứng và phát hành](./phase-02-ui-verification-and-release.md).

## Kiểm chứng

Provider chunk trước finish, Unicode/JSON split, không lộ số/fact sai, failover reset, deadline, auth, SSE final receipt/idempotency/disconnect. Frontend parser phân mảnh UTF-8 và stream events, UI partial-before-result, error/retry. Chạy pytest/Ruff/mypy, frontend lint/test/build và Playwright phù hợp; CI/PR theo bảo vệ main. Real API và production smoke đo event đầu đến trước result qua reverse proxy.

Nguồn: [OpenAI streaming](https://developers.openai.com/api/docs/guides/streaming-responses), [stream events](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events), [pydantic-core JSON parser](https://docs.pydantic.dev/latest/api/pydantic_core/).
