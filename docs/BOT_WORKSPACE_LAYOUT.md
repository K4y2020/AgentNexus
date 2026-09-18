# Bot Task Workspaces

New Bot sessions without a bound Project use
`<bot-home>/topics/<session-id>/`. The server owns this path; stale client
workspace preferences do not select it. Primary chats, Topics, scoped A2A
sessions, and routine runs use their own session directory.

Each directory contains `inputs/`, `work/`, `outputs/`, and `temp/`.
The session stores its assigned path, so resuming a session does not allocate
another directory. Project checkout/worktree resolution remains unchanged.
Delegated workers should receive the explicit task workspace; independent Git
writers still require worktree isolation.

Sessions using this layout carry `agentnexus.workspace_layout=topic-v1`.
The runner adds the storage conventions to framework instructions. Directory
isolation is enforced by assignment; file placement within the directory is
agent guidance, not a filesystem sandbox.

## Existing Workspaces

No migration moves existing sessions or files. Existing chats continue at
their saved paths. The legacy Debby directory has an `AGENTS.md` guide for
future work; harnesses that do not read that file may need explicit guidance.

Before reorganizing old tasks, inventory source files, relative asset links,
script paths, and conversation references. Move a complete task only after
review, with an old-to-new path manifest and a rollback plan. Do not auto-delete
browser profiles or infer that a file is disposable from its name alone.

## Deployment and Verification

Restart the server and local runner after backend updates, when tasks are idle.
Create two new Bot Topics and verify distinct `topics/<session-id>` paths and
the four subdirectories. Resume either Topic and verify its path is unchanged.
Open an old conversation and verify its previous workspace is unchanged.
No UI artifact filter or automatic archival/deletion is included in this change.
