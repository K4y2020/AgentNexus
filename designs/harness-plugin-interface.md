# Harness Plugin Interface

AgentNexus now discovers optional harness support through Python entry points.
Core `agentnexus` ships the built-in harness contribution. A separate package, for
example `agentnexus-kimi`, can add harness ids, aliases, runner modules, install
metadata, model environment plumbing, and picker labels without adding that
harness to the default install.

The goal is:

- `pip install agentnexus` gives only core harnesses.
- `pip install agentnexus-kimi` adds Kimi support to the same `omni` CLI and
  server process.
- Core can still produce a targeted error for known optional harness ids:
  install `agentnexus-kimi`.

## Package Contract

An optional harness package declares an entry point in the
`agentnexus.community.harness` group. Community harness implementation modules
must also live under the `agentnexus.community.harness.*` namespace; core rejects
plugins that try to register flat packages or override builtin harness names.

```toml
[project]
name = "agentnexus-foo"
dependencies = [
  "agentnexus==0.3.0.dev0",
]

[project.entry-points."agentnexus.community.harness"]
foo = "agentnexus.community.harness.foo.plugin:get_contribution"
```

For local sibling checkouts, keep the package dependency normal and point uv at
the local core checkout:

```toml
[tool.uv.sources]
agentnexus = { path = "../agentnexus-oss-2", editable = true }
```

If the plugin lives inside the core repo, the relative path should point back to
the repo root. If it moves to a sibling repo, update the path. A bad path is why
uv may try to build `agentnexus @ file:///Users/<user>`.

## Registry Types

The public interface lives in `agentnexus.harness_plugins`:

```python
from agentnexus.harness_plugins import HarnessContribution
from agentnexus.harness_install_spec import HarnessInstallSpec
```

`HarnessInstallSpec` intentionally lives outside `agentnexus.onboarding` so a
plugin can be imported during entry-point discovery without pulling in the
provider/onboarding stack and creating import cycles.

### `HarnessContribution`

Each plugin exports a `get_contribution()` function returning
`HarnessContribution`.

```python
def get_contribution() -> HarnessContribution:
    return HarnessContribution(
        name="agentnexus-foo",
        valid_harnesses=frozenset({"foo"}),
        harness_modules={
            "foo": "agentnexus.community.harness.foo.inner.foo_harness",
        },
        aliases={
            "foo-code": "foo",
        },
        install_specs={
            "foo": HarnessInstallSpec(
                "Foo",
                "foo",
                package=None,
                install_hint="curl -fsSL https://foo.example/install.sh | bash",
                login_args=("login",),
                logout_args=("logout",),
            ),
        },
        harness_install_keys={
            "foo": "foo",
            "foo-code": "foo",
        },
        missing_install_package={
            "foo": "agentnexus-foo",
            "foo-code": "agentnexus-foo",
        },
        harness_labels={"foo": "Foo"},
    )
```

## Field Semantics

`valid_harnesses`
: Canonical harness ids accepted by spec validation once the plugin is
installed.

`harness_modules`
: Maps each canonical harness id to the subprocess module that creates the
harness app. `agentnexus.runtime.harnesses` merges these into `_HARNESS_MODULES`.

`aliases`
: User-facing spellings canonicalized by `agentnexus.harness_aliases`, for example
`foo-code -> foo`.

`install_specs`
: Plugin-provided CLI install/auth metadata, keyed by install key. Use
`HarnessInstallSpec` from `agentnexus.harness_install_spec`.

`harness_install_keys`
: Maps harness ids and aliases to an `install_specs` key. Readiness and
preflight checks use this to decide which CLI binary a harness requires.

`model_env_keys`
: Maps harness id to an env var name used by launcher/spec generation for model
override plumbing.

`spawn_env_builders`
: Maps headless harness id to a callable import path. The runner calls this to
build per-spawn environment variables from the agent spec.

`missing_install_package`
: Maps known optional harness spellings to the package that provides them. Core
uses this even when the plugin is not installed so validation and process-manager
errors can say `pip install agentnexus-foo`.

`harness_labels`
: Maps canonical harness ids to display labels returned by `GET /v1/harnesses`
and merged into web picker surfaces.

## Runtime Flow

1. Python loads installed entry points in `agentnexus.community.harness`.
2. `agentnexus.harness_plugins.plugin_state()` merges the built-in contribution
   with each plugin contribution.
3. Spec validation checks `accepted_harnesses()` and uses
   `missing_install_package()` for known optional harness hints.
4. `agentnexus.runtime.harnesses` registers `harness_modules()`.
5. Runner launch paths consult `spawn_env_builders()` for contributed headless
   harnesses.
6. Host readiness uses `harness_install_keys()` and `install_specs()` to gate
   CLI-backed contributed harnesses on their binary.
7. The server exposes `GET /v1/harnesses` from `harness_catalog()`.
8. The web UI merges `/v1/harnesses` into harness picker surfaces.

## Minimal Headless Harness Checklist

For a non-native harness:

- Create a separate package, for example `agentnexus-foo`.
- Add the `agentnexus.community.harness` entry point.
- Implement `get_contribution()`.
- Fill `valid_harnesses`, `harness_modules`, and `aliases`.
- Add `install_specs` and `harness_install_keys` if the harness needs a CLI.
- Add `spawn_env_builders` if the harness needs spec-derived env vars.
- Add `missing_install_package` entries in core if the harness id should produce
  a targeted install hint before the plugin is installed.
- Move harness implementation modules into the plugin package.
- Remove the harness id and module from the built-in contribution.

## Native TUI Harnesses

Community native terminal harnesses are not supported by this interface yet.
Core native harnesses still use internal registry metadata, but the runner,
chat-resume, CLI-command, interrupt/stop, and built-in agent seeding paths are
not pluggable. Community plugins that set `native_harnesses` or `native_agents`
are rejected at load time until those lifecycle hooks are wired end to end.

## Import Rules

Entry-point loading happens early and can happen while other core modules are
still initializing. Plugin `plugin.py` should keep top-level imports light:

- safe: `agentnexus.harness_plugins`, `agentnexus.harness_install_spec`, constants,
  stdlib;
- risky: `agentnexus.onboarding.*`, `agentnexus.cli`, server modules, runner modules,
  or anything that imports `agentnexus.harness_aliases`.

Put heavy imports inside the callable that needs them. For example, a spawn-env
builder may import provider/runtime helpers inside `build_spawn_env()`, but
`get_contribution()` should not need onboarding.

## Local Demo Commands

Sibling checkout demo:

```bash
cd /path/to/agentnexus-oss-2
uv pip install -e .
uv pip install -e ../agentnexus-foo

uv run python -c "from agentnexus.harness_plugins import valid_harnesses; print('foo' in valid_harnesses())"
uv run python -c "from agentnexus.runtime.harnesses import _HARNESS_MODULES; print(_HARNESS_MODULES['foo'])"
```

If the plugin dependency still points at a published or wrong local `agentnexus`,
use the sibling source override in the plugin `pyproject.toml`:

```toml
[tool.uv.sources]
agentnexus = { path = "../agentnexus-oss-2", editable = true }
```

For published packages, remove local source overrides and publish both
distributions with compatible versions.

## Tests To Add For Each Split Harness

- Core registry excludes the optional harness by default.
- Core validation/error messages suggest the optional package.
- Installing or faking the entry point adds `valid_harnesses`, aliases, install
  specs, and harness modules.
- Readiness/setup tests isolate core-only behavior by stubbing entry-point
  discovery when the optional package is installed in the dev environment.
- Two community plugins cannot claim the same harness spelling, alias, or
  install key.
