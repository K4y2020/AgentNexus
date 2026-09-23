# AgentNexus Slack Bot

Slack Socket Mode bot that maps one Slack thread to one AgentNexus session. The
bot talks to **one** AgentNexus server, set by the operator via
`AGENTNEXUS_SERVER_URL` — Slack users never enter a URL, so the bot only ever
issues requests to that fixed host. Each user still authenticates as their own
AgentNexus identity against it.

Existing `/omnigent` Slack commands remain supported until AgentNexus 2.0.
Configuration is read only from `AGENTNEXUS_*` environment variables, and the
default database is `agentnexus_slack.sqlite3` under the data directory; rename
or copy a pre-rename database there to keep thread mappings and tokens.

> This README is the operator/user guide (setup, scopes, running, auth). For the
> user-facing behaviour contract (setup, DM, channels, error handling), see
> **[docs/CUJS.md](docs/CUJS.md)**; for the Databricks-App auth design, see
> **[docs/DATABRICKS_APP_WEBAUTH_DESIGN.md](docs/DATABRICKS_APP_WEBAUTH_DESIGN.md)**.

## Setup

1. Create a Slack app with Socket Mode **and** Interactivity enabled (Socket
  Mode delivers the interactive button/modal payloads — no request URL needed).
2. Add the OAuth scopes and event subscriptions listed under **Required scopes**
   below.
3. Add a slash command `/agentnexus` (Features → Slash Commands). In Socket Mode
  the request URL is ignored, so any placeholder works.
4. Install the app into the workspace.
5. Set the two Slack tokens (`AGENTNEXUS_SLACK_BOT_TOKEN`,
   `AGENTNEXUS_SLACK_APP_TOKEN`) and your AgentNexus server URL
   (`AGENTNEXUS_SERVER_URL`) as **environment variables**. If your server sets
   `AGENTNEXUS_DEVICE_CLIENT_SECRET`, set the same value here so the bot is
   accepted as an authorized device-grant client. See **Configuration** below
   for how the bot reads config.
6. Run the bot — see **Running the bot** below.

## Required scopes

The bot uses two tokens, each carrying different scopes.

### Bot token scopes (`AGENTNEXUS_SLACK_BOT_TOKEN`, `xoxb-…`)

Add these under **OAuth & Permissions → Scopes → Bot Token Scopes**. All are
required for the bot's core behaviour:

| Scope | Why it's needed |
| --- | --- |
| `app_mentions:read` | Receive `app_mention` events — the only way the bot joins a channel thread. |
| `chat:write` | Post, delete, and stream replies (`chat.postMessage`, `chat.delete`, `chat.startStream`), including ephemeral setup nudges (`chat.postEphemeral`). |
| `im:write` | Open a DM with the user (`conversations.open`) to send the setup button and logout confirmation. |
| `im:history` | Read direct messages. DMs are a first-class entry point and do **not** fire `app_mention`, so without this the bot can't respond in DMs. |
| `commands` | Register and receive the `/agentnexus` slash command. |
| `team:read` | Read the workspace name (`team.info`) to label the delegated-login request. |
| `users:read`, `users:read.email` | Read the user's email (`users.info`) — **required only for Databricks web-auth mode**, where it's signed into the enrollment link and matched against the OAuth-authenticated email to bind the token to the right person. Omit for `accounts`/`oidc` mode. |

**Channel history — add per channel type where the bot will run.** These back
the plain-`message` event; add only the ones matching where you'll use the bot:

| Scope | Channel type |
| --- | --- |
| `channels:history` | Public channels |
| `groups:history` | Private channels |
| `mpim:history` | Group DMs |

If you only use the bot via DMs and channel `@mention`s, `im:history` alone is
enough and the three channel-history scopes can be omitted.

### App-level token scope (`AGENTNEXUS_SLACK_APP_TOKEN`, `xapp-…`)

| Scope | Why it's needed |
| --- | --- |
| `connections:write` | Open the Socket Mode connection. Socket Mode fails to connect without it. |

