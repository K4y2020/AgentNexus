# A2A result delivery repair

This change repairs the ordinary `send_to_teammate` result path. It does not
complete Phase 2 or all of Phase 3 in `designs/BOT_COMPUTER_A2A_ROADMAP.md`.

## Contract

- Each dispatch uses a durable coordination request ID. Replies carry
  `in_reply_to`; their recipient and correlation come from that stored request.
- New tasks still enter the target Bot's A2A channel. Only an authorized reply
  to an existing request may return to its original primary/Topic session.
- Forwarding an incoming task carries its parent request ID. The server derives
  the next hop and preserves the parent's limit. Terminal results do not spawn
  another request or consume a forwarding hop.
- `wait` defaults to false. Optional waits back off to five seconds and have an
  absolute 300-second cap. Timeout returns pending, not success. Session idle,
  transcript length, and child-session snapshots never establish task completion.
- The recipient explicitly declares the final result, either through
  `send_to_teammate(intent="task.result", in_reply_to=...)` or a final assistant
  report ending in `[A2A_RESULT:<request-id>:succeeded]` / `:failed]`.
  Framework delivery instructions supply the exact request ID and syntax.
- A terminal-turn hook turns that declaration into a durable result/outbox.
  Reconciliation recovers missed hooks from persisted assistant reports.
  Explicit and automatic reports share one unique result key per request.
- A missing declaration remains pending. Progress prose is never guessed to be
  a final result. Declaring success means the agent submitted a result; it does
  not mean an independent review or a roadmap's Exit Criteria passed.
- Returning results reuse the existing runner recovery path. Unbound/unreachable
  recipients wait in the outbox without consuming execution retry attempts.
- Incompatible saved worker models fail explicitly. Claude SDK gateway model
  support remains enabled; the dispatcher does not select a replacement model.

## Verification

The regression suites cover correlated and out-of-order results, forged reply
targets, deduplication, pending timeout, failure presentation, forwarding limits,
stored-report recovery, and model preference enforcement.

`tests/integration/test_control_plane_a2a_bus_live.py` includes a real
Server -> Runner -> Harness -> result -> originating conversation journey using
a scripted mock model. It checks that an intermediate turn produces no result,
then that the final report is persisted, delivered and followed by an assistant
reply in the originating conversation. Mock-model success does not establish
that every upstream model follows the completion protocol reliably.

Verified locally on 2026-09-05: this journey passed with both `openai-agents`
and `claude-sdk`. The targeted backend regression batch passed 71 tests, the
additional bound-origin wake test passed, and the A2A frontend suite passed
10 tests. TypeScript checking and the web production build passed.

Manual acceptance: from two Topics, dispatch different short checks to the same
Bot. Each originating Topic must receive only its own result, with no repeated
inbox polling. Restart during a pending task and verify its eventual declared
result returns. Check that changing an incompatible worker model produces an
explicit error instead of launching a default model.

Remaining roadmap work includes the Computer entity/provider registry, full
Run/worktree/lease lifecycle, Bot Connections and management UI, and broader
cross-host/process-crash fault injection. This patch does not certify exactly-once
external side effects or the entire Phase 2/3 acceptance matrix.
