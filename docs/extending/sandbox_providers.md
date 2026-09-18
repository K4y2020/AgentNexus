# Sandbox providers

AgentNexus supports running agent hosts in remote sandboxes. Built-in providers
(Modal, Daytona, Blaxel, CoreWeave Sandbox, E2B, Islo, OpenShell, Boxlite,
Kubernetes)
ship with the core package. Third-party packages can add new providers through
the `agentnexus.sandbox_providers` entrypoint group.

## How it works

Each sandbox provider implements the
[`SandboxLifecycle`](../../agentnexus/onboarding/sandboxes/base.py) interface.
Providers that exec into a running sandbox (Modal, Daytona, …) inherit
`ExecModelHostLauncher`, which provides a default `start_host` that probes
`$HOME`, creates a workspace, clones a repo, and backgrounds `agentnexus host`.
Providers whose sandbox boots running the host directly (Kubernetes) inherit
`SandboxHostLauncher` and override `start_host` to build the infrastructure
manifest instead.

## Creating a community sandbox provider

### 1. Implement the launcher

```python
# agentnexus_community_sandbox_acme/launcher.py
from agentnexus.onboarding.sandboxes.base import ExecModelHostLauncher
from agentnexus.onboarding.sandboxes.types import SandboxCapabilities


class AcmeSandboxLauncher(ExecModelHostLauncher):
    provider = "acme"

    @property
    def capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(
            cli_bootstrap=True,
            managed_launch=True,
            local_port_forward=False,
            resume_stopped=False,
            programmatic_terminate=True,
            file_copy=True,
            streaming_exec=False,
            foreground_exec=True,
        )

    def prepare(self) -> None:
        # Verify credentials / tooling
        ...

    def provision(self, name: str) -> str:
        # Create the sandbox, return its id
        ...

    def run(self, sandbox_id: str, command: str, *, check: bool = True):
        # Execute a command in the sandbox
        ...

    def put(self, sandbox_id: str, local_path, remote_path: str) -> None:
        # Copy a file into the sandbox
        ...

    def terminate(self, sandbox_id: str) -> None:
        # Delete the sandbox
        ...
```

### 2. Register the contribution

```python
# agentnexus_community_sandbox_acme/plugin.py
from agentnexus.onboarding.sandboxes.registry import (
    SandboxProviderContribution,
    SandboxProviderMetadata,
)

def get_contribution() -> SandboxProviderContribution:
    return SandboxProviderContribution(
        name="agentnexus-acme",
        providers={
            "acme": SandboxProviderMetadata(
                name="acme",
                launcher_class="agentnexus.community.sandbox.acme:AcmeSandboxLauncher",
            )
        },
    )
```

### 3. Declare the entrypoint in `pyproject.toml`

```toml
[project.entry-points."agentnexus.sandbox_providers"]
acme = "agentnexus_community_sandbox_acme.plugin:get_contribution"
```

### 4. Install and use

```bash
pip install agentnexus-community-sandbox-acme
agentnexus sandbox create --provider acme --server https://your-host
```

## Namespace requirement

Community provider code **must** live under the `agentnexus.community.sandbox`
namespace package. This is enforced by the registry's validation — a
contribution whose `launcher_class` points outside this namespace is rejected
with a clear error.

To use the namespace, create a package under `agentnexus/community/sandbox/` in
your distribution:

```
agentnexus/
  community/
    sandbox/
      acme/
        __init__.py
        launcher.py
```

The `agentnexus.community.sandbox` namespace package is already set up by core
AgentNexus using `pkgutil.extend_path`, so your package's files are discovered
automatically when installed.

## Server-managed sandboxes

To use a community provider for server-managed sessions, add it to the
server's `sandbox:` config:

```yaml
sandbox:
  provider: acme
  server_url: https://your-host
```

The server resolves the provider through the registry and calls
`prepare()` → `provision()` → `start_host()` → wait for online registration.
Each managed sandbox authenticates back with a server-minted per-launch token.

## Provider capabilities

Providers declare their feature set via a `capabilities` property returning
`SandboxCapabilities`:

| Capability | Description |
|---|---|
| `cli_bootstrap` | Supports `agentnexus sandbox create` / `connect` |
| `managed_launch` | Supports server-managed `host_type="managed"` sessions |
| `local_port_forward` | Can bridge a local port into the sandbox |
| `resume_stopped` | Can resume a stopped sandbox in place |
| `programmatic_terminate` | Can terminate a sandbox programmatically |
| `file_copy` | Supports copying files into the sandbox |
| `streaming_exec` | Supports streaming process execution |
| `foreground_exec` | Supports a foreground exec with inherited stdio |
