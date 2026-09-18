# Teammates: Persistent Bot Team on Harness Adapters

## Goal

Turn AgentNexus's session-scoped agents (today: Debby/Polly-style YAML personas that
exist only while a session runs) into **persistent teammates** — bots with identity,
memory, routines, message channels, and optionally a dedicated machine — without
changing the harness invocation layer. This is the product counterpart of
"AI teammates you actually own" (Rakazo's positioning), built on AgentNexus's
harness-agnostic architecture instead of a self-hosted bot runtime.

Non-goals: voice/calling, general-purpose assistant bots (chat companions,
booking agents), and replacing the session as the unit of work.

## TL;DR

- **The bottom layer does not change.** Harness invocation stays "spawn the vendor
  CLI and bridge it" (`claude <args>`, Codex app server, ACP stdio). A bot is a
  *logical identity* above that; every work shift is a short-lived CLI process.
  **1 bot → N ephemeral CLI processes over its lifetime; 1 CLI binary serves M bots.**
- **The concept model is company / employee / workstation.** The server holds
  identities, memory, routines, and track record ("the company"). Bots are employees —
  logical, portable. Machines running the runner (or cloud sandboxes) are
  workstations — replaceable compute. Employee–workstation binding is a management
  action, not an architectural fact. This is the key structural advantage over
  Rakazo, whose bot state lives partly in the machine's filesystem and dies with it.
- **~80% of the foundations already exist** (audited below): scheduled tasks with
  rrules and per-task policies, a Slack integration, opt-in hindsight memory, the
  coordination/A2A workflow engine, managed-host lifecycle with a runner daemon and
  tunnel transport, and working multi-harness fan-out exemplars (Debby, Polly).
- **Phase 1 is "make Debby a resident employee"** — zero format changes, only a
  hosting-model change: registry persistence + memory injection + routine binding +
  channel binding. Phase 2 (bot-to-bot delegation at scale) is **gated on fixing the
  4 known P1 blockers in `coordination/`**. Phase 3 adds a workstation tier
  (`persistent_sandbox`) by adding owner semantics to the existing managed-host
  lifecycle.
- **UI direction**: replicate Rakazo's visual language (Apache-2.0 — design language
  only, no code porting) as a `rakazo-dark` token preset, plus a Teammates roster
  page and bot activity cards. Decision item: standalone roster page (default) vs
  always-visible sidebar tabs (closer to Rakazo). A task-board-first main view is a
  stronger long-term direction for coding teams (see Product directions).

---

## Background: the Rakazo benchmark

