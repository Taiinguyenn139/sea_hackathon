# LLM streaming verification

Scope: OpenAI draft stream → guarded sentence snapshots → authenticated SSE → progressive web chat. Keep input security screening, primary/backup configuration, numeric/fact guard, JSON route and Telegram behavior.

## Evidence

- Backend: 344 tests and 120 subtests passed before adding shutdown/truncation cases; final count recorded after rerun.
- Ruff and mypy passed. Frontend typecheck, 11 Vitest tests and production build passed.
- Playwright: 18 tests passed, including partial content before result, mobile/desktop delivery, retry ID/draft preservation, history races and navigation.
- Socket test receives partial content before result, disconnects, then verifies persisted receipt and retry without a second draft call. Additional variant shuts server down immediately after disconnect.
- Real OpenAI primary: 21 partial JSON snapshots; first delta 1.730s, completion 1.917s. Completion log confirms `streamed: true`.
- Real local full SSE quote: first status 0.001s, first guarded text 5.353s, final result 6.564s; trusted fact IDs present and no G1 rewrite. Isolated database, no production reset or fault injection.
- Independent simplifier review approved integration after fixes for synchronous double-submit and draining disconnected stream workers at shutdown.

## Decisions and limits

Client receives checked sentences, not raw model tokens. Full final validation can replace a preview. Model retry resets the preview; empty partial JSON strings are ignored and cannot falsely signal backup. Input-blocked requests skip memory brief/flush in both API routes.

Worker continues after client disconnect to preserve idempotency. Shutdown waits for its receipt and existing post-result processing; a large Telegram outbox can prolong shutdown. Existing release grace remains 30 seconds; per-step provider budgets do not imply a whole-request deadline.

Production proxy timing and deployment revision will be recorded after the PR is merged and the existing GCP pipeline completes. Secret configuration is unchanged.
