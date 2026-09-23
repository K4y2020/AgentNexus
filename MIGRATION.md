# Migrating from Omnigent to AgentNexus

AgentNexus is the current Python distribution, source package, CLI and product name.
The source repository is https://github.com/K4y2020/AgentNexus. This guide describes
compatibility implemented in this checkout; it does not imply that an arbitrary
new Docker image, package version or external deployment has been published.

## Commands and installation

Use `agentnexus` or its short alias `nexus` for new scripts:

```bash
agentnexus --help
agentnexus run
agentnexus server --host 127.0.0.1 --port 6767
agentnexus host --server http://127.0.0.1:6767
nexus --version
```

The installed console aliases `omnigent` and `omni` still invoke the same CLI.
They are deprecated and scheduled for removal in **2.0**. A console alias does not
provide an old Python import package: update Python imports and `python -m`
commands to `agentnexus`, for example:

```python
from agentnexus.spec import AgentSpec
```

For a source installation:

```bash
uv tool install --python 3.12 git+https://github.com/K4y2020/AgentNexus.git
```

Preserve any extras used by your installation. A git checkout uses its checkout
update workflow; a wheel installation can use `agentnexus upgrade --check` and
`agentnexus upgrade`. Installing the renamed distribution and updating an old
installation are distinct operations. Do not remove the working installation
before the replacement has been built successfully.

## Environment variables

Only the `AGENTNEXUS_` prefix is read. Environment variables that use a
pre-rename prefix are ignored, so rename them in shell profiles, `.env` files,
CI secrets and deployment configs before upgrading.

For a Windows development shell:

```powershell
$env:AGENTNEXUS_URL = 'http://127.0.0.1:6767'
# For an authenticated development proxy, set AGENTNEXUS_AUTH_TOKEN separately.
```

The Vite proxy uses `AGENTNEXUS_URL` and `AGENTNEXUS_AUTH_TOKEN`. The desktop shell
uses `AGENTNEXUS_CONFIG_HOME`, `AGENTNEXUS_DATA_DIR` and, in development only,
`AGENTNEXUS_DESKTOP_VERSION_OVERRIDE`. DMG release builds use
`AGENTNEXUS_NOTARIZE_DMG`. Runner children receive the canonical `AGENTNEXUS=1`
session marker and the deprecated `OMNIGENT=1` marker until 2.0. Control-plane
secrets are stripped from child environments.

## Configuration and existing sessions

The default config file is `~/.agentnexus/config.yaml`. The default local database
and state live directly under `~/.agentnexus`, including `chat.db`.
`AGENTNEXUS_CONFIG_HOME` moves the config-file location; `AGENTNEXUS_DATA_DIR` moves
the local data/state location. They are separate settings.

Pre-rename state directories are neither read nor migrated automatically. To
keep existing config, sessions and tokens, stop any running host or server,
back up your previous state directory, and copy or move it to `~/.agentnexus`
yourself before the first AgentNexus run. Do not merge databases by copying
files over a running server.

## Stored references and integration boundaries

The canonical server manifest is `/.well-known/agentnexus.json`; the legacy
`/.well-known/omnigent.json` endpoint remains readable until 2.0. Desktop deep
links accept `agentnexus://` and the deprecated `omnigent://` scheme.

New workspace URL discovery uses `/api/2.0/agentnexus` and `/agentnexus`. Explicit
legacy API mounts `/api/2.0/omnigent` and `/api/2.0/omnigents` map to the legacy
`/omnigent` UI mount, preserving the selected deployment and workspace query.
Renaming this code does not rename a remote deployment; configure its actual URL.

New tool relay prompts use `mcp__agentnexus__`. Older relay names and generated
hook flags remain accepted at compatibility boundaries until 2.0. Built-in
platform skills are packaged as `build-agentnexus` and `agentnexus-knowledge`;
old skill calls resolve to those skills until 2.0. This does not rename arbitrary
user-authored skills.

ACP config writes `agentnexus_mcp` and reads legacy `omnigent_mcp` when the new key
is absent. Existing Python ACP constructor keywords remain compatible until 2.0.
Installer-ledger readers recognize both exact AgentNexus and Omnigent profile
markers, so upgrading does not strand an old installer-owned PATH block.

Persistent identifiers need separate migrations. Internal tmux options, stored
approval/SDK metadata, existing dev-pod data and cache paths, Cloudflare resources,
remote image names, signing identifiers and workflow filenames used by trusted
publishers are not renamed by blind text replacement. Their old spelling alone
is not a broken import or an incomplete user-visible rename.

## Building and verifying a checkout

The Python source directory is `agentnexus/`. The Web build outputs to
`agentnexus/server/static/web-ui`; the Dockerfile copies that same location.
The VS Code workspace package is `agentnexus-vscode`.

```bash
python scripts/check_agentnexus_rename.py
uv run --no-sync pytest -q tests/cli/test_update_check.py tests/cli/test_upgrade_command.py
pnpm --filter web run type-check
pnpm --filter agentnexus-vscode run type-check
pnpm --filter web run build
```

With Docker available, build a local image from the checkout rather than guessing
a renamed registry tag:

```bash
docker build -f deploy/docker/Dockerfile -t agentnexus-server:local .
```

After starting the server, check `/.well-known/agentnexus.json`, open the desktop
connection page, and confirm its connection controls, Find window and return
banner work. A saved legacy link should still open the intended deployment.