[Rakazo](https://github.com/elie222/rakazo) (Apache-2.0, TS monorepo) ships the
product surface this proposal targets: persistent bots with memory, routines, voice,
and Slack/WhatsApp/Telegram/iMessage channels; bots own Team/Private Computers; a
graphical desktop panel with Take-control; four-layer test pyramid ending in
real-sandbox e2e + canaries.

What we take vs. leave:

| Take | Leave |
|---|---|
| Product semantics: memory / routines / channels / roster | Their bot runtime (single-vendor, Pi-based) — we keep harness adapters |
| Security fail-closed patterns (loopback-only Postgres, reject plaintext HTTP for sensitive media, Markdown/HTML-escape all bot output — audit our web UI XSS surface for agent-rendered content) | Their compute model (bot state in machine filesystem) — we keep git + platform memory as state |
| Real-sandbox deterministic e2e + canary discipline (fits alongside `harness_bench`) | Voice-first product bets |
| Design language of their UI (dark three-pane roster/chat/computer) | Their `packages/ui` code (bound to their oRPC contracts; porting costs more than re-creating) |

License note: Apache-2.0 permits code reuse with license/NOTICE preservation. This
proposal intentionally copies **design language only**; any future component port
must carry NOTICE attribution.

---

## Concept model

```
Company     = AgentNexus server (identities, memory, routines, track record, billing)
Employee    = bot: agent YAML (persona + harness binding + policies) + platform-side
              memory + routine bindings + channel bindings. Logical, portable.
Workstation = a machine running the runner daemon, or a cloud sandbox. Pure compute.
Binding     = "this bot sits at that workstation" — mutable management state.
```

Three calibrations that prevent common misreadings:

1. **A CLI is not a bot.** The CLI process is a *shift*: spawned per session/routine
   fire, destroyed when the session ends, carries no cross-session state. Memory is
   written back to the platform; re-injected next shift. Bot lifetimes and process
   lifetimes must be separate entity types (agent registry vs runner process table).
2. **Sub-agents spawned inside one CLI process are not bots** — they are temporary
   workers within a shift. Promotion to teammate = giving them their own agent YAML,
   memory, and routine bindings.
3. **"Persistent" in Phase 3 refers to the workstation, not the process.** Login
   state, installed toolchains, and long-running services live on the machine; the
   bot's CLI still starts and stops per shift.

Why employee/workstation separation beats Rakazo's "bot owns a computer": bot state
here = platform memory + git artifacts (both machine-independent), so a bot migrates
between workstations with full continuity; Rakazo's bots degrade when their machine
is lost. For coding work, git-as-state is also the cheaper model — resident machines
are only essential for login-state / resident-service scenarios.

---

## Existing foundations (audited)

| Rakazo concept | AgentNexus counterpart | Status |
|---|---|---|
| Bot definition | Agent YAML — `examples/debby/`, `examples/polly/` already demonstrate identity + harness binding + multi-harness sub-agents + `sys_session_send`/inbox async collaboration + `blast_radius` guardrails | **Working exemplars** |
| Routines | `agentnexus/entities/scheduled_task.py` — `scheduled_tasks`/`scheduled_task_runs`, `/v1/scheduled-tasks`, `sys_scheduled_task_*` tools; rrule triggers; per-task `model_override`/`permission_mode`/`cost_budget`; fire results bound to conversation (`last_run_conversation_id`). Product name "Automations" | **Shipped**; `execution_target` limited to `"connected_host"` (`scheduled_task.py:91`) |
| Channels | `integrations/slack/` — Socket Mode, thread = session, per-user auth, DM/@mention/channels | **Slack only** |
| Memory | `tools/builtins/hindsight.py` | **Opt-in, not persistent** |
| Bot-to-bot delegation | `coordination/workflow_engine.py` + `workflow_scheduler.py` + A2A bus | **Chain exists; 4×P1 open** (see Phase 2) |
| Remote CLI execution | Runner daemon (FastAPI + WebSocket, `runner/app.py`) on the target machine; server relays via tunnel (`app.py:432`); runner spawns the harness locally (`_auto_create_claude_terminal`) — a daemon model, not SSH exec | **Shipped** |
| Machine lifecycle | `server/managed_hosts.py` — launch/terminate/`resume_managed_host`, "wake in place" gating (`:3066`, `:3118`) | **Shipped; lacks owner semantics** |
| Governance | Three-level policies (server/agent/session), `cost_budget`, approval flows | **Shipped, stronger than Rakazo** |
| Launcher extensibility | `claude_launcher.py` — `AGENTNEXUS_CLAUDE_LAUNCHER` env + setuptools entry-point wraps the CLI command (auth/telemetry/cost wrappers) without code changes | **Shipped** |

Gaps: persistent memory injection, channels beyond Slack, bot roster/management UI,
per-bot workstation binding, voice, non-Slack channel adapters.

---

## Phase 1 — Resident teammates (minimal new code)

Reframe: **"turn Debby from a character into a resident employee."** Agent YAML
format unchanged; only the hosting model changes.

1. **Registry residency** — agent YAMLs registered in a persistent team roster
   (server-side), instead of living only inside a session run. New `Teammates` page
   aggregates roster + scheduled tasks + channel bindings + recent activity
   (`last_run_conversation_id` deep-links to the last session).
2. **Memory persistence** — per-bot memory via hindsight: each shift ends with a
   memory write-back; each fire/session start injects a digest. (Decision item on
   strategy: continued long session vs per-fire digest injection.)
3. **Routine binding** — scheduled tasks reference a roster bot instead of a bare
   prompt; fire results still bind to conversations.
4. **Channel binding** — per-bot Slack target ("this bot staffs #eng-standup"),
   building on the existing Socket Mode integration.

**Demo milestone**: Debby registered as a resident bot + one routine — "every
weekday 9:00, fan the daily question to the Claude and GPT partners, synthesize,
post to Slack." Low implementation cost, complete product story, validates the
"teammate feel" before any heavier investment.

## Phase 2 — Delegation at scale (gated)

**Precondition: fix the 4 coordination P1 blockers first** (per the earlier
acceptance review: dangling outbox entries, dead `delivery_state` code,
reconciliation not probing real state, a read endpoint missing ACL). The bot team's
delegation chain sits on this stack; per the project's serial-task discipline, these
land before any Phase 2 feature work.

Then: team-lead bots delegating to heterogeneous harness workers, cross-vendor
review (Polly mode) as a standing pattern, promotion of recurring in-session
sub-agents into full teammates.

## Phase 3 — Workstation tier (`persistent_sandbox`)

Add a third `execution_target` value alongside `connected_host`: a managed host
**bound to `agent_id`**. Routines and channel messages resume the same machine.

- Semantics: `managed_hosts` gains owner binding; lifecycle policies (keep-warm,
  idle sleep reusing the wake gating, idle reclamation, snapshots).
- Governance: bot workstations inherit `cost_budget` + three-level policies +
  egress proxy; ACL — only the bot itself and its owner may enter the machine.
- **Workstation onboarding as product**: `pip install` the runner onto any
  user machine (NAS, spare workstation) → 5-minute "register a workstation" flow.
  This literalizes "AI teammates you actually own" **on the user's own hardware** —
  something Rakazo's hosted-compute model cannot offer, on foundations that already
  ship.
- UI: workstation panel (machine status, screen preview, Take control, Routines).
  Web UI has no desktop-stream (VNC-like) surface today and `BrowseLocationBar` is a
  filesystem browser, not a web browser — graphical desktop access is explicitly
  out of scope for now; weak need in coding scenarios.

Sequence note: Phase 1 (memory + routines, git-as-state) should validate the product
value of teammates before investing in resident machines; the workstation tier is
essential only for login-state / resident-service scenarios.

---

## UI direction

Baseline: Tailwind v4 + shadcn; all tokens centralized in
`themePalettes.generated.css`, generated by `scripts/generate-theme-palettes.mjs`,
switched via `data-theme`. Restyling = adding a generator preset + minor
font/radius variables; components untouched. One change propagates to
Electron/Android/iOS (shared React UI).

- **`rakazo-dark` preset**: deep charcoal bg (`#0D0D0D`/`#1B1B1B`), white user
  bubbles (`#F5F5F4`, 16px radius), green activity checks (`#4ADE80`), per-harness
  accent colors for badges (Claude `#D97757`, Codex `#60A5FA`, Cursor `#A78BFA`).
  Geist Mono already in the font stack — zero added font weight.
- **Screen A — Teammates roster page** (new): sidebar nav entry (like TasksPage for
  Automations); rows = avatar dot + name + harness badge + last-activity preview +
  status; detail panel = bindings (channel/sandbox/routines/policy) + memory count
  with view/edit entry. Low-frequency management surface.
- **Screen B — Session view** (upgraded existing): chat stream gains bot activity
  cards (`✓ Extracted → … ` rows), delegation chips, cross-vendor review quotes,
  and harness badges; left mini-roster for teammate switching; right context panel
  (AgentInspector position) for workstation/routines/memory when applicable. Screen B
  is also the direct landing for Slack messages and routine fires.
- **IA decision item**: standalone roster page (default) vs Rakazo-style
  always-visible sidebar tabs. A third, longer-term option: **task-board-first**
  main view (team graph + kanban), since coding work is task-shaped — see below.
- Preview artifact: `.workbuddy/tmp/rakazo-style-preview.html` (two-screen static
  mockup with the token strip); reference screenshot
  `.workbuddy/tmp/rakazo-hero.png`.

---

## Product directions beyond the baseline

Ranked; ① folds into Phase 3, ③ folds into the UI proposal, ② ④ are new dimensions
needing separate prioritization.

1. **Workstation productization** (strongest bet) — Phase 3 reframed as above.
2. **Cross-harness credit system** — Polly-style cross-vendor review productized
   into accumulated data: finding rates, false-positive rates, per-task-type ×
   harness pass rates → per-bot track record that drives dispatch suggestions
   ("Cursor's teammate passes UI-refactor reviews at 92%"). Unique to a
   harness-meta-scheduler; single-runtime platforms can never accumulate it.
3. **Task-board-first UI** — promote the team graph (SubagentsGraphView) to the
   main view: kanban of delegated tasks across bots; chat becomes the per-task
   detail view. Closer to coding reality than chat-first.
4. **Payroll / ROI report** — monthly per-bot statement from `cost_budget` data:
   spend, delivered commits, P1s caught. Makes "hiring AI employees" accountable.

Not pursuing: voice/calling (low frequency in coding, high cost), general assistant
bots (Rakazo's battleground, no advantage here).

---

## Decision items

1. **Memory strategy**: continued long session per bot (simple, grows unbounded) vs
   per-fire hindsight digest injection (clean, extra hop)? Leaning digest.
2. **Channel priority after Slack**: Telegram vs WhatsApp first?
3. **Phase 2 strictly after coordination P1 fixes?** Leaning yes (serial discipline).
4. **Workstation tier in scope?** Leaning Phase 3, post-validation.
5. **IA choice**: standalone roster page vs sidebar dual-tab vs task-board-first.
6. **Credit system & payroll**: schedule as new epics or defer?

## Risks & open questions

- **Coordination P1s** are hard prerequisites for Phase 2; slippage there delays
  the flagship demo (cross-vendor review as a standing team).
- **Memory quality** determines the teammate feel; hindsight digests need curation
  and a view/edit surface, or bots accumulate noise (Rakazo ships memory management
  UI for this reason — it is on our P1 UI list).
- **Security surface grows with residency**: persistent memory and channels give
  bots a durable identity worth attacking. Adopt Rakazo's fail-closed patterns
  (escape all bot-rendered content; audit XSS in the web UI's agent-message
  rendering; per-bot policy defaults deny-by-default).
- **Cost control**: routines + resident workstations can burn budget unattended;
  per-bot `cost_budget` defaults and payroll reporting should land with Phase 1,
  not after.
- **Windows native parity** remains degraded (no bwrap/sandboxing, no tmux
  wrapping) — relevant if workstations include Windows machines.