### Event subscriptions

Under **Event Subscriptions → Subscribe to bot events**, add:

- `app_mention`
- `message.im` (DMs)
- `message.channels` / `message.groups` / `message.mpim` — only for the channel
  types whose history scope you added above.



## Running the bot

With the `agentnexus` CLI installed, the Slack bot is managed as a background daemon:

```bash
agentnexus integration slack              # run in the foreground (Ctrl-C to stop)
agentnexus integration slack --background # run in the background (detached)
agentnexus integration slack status       # is the background bot running?
agentnexus integration slack stop         # stop the background bot
agentnexus integration slack logs         # print the background bot's log path
agentnexus integration slack logs -f      # follow the log (like tail -f)
```

`agentnexus integration slack --background` spawns a detached daemon and returns
immediately; `status`/`stop`/`logs` manage it. Running `--background` again
while it's already up is a no-op that reports the existing process.

### Configuration

All configuration (the two Slack tokens, `AGENTNEXUS_SERVER_URL`, and the
optional `AGENTNEXUS_DEVICE_CLIENT_SECRET` / `AGENTNEXUS_SLACK_TOKEN_ENCRYPTION_KEY`)
comes from **real environment variables** — the bot does **not** read a `.env`
file itself. For local dev, either export the vars, or launch under a tool that
injects a `.env` — e.g. `uv run --env-file .env agentnexus integration slack`, or
`export $(grep -v '^#' .env | xargs)` before running. In production the
Docker / Databricks deploy sets them directly. `.env.example` documents the
full set of variables to copy from.

The bot lives in the separate `agentnexus-slack` package, which must be installed
**in the same environment as** `agentnexus` for the `agentnexus integration slack` commands
to find it. Install it as the `slack` extra of agentnexus:

```bash
uv tool install "agentnexus[slack]"     # or, from a source checkout: uv sync --extra slack
```

Set `LOG_LEVEL=DEBUG` in the environment when diagnosing why Slack events are not producing replies.

## Per-user setup flow

The first time a user interacts with the bot (a channel `@mention` or a DM)
without having configured, the bot DMs them a **Set up AgentNexus** button and,
for channel mentions, drops an ephemeral pointer in the thread.

The button opens a modal that connects to the operator-configured server (no
URL to enter):

1. The bot validates connectivity to `AGENTNEXUS_SERVER_URL`. If the server has
  authentication enabled, the modal shows a login link; once the user approves
   it in their browser the **same modal advances automatically** (see
   **Authentication** below). If the server has no online host, setup shows how
   to start one (see below) instead of continuing — a session needs a host to
   run on.
2. Pick the **agent** and **host** (both required) from menus populated by the
  server, and set the **workspace path** — an absolute directory on the host
   where each session's runner starts. It defaults to the selected host's home
   directory (resolved from the server), falling back to the bot's working
   directory only if the host can't be probed.

The choice is saved per `(Slack workspace, user)`. After that, mentioning the
bot (or DMing it) starts a session on the configured server.

## Authentication

For AgentNexus servers with authentication enabled, each Slack user logs in with
their own AgentNexus identity — no AgentNexus credential ever passes through Slack.
Login happens inside the single `/agentnexus` configuration modal, not a separate
command.

The bot **auto-detects the server's auth mode** (an unauthenticated `GET /v1/me`, exactly as the `agentnexus login` CLI does) and picks the matching flow:

