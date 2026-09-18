# AgentNexus — docker-compose stack

Run the server as a self-contained Docker stack on any host: your
laptop, a VPS, an EC2 instance, a home server, anywhere `docker
compose` runs.

The stack:
- `postgres` — persistent DB on a Docker volume
- `agentnexus` — the server image (built from `../Dockerfile`)

Auth is in-process — the server has both header-proxy and native
OIDC modes built in (see [Multi-user mode](#multi-user-mode-oidc)
below). There is no separate auth-proxy container.

## Quickstart (single-user)

```bash
cd deploy/docker
./bootstrap.sh                          # mints POSTGRES_PASSWORD + cookie secret into .env
docker compose up -d
docker compose logs -f agentnexus       # ctrl-c when boot is clean
```

`bootstrap.sh` is idempotent — re-running it leaves already-set secrets
alone. If you prefer to manage `.env` yourself, just `cp .env.example
.env` and edit `POSTGRES_PASSWORD` (and `AGENTNEXUS_OIDC_COOKIE_SECRET`
if you're enabling OIDC) by hand.

Server is on http://localhost:8000. The web UI prints the CLI command
to launch a local runner against it. From your laptop:

```bash
agentnexus run path/to/agent.yaml --server http://localhost:8000
```

Reset everything (drops the DB and the artifact store):

```bash
docker compose down -v
```

## Release features

Release features are deployment-wide and off by default. Enable one or more
with the comma-separated `AGENTNEXUS_FEATURES` variable in `.env`, then recreate
the server container:

```dotenv
AGENTNEXUS_FEATURES=usage_page
```

```bash
docker compose up -d
curl -s http://localhost:8000/v1/info | jq '.features'
```

Known keys and their lifecycle are documented in
[`designs/FEATURE_FLAGS.md`](../../designs/FEATURE_FLAGS.md). Unknown keys fail
server startup so a typo cannot silently produce the wrong rollout. To roll
back, remove the key (or empty the variable), run `docker compose up -d` again,
and reload the web app.

## Multi-user mode (accounts — default)

Built-in accounts auth: no IdP to register, no proxy to host.
This is the default — `docker compose up -d` brings it up with no
extra env wiring. No credentials are auto-generated. On first boot,
when no admin exists yet and none was pre-seeded, the server creates
nothing and prints:

```
→ No admin yet. Open <base_url> to create the first admin account (choose a username + password).
```

You then open the web UI's **Create admin** form (it appears while no
admin exists) and pick your own username + password.

For any deploy reachable through a public domain, also set the
external URL so the printed link and invite links resolve correctly:

```bash
# Add to .env (bootstrap.sh already minted the cookie secret for you):
AGENTNEXUS_ACCOUNTS_BASE_URL=https://agentnexus.example.com

docker compose up -d
docker compose logs agentnexus      # shows the "No admin yet" line with your base URL
```

Once you've created the admin and signed in:

- Click your username in the top-right → **Members** → **Invite member**.
- Share the single-use URL with the teammate; they pick their own
  username and password when they redeem it.
- Sign-out lives in the same account menu.

Headless deploy (CI, Cloud Run, etc.) where you can't reach the
Create-admin form? Pre-seed the admin password so first boot creates
the admin directly:

```bash
AGENTNEXUS_ACCOUNTS_INIT_ADMIN_PASSWORD=<your-strong-password>
```

`AGENTNEXUS_ADMIN_CREDENTIALS_PATH` (set to `/data/admin-credentials`
in `docker-compose.yaml`) anchors the persistent state directory on
the `artifact-data` volume — it survives `docker compose restart` and
is deleted by `docker compose down -v`.

## Multi-user mode (OIDC)

Single-user mode trusts everyone who reaches the port and uses the
identity `"local"` for all requests. For a shared deploy, the server
has native OIDC support — it handles the full
login flow itself (`/auth/login`, `/auth/callback`, `/auth/logout`)
with a signed session cookie. No extra container, no Caddy basic-auth
shim, no oauth2-proxy.

### Walkthrough: GitHub OAuth (easiest to register)

1. **Register the OAuth app.** Go to
   https://github.com/settings/developers → New OAuth App. Set the
   callback to `https://<your-host>/auth/callback` (HTTPS is
   strongly recommended; GitHub permits HTTP for testing but warns).

2. **Mint a cookie secret.** `./bootstrap.sh` already did this on the
   quickstart path — `AGENTNEXUS_OIDC_COOKIE_SECRET` is set in your
   `.env`. If you skipped it, run `openssl rand -hex 32` and paste the
   value yourself.

3. **Edit `.env`:**
   ```bash
   AGENTNEXUS_AUTH_PROVIDER=oidc
   AGENTNEXUS_OIDC_ISSUER=https://github.com
   AGENTNEXUS_OIDC_CLIENT_ID=Iv1.abc123…
   AGENTNEXUS_OIDC_CLIENT_SECRET=…
   AGENTNEXUS_OIDC_REDIRECT_URI=https://agentnexus.example.com/auth/callback
   # AGENTNEXUS_OIDC_COOKIE_SECRET is already set by bootstrap.sh — leave it alone.
   ```

4. **Bring it up.**
   ```bash
   docker compose up -d
   ```

   The server will fail loud at startup if any required OIDC env var
   is missing — check `docker compose logs agentnexus` if it doesn't
   come up.

5. **Visit the URL** → you should be redirected to GitHub to log in,
   then back to the web UI with a `__Host-ap_session` cookie set.

### Walkthrough: Google Workspace (with domain allowlist)

```bash
AGENTNEXUS_AUTH_PROVIDER=oidc
AGENTNEXUS_OIDC_ISSUER=https://accounts.google.com
AGENTNEXUS_OIDC_CLIENT_ID=…apps.googleusercontent.com
AGENTNEXUS_OIDC_CLIENT_SECRET=…
AGENTNEXUS_OIDC_REDIRECT_URI=https://agentnexus.example.com/auth/callback
AGENTNEXUS_OIDC_COOKIE_SECRET=<64-hex-chars>
AGENTNEXUS_OIDC_ALLOWED_DOMAINS=example.com,subsidiary.example.com
```

`ALLOWED_DOMAINS` is critical when the OAuth consent screen is
"External" — without it, any Google account on the planet can log in.

### Generic OIDC (Okta, Auth0, Keycloak, Entra ID)

Any IdP that publishes `/.well-known/openid-configuration` works.
Set `AGENTNEXUS_OIDC_ISSUER` to the base URL; the server fetches
discovery at startup.

```bash
AGENTNEXUS_AUTH_PROVIDER=oidc
AGENTNEXUS_OIDC_ISSUER=https://your-tenant.okta.com
AGENTNEXUS_OIDC_CLIENT_ID=…
AGENTNEXUS_OIDC_CLIENT_SECRET=…
AGENTNEXUS_OIDC_REDIRECT_URI=https://agentnexus.example.com/auth/callback
AGENTNEXUS_OIDC_COOKIE_SECRET=<64-hex-chars>
```

### HTTPS for the callback URL

Most IdPs require HTTPS for non-localhost redirect URIs, and the
session cookie uses the `__Host-` prefix which browsers only
accept over HTTPS. Three options:

1. **Use the bundled Caddy overlay** (easiest — any VPS / EC2 / home
   server with a public domain):

   ```bash
   # In .env:
   AGENTNEXUS_DOMAIN=agentnexus.example.com
   AGENTNEXUS_ACME_EMAIL=you@example.com      # optional, for Let's Encrypt notices

   # Point DNS A/AAAA records at the host, then:
   docker compose -f docker-compose.yaml -f docker-compose.https.yaml up -d
   ```

   Caddy auto-provisions and renews a Let's Encrypt cert; the
   agentnexus container stops being directly exposed and only :80 +
   :443 are published. Requires Docker Compose 2.24+ for the overlay's
   `!reset` directive. See `Caddyfile` for the (3-line) config.

2. **Behind an existing reverse proxy** — point your proxy at
   `agentnexus:8000` over the docker network (or `127.0.0.1:8000`
   from the host). Examples: AWS ALB with ACM cert, Cloudflare in
   "Full" SSL mode, Fly.io / Cloud Run / Render platform certs.

## Header-proxy mode (for deploys behind an existing SSO proxy)

If you already have oauth2-proxy, Databricks Apps, AWS ALB OIDC,
Cloudflare Access, Tailscale Funnel, or any other proxy that injects
an identity header, set `AGENTNEXUS_AUTH_PROVIDER=header`. The
server will reject requests without the header.

```bash
AGENTNEXUS_AUTH_PROVIDER=header
```

The header read is `X-Forwarded-Email` by default. Proxies that use
a different header name set `AGENTNEXUS_AUTH_HEADER` to point the
server at it — for example, Cloudflare Access supplies the
authenticated email in `Cf-Access-Authenticated-User-Email`:

```bash
AGENTNEXUS_AUTH_PROVIDER=header
AGENTNEXUS_AUTH_HEADER=Cf-Access-Authenticated-User-Email
```

Some proxies namespace the value they inject. Google IAP forwards the
email in `X-Goog-Authenticated-User-Email` prefixed with
`accounts.google.com:`; set `AGENTNEXUS_AUTH_HEADER_STRIP_PREFIX` to drop
it and recover the bare email:

```bash
AGENTNEXUS_AUTH_PROVIDER=header
AGENTNEXUS_AUTH_HEADER=X-Goog-Authenticated-User-Email
AGENTNEXUS_AUTH_HEADER_STRIP_PREFIX=accounts.google.com:
```

**Security note:** in this mode the proxy is responsible for
stripping any inbound copy of the identity header from the client
request — otherwise any visitor can spoof an identity. The server
trusts whatever value reaches it.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `POSTGRES_PASSWORD` | *required* | DB password for the bundled Postgres container. |
| `POSTGRES_USER` / `POSTGRES_DB` | `agentnexus` | DB user + database name. |
| `AGENTNEXUS_PORT` | `8000` | Host port the server is published on. |
| `AGENTNEXUS_AUTH_ENABLED` | `1` (in compose) | Master auth switch. `1` → accounts (or oidc if `AGENTNEXUS_OIDC_ISSUER` is set); `0` → single-user local mode (every request is the shared `local` user — local dev only, never shared deploys). |
| `AGENTNEXUS_AUTH_PROVIDER` | unset | Escape hatch to pin a mode explicitly: `header` / `accounts` / `oidc`. Overrides the `AUTH_ENABLED` auto-selection. |
| `AGENTNEXUS_AUTH_HEADER` | `X-Forwarded-Email` | Header-mode only: name of the trusted identity header. Set for proxies that use another name, e.g. `Cf-Access-Authenticated-User-Email` (Cloudflare Access). |
| `AGENTNEXUS_AUTH_HEADER_STRIP_PREFIX` | unset (strip nothing) | Header-mode only: prefix removed from the identity header value. Set to `accounts.google.com:` for Google IAP's `X-Goog-Authenticated-User-Email`. |
| `AGENTNEXUS_OIDC_*` | unset | OIDC config — required in oidc mode (issuer set, or `AUTH_PROVIDER=oidc`). See `.env.example`. |
| `PYPI_INDEX_URL` | `https://pypi.org/simple` | Build-time PyPI index — override only behind a corporate proxy. |

`DATABASE_URL` and `ARTIFACT_DIR` are computed by compose and
injected into the container.

## Host image (`--target host`)

The same Dockerfile publishes a second image: the official AgentNexus
**host** image, which remote sandboxes boot from so they start in
seconds instead of paying an in-sandbox dependency install. It bakes
the full agentnexus install (all three packages + deps, `python` and
`pip` on PATH), `git` (workspaces / worktrees), `tmux` (terminal
sessions spawned by native harnesses), and the coding-harness CLIs —
`claude`, `codex`, `pi`, and `kiro-cli`, with the runtime they need — so
claude-sdk / claude-native / codex / pi / kiro-native agents run in sandboxes
without an in-sandbox install. None of the server-only bits are
included (no SPA bundle, no psycopg, no uvicorn entrypoint).

CI publishes it next to the server image, with the same tag scheme:

- `ghcr.io/agentnexus-ai/agentnexus-host:latest` — tracks main HEAD
  (the default for `agentnexus sandbox create --provider modal`)
- `ghcr.io/agentnexus-ai/agentnexus-host:sha-<short>` — immutable
  per-commit pin
- `ghcr.io/agentnexus-ai/agentnexus-host:vX.Y.Z` — release tags

Build it locally from the repo root:

```bash
docker build -t agentnexus-host:latest --target host \
             -f deploy/docker/Dockerfile .
```

### Using it with the Modal sandbox provider

`agentnexus sandbox create --provider modal` boots sandboxes from
`ghcr.io/agentnexus-ai/agentnexus-host:latest` by default. Your local
checkout's wheels are still built and overlaid on top at create time
(`pip install --force-reinstall --no-deps`), so the sandbox runs
exactly your code — the baked image just supplies the dependency
tree. A checkout that adds a brand-new dependency needs that package
installed manually in the sandbox until the official image rebuilds
with it.

Two environment variables tune the pull:

| Variable | Purpose |
|---|---|
| `AGENTNEXUS_MODAL_HOST_IMAGE` | Override the image ref, e.g. an org-internal copy (`ghcr.io/<your-org>/agentnexus-host:latest`) or a `:sha-<short>` pin. |
| `AGENTNEXUS_MODAL_REGISTRY_SECRET` | Name of a [Modal secret](https://modal.com/secrets) holding registry credentials for private pulls. Create it with keys `REGISTRY_USERNAME` (your registry username) and `REGISTRY_PASSWORD` (for GHCR: a personal access token with `read:packages`). Unset = anonymous pull. |

### Using it with the Daytona sandbox provider

The same host image backs Daytona-managed sessions (server config
`sandbox.provider: daytona`; Daytona is managed-only — there is no
`agentnexus sandbox create --provider daytona` CLI flow). Daytona ingests
the registry image into an internal snapshot on first use (the first
launch from a given image takes minutes; later launches reuse the
snapshot and take seconds). Override the ref with
`AGENTNEXUS_DAYTONA_HOST_IMAGE` or the server config's
`sandbox.daytona.image`. See
[`deploy/daytona/README.md`](../daytona/README.md) for the
full provider guide (credentials, the free-tier egress relay, and
security considerations).

## Related design docs

- `designs/OIDC_AUTH.md` — full native OIDC design
- `designs/SESSIONS_AUTH.md` — `AuthProvider` contract + permission system
