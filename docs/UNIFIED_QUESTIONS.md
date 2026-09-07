# Unified user questions

`sys_ask_user` is the canonical question entry point for main Bots and workers.
ToolManager registers it for every agent; native relay schemas expose the same
tool. Runtime instructions request its use when a decision is needed, instead
of Markdown choices. This is model guidance, not a natural-language classifier.

```json
{
  "questions": [{
    "id": "next_step",
    "question": "How should we continue?",
    "options": [
      {"label": "Fix the code", "description": "Apply the targeted changes"},
      {"label": "Wait", "description": "Make no changes yet"}
    ]
  }]
}
```

Omit options for free text. Up to three questions are accepted. Questions have
distinct IDs, optional multi-select, and a sensitive-input flag. No option is
automatically accepted. A successful answer returns `status: answered`; decline,
cancel, timeout, or invalid answers never imply permission to proceed.

## Routing

- Main Bot: the existing interactive chat question card appears in its session.
- Child: existing ancestor mirroring shows the card in the parent chat. The
  original child turn remains parked and resumes when answered. Known ordinary
  answers can use the parent continuation tool; unknowns remain for the human.
- A2A recipient: supply `a2a_request_id`. The server verifies this session received
  that command and that the caller can edit the sender session before mirroring
  the card to that exact originating chat. No latest-session fallback exists.
- CLI-native question adapters keep their existing normalized card and resolver.
  For A2A correlation, agents should use `sys_ask_user` with the request ID;
  arbitrary legacy native questions do not acquire an inferred A2A origin.
- Permissions remain separate from ordinary questions; this tool does not grant
  policy exceptions. Sensitive questions cannot be auto-answered by a parent.

The transport reuses the server's elicitation registry, ownership checks, SSE
events and resolve endpoint. It does not add a second question table or UI.
Pending state survives page reload, not a server restart. The current request
wait limit is ten minutes; there is no automatic resubmission after a lost reply.

## Verification

After restarting idle server/runner processes, ask Polly to offer two choices
using `sys_ask_user`. Select one in the chat card and submit: Polly should receive
the exact answer in the same conversation. Cancel a second question: neither
option should execute. Repeat with a child and an A2A request; answers must return
to the waiting target, not open a new session.

Service-route integration tests cover main/child/A2A cards, answer/decline/cancel,
and rejection of unrelated A2A request IDs. This does not certify real-model
compliance or evidence-backed task acceptance.
