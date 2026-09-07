# Sub-agent continuation: first delivery

## Implemented

- `sys_session_send(task_id=..., args=...)` continues the original child using
  the existing durable session ID. It retains the direct-child authorization,
  closed-session checks and busy-turn checks of `session_id` mode.
- Changing a title no longer silently creates another child when that worker
  already has a child. The dispatcher returns `continuation_decision_required`
  and existing task IDs. It reads all child-summary pages, including after restart.
- Independent work remains possible with `new_task_reason`; the reason is saved
  in the new session labels. This is an explicit decision, not semantic proof
  that the new task is independent. Concurrent first dispatches can still create
  separate children; this guard is not an atomic one-worker-one-session constraint.
- Missing-input replies and review corrections should continue the original task.
  New independent tasks must receive full project context. No path is guessed or
  copied from another child, and no workspace authorization is broadened.
- Tool documentation now correctly describes an asynchronous launching handle.
- Completion notices distinguish execution termination from task success.

## Not implemented by this change

There is not yet a structured `needs_input` task lifecycle, an evidence-backed
acceptance record, a required-child success gate, or a final-response validator.
The dispatcher cannot guarantee that a model will not misstate success in prose.
`task_id` currently identifies a child conversation, not a separately versioned
work item or execution attempt. No new UI success indicators are introduced.

The next delivery needs versioned task outcomes and acceptance evidence before
claiming end-to-end protection against false completion. Do not parse success or
failure from natural-language keywords, or treat an earlier review as approval of
later changes.

## Structured questions

Ordinary Codex requestUserInput, Antigravity askQuestion and explicitly identified
Claude native AskUserQuestion prompts now wake the parent immediately, once per
question. Permission approvals and secret inputs retain the human escalation path.
Existing ancestor chat cards remain the human-facing interface; no new UI is needed.

The parent can answer from verified task context:

```json
{
  "task_id": "original-child-session-id",
  "question_id": "pending-elicitation-id",
  "answers": {"path": "U:/AI/Gamehack/ExportedProject"},
  "args": "Workspace recorded in the original user task"
}
```

This resolves the original parked request, rather than sending another chat turn.
All questions must be answered, keyed by stable ID or exact question text. Unknown
questions, unsupported providers, permission approvals, and secret inputs stay in
the human chat card. Other parents' questions and descendant-mirrored prompts cannot
be answered through the direct-child tool. Submission means `answer_submitted`, not
task success. A timeout is reported as unconfirmed delivery, not blindly retried.

The existing elicitation registry is in-memory: this does not add restart-safe
question persistence. A prose report asking for a path is not automatically converted
into an elicitation. It still needs an ordinary follow-up to the original task.

## Verification

Ask a worker to inspect a file, then request a correction with a different title.
Without `new_task_reason`, expect a continuation decision and no session creation.
Send the known absolute file path using the returned `task_id`: the original child
must receive the follow-up. A task ID belonging to another parent must be rejected.
Explicit independent reviews may create a new child and must retain their reason.

Changes require restarting the affected server/runner processes. Do not interrupt
active user tasks to load this repair.
