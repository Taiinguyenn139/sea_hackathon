---
phase: 1
title: "Provider, guard và SSE API"
status: pending
priority: P2
effort: 3h
dependencies: []
---

# Provider, guard và SSE API

Ownership: harness/llm.py, harness/streaming.py (new), harness/agent/engine.py, harness/service.py, harness/api.py, tests/test_streaming.py (new). Callback không ghi vào LangGraph checkpoint; truyền qua request ContextVar hoặc closure và khôi phục sau mỗi request.

- [x] Draft provider SSE parsing, usage/finish/DONE handling, partial JSON, max size/deadline và backup reset.
- [x] Guard snapshot chỉ phát complete safe sentences, render fact IDs và disclosure; cuối cùng vẫn guard toàn văn.
- [x] Authenticated bounded SSE route, heartbeat, terminal result/error; receipt persist trước result và disconnect không replay tools.
- [x] Regression cho split chunks/Unicode, unsafe output, partial failure/backup, auth, idempotency và disconnect.
- [x] pytest, Ruff và mypy đạt.
