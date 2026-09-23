# Environment Variables

AgentNexus reads configuration only from `AGENTNEXUS_*` environment variables.
Variables that use a pre-rename prefix are ignored: rename them in shell
profiles, `.env` files, Docker / Kubernetes configs and CI secrets before
upgrading.

## Common variables

| Variable | Description |
|----------|-------------|
| `AGENTNEXUS_CONFIG_HOME` | Directory holding `config.yaml` (default: `~/.agentnexus`) |
| `AGENTNEXUS_DATA_DIR` | Local data / state directory (default: `~/.agentnexus`) |
| `AGENTNEXUS_LOG_LEVEL` | Log level (`DEBUG` / `INFO` / `WARNING` / `ERROR`) |
| `AGENTNEXUS_SERVER_URL` | Server URL used by clients and integrations |
| `AGENTNEXUS_AUTH_ENABLED` | Enable server authentication |
| `AGENTNEXUS_RUNNER_ENV_PASSTHROUGH` | Environment variables passed through to runners |
| `AGENTNEXUS_TELEMETRY_ENABLED` | Enable telemetry |
| `AGENTNEXUS_NO_UPDATE_CHECK` | Disable the CLI update check |

Deployment-specific variables (auth providers, OIDC, accounts, artifact
storage) are documented in `deploy/README.md` and `deploy/docker/README.md`.

## Finding variables to rename

```bash
env | grep -i omni
```

Rename any match to the same suffix with the `AGENTNEXUS_` prefix.