- `accounts` **mode** → **OAuth 2.0 Device Authorization Grant** (RFC 8628).
The modal shows a one-click login link (code prefilled) and the short code to
confirm; the user opens the link and approves a consent page in their browser.
(The consent page **forces a fresh password entry** before it will approve —
even if the user is already signed in — so a link the user didn't personally
start can't be approved by reflex.) The server issues a short-lived,
session-scoped delegated
token plus a rotating refresh token, so the bot silently refreshes and the
token can't reach admin endpoints. **The AgentNexus server must have the device
grant enabled** (`AGENTNEXUS_DEVICE_GRANT_ENABLED=1` — it is default-off);
otherwise the `/oauth/*` routes are absent and accounts-mode login can't
complete. If the server sets `AGENTNEXUS_DEVICE_CLIENT_SECRET`, set the same
value as the bot's `AGENTNEXUS_DEVICE_CLIENT_SECRET` so only this authorized
socket server can drive the device flow.
- `oidc` **mode** → the server's **cli-login ticket flow** (`/auth/cli-login` +
`/auth/cli-poll`). The modal shows a login link; the user signs in at *your
IdP* in their browser. The server hands back its session JWT — the same token
a browser session gets. There is **no device grant and no refresh token**: the
session lasts its normal TTL (default 8h), after which the user logs in again.
- `header` **/ proxy mode** → identity is asserted by a trusted upstream proxy
header (e.g. `X-Forwarded-Email`), so the server mints no token and exposes no
per-user login the auto-detect flow can drive. Two options:
  - **Databricks Apps** (the common case): set
    `AGENTNEXUS_SLACK_SERVER_AUTH=databricks` and the bot enrolls each user
    through a web page it serves as its own Databricks App — see
    [Databricks Apps web-auth](#databricks-apps-web-auth) below.
  - Otherwise run the server in `accounts`/`oidc` mode, or place the bot behind
    the same identity proxy.

Either way the flow is the same from Slack's side:

1. During setup, when the entered server requires authentication, the modal
  shows a login link and waits.
2. The user completes login in their own browser (consent page, or your IdP).
3. The bot stores the resulting token **encrypted at rest** and attaches it on
  that user's behalf.
4. The **same modal advances automatically** to the agent / host / workspace
  picker as the now-authenticated identity — no DM, no re-running the command.

The bot reads no auth-mode config itself; the AgentNexus server's own
`AGENTNEXUS_OIDC_*` / `AGENTNEXUS_AUTH_*` env vars decide its mode (see the server's
`[deploy/README.md](../../deploy/README.md#auth)`).

Set `AGENTNEXUS_SLACK_TOKEN_ENCRYPTION_KEY` (see `.env.example`) to persist tokens
encrypted at rest; without it tokens are kept in memory only and lost on restart
(users simply re-authenticate) — the integration works either way.

`/agentnexus logout` fully resets you: it revokes your delegated token and clears
all your saved settings (agent, host, workspace, and thread→session mappings).
Run `/agentnexus` afterwards to set up again.

### Resident teammates

Run **`/agentnexus bind`** inside a Slack channel to staff that channel with one
agent: the same agent / host / workspace picker opens, but the result is stored
as a channel binding rather than your personal config. Every new mention thread
in that channel then routes to the bound agent, regardless of who runs the
command or what personal config they have. **`/agentnexus unbind`** removes the
binding and falls back to per-user routing.

When a scheduled task (routine) for a bound agent finishes, the bot posts a
completion summary back to that channel. The bot silently marks pre-existing
runs as seen on startup, so a restart doesn't replay historical history; only
runs that finish after the bot starts are posted.

See `designs/DEVICE_AUTH.md` in the main repo for the full design and
threat model.

### Databricks Apps web-auth

When the AgentNexus server is deployed as a **Databricks App**, it runs in header
mode: the Databricks Apps proxy authenticates every request and injects the
user's identity. A Socket-Mode event carries no such proxy-authenticated
request, so the device/OIDC flows above can't be driven. Instead the bot runs a
**custom U2M OAuth app** (authorization code + PKCE, `offline_access`) via an
enrollment page it serves as its own Databricks App:

1. On `/agentnexus`, the bot looks up the user's email (`users.info`), generates a
   PKCE verifier + single-use nonce, and posts a *Sign in with Databricks* link
   — the workspace `/oidc/v1/authorize` URL whose signed `state` carries that
   email and nonce.
2. The user signs in at the Databricks authorize screen and Databricks redirects
   back to the bot's `GET /auth/callback` with a single-use, PKCE-bound code.
3. The callback consumes the PKCE verifier for the nonce (single-use — a
   replayed redirect is refused) and exchanges the code at `/oidc/v1/token` for
   an **access + refresh** pair, reading the authenticated email from the
   `id_token` (falling back to SCIM `Me`).
4. **Identity binding (confused-deputy guard):** the callback requires the
   OAuth-authenticated email to equal the Slack email in the signed state — so a
   link bound to user A, signed in by victim V, can't store V's token under A.
   Mismatch → refused (HTTP 403).
5. **Confirm before storing:** the GET stores nothing — it shows a consent page
   naming the exact identities being linked ("your AgentNexus `<server>` account
   `<idp-email>` with Slack user `<slack-email>`") and a **Confirm** button. The
   pair is persisted only when the user submits the confirming POST, then the
   setup modal advances automatically. The token is bounded by the OAuth app's
   requested scopes, so it isn't a broad workspace credential.
6. The bot calls the server with the access token; the proxy validates it and
   injects the real `X-Forwarded-Email`, so the server maps the request to the
   user — **no server-side change needed**. On expiry the bot refreshes silently
   via the refresh token; the user signs in once, not hourly.

Enabled with `AGENTNEXUS_SLACK_SERVER_AUTH=databricks` plus the custom OAuth app's
`AGENTNEXUS_SLACK_DATABRICKS_CLIENT_ID` / `AGENTNEXUS_SLACK_DATABRICKS_CLIENT_SECRET`
and a `AGENTNEXUS_SLACK_DATABRICKS_STATE_SECRET` (see `.env.example`). To deploy
the bot as its own Databricks App, see
[`deploy/databricks/README.md`](deploy/databricks/README.md); for the full
design and threat model, [`docs/DATABRICKS_APP_WEBAUTH_DESIGN.md`](docs/DATABRICKS_APP_WEBAUTH_DESIGN.md).

Run `/agentnexus` (or `/agentnexus config`) any time to reopen this modal and change
your agent, host, or workspace. The server is fixed by the operator, so there's
no URL to change.

Each new session **launches a fresh runner** on the chosen host rooted at the
configured workspace — the server keeps no standing runners.

If the bot can't reach your server, it replies telling you to run `/agentnexus` to
reconfigure. If no host is online (or your preferred host is offline), it replies
with the command to start one, then reconfigure:

```text
Run this on the machine you want to use, then run /agentnexus:
`agentnexus host --server <your-server-url>`
```



## Usage

Mention the bot with a message to start a session:

```text
@your-bot help me inspect this failure
```

Replies stream in live and render Markdown. Replies in that Slack thread continue
the same AgentNexus session. A channel thread belongs to whoever started it; a
follow-up from a different user gets a private ("Only visible to you") note
pointing them to start their own thread.

When the agent needs you — a tool-call approval or a multiple-choice question —
it appears in the thread as an **Approve / Deny** card or a radio/checkbox
**Submit** form; answer it there (or in the web UI). A request it can't render
with buttons (free-form typed input) links out to the web UI instead.

Send another message while the bot is still replying and it privately tells you
to wait or continue in the web UI; a message to an idle thread just continues the
conversation.

For the full set of user-facing behaviours — setup, DM vs channel routing,
ownership, and error handling — see **[docs/CUJS.md](docs/CUJS.md)**. The
under-the-hood details (streaming, turn-end detection, elicitation handling,
concurrency, ordering) live in the module docstrings and inline comments.

## Development

This integration is a **separate package** (`agentnexus-slack`) with heavy deps
(slack_bolt, aiohttp) kept out of the core `agentnexus` install. It resolves as an
editable path dep of the root `agentnexus` package via the `slack` extra (see
`[tool.uv.sources]` in the root `pyproject.toml`), and shares the root's dev
tooling (Ruff, Pyrefly, pytest) and config rather than carrying its own. Work on it
from the repo-root env:

```bash
# From the repo root — install the Slack capability and contributor tooling:
uv sync --extra slack --group dev
uv run --no-sync agentnexus integration slack
```
