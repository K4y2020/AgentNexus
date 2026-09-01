# AgentNexus fixed acceptance sample

This directory is the public acceptance sample the control-plane plan calls
for: a tiny, self-contained project plus 20 standard tasks that multi-agent
runs can execute and review without real customer data or model tokens.

## What is here

- `sample_repo/` - a minimal, dependency-free Python package (order pricing
  helpers) with a baseline unittest suite.
- `tasks.json` - 20 standard tasks, each with a story, acceptance criteria,
  allowed scope, and required review evidence.
- `verify_baseline.py` - the evidence command; exits 0 only when the whole
  baseline suite passes, and prints a machine-readable JSON report.
- `workflow.plan-implement-review.json` - a ready-to-post
  `POST /v1/coordination/workflows/template` payload for the
  Plan -> Implement -> Review flow (replace the session-id fields).
- `acceptance-matrix.template.md` - the per-run evidence table used by human
  and agent reviewers.

## Running the evidence command

```bash
python demos/agentnexus-acceptance/verify_baseline.py
```

The command needs only the standard library. Expected baseline output ends
with a JSON report whose `success` is `true`.

## Using it as the fixed acceptance sample

1. Treat this directory as the sample checkout for every collaborative-coding
   demo: one shared workspace, independent per-agent worktrees, and the
   `tasks.json` tasks as the only workload during Gate D evaluation.
2. Start the Plan -> Implement -> Review workflow from
   `workflow.plan-implement-review.json`, replacing `root_session_id` and the
   three `assignee_session_id` fields with real session ids.
3. Each stage runs the evidence command itself. Implementers never touch files
   outside `sample_repo/`, never write credentials into the diff, and do not
   commit to a user's main branch without explicit approval.
4. The reviewer reruns the evidence command, checks diff scope, and fills one
   row in the acceptance matrix before reporting `approve` or `needs_changes`.
5. Record only evidence that actually exists: test output, session links,
   diff links, and the acceptance-matrix row. A "looks done" comment without
   evidence does not close a task.

## Public sample repo

To turn this into the standalone public demo repo referenced in the plan,
copy this directory into a new git repository (sample project + tasks +
evidence command), push it, and record the URL in the plan's Gate D materials.
The demo itself never depends on AgentNexus internals, so it can be reused by
any downstream team comparing harnesses on identical acceptance conditions.
