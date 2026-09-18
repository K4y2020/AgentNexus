# Environment Variables Migration Guide

## AgentNexus Environment Variables

AgentNexus uses the `AGENTNEXUS_*` prefix for all environment variables.

### Backward Compatibility

For a limited time (until version 2.0), AgentNexus supports the old `OMNIGENT_*` environment variables with deprecation warnings. This allows existing deployments to continue working without immediate changes.

**Migration Priority**: `AGENTNEXUS_*` variables take precedence over `OMNIGENT_*` variables.

---

## Variable Mapping

### Core Configuration

| Old (Deprecated) | New | Description |
|------------------|-----|-------------|
| `OMNIGENT_CONFIG_HOME` | `AGENTNEXUS_CONFIG_HOME` | Configuration directory (default: `~/.agentnexus/`) |
| `OMNIGENT_DATA_DIR` | `AGENTNEXUS_DATA_DIR` | Data directory (default: `~/.agentnexus/data/`) |
| `OMNIGENT_LOG_LEVEL` | `AGENTNEXUS_LOG_LEVEL` | Log level (DEBUG/INFO/WARNING/ERROR) |
| `OMNIGENT_LOG_DIR` | `AGENTNEXUS_LOG_DIR` | Log directory |

### Server Configuration

| Old (Deprecated) | New | Description |
|------------------|-----|-------------|
| `OMNIGENT_SERVER_URL` | `AGENTNEXUS_SERVER_URL` | Server URL |
| `OMNIGENT_SERVER_PORT` | `AGENTNEXUS_SERVER_PORT` | Server port (default: 6767) |
| `OMNIGENT_SERVER_HOST` | `AGENTNEXUS_SERVER_HOST` | Server host (default: 0.0.0.0) |
| `OMNIGENT_AUTH_ENABLED` | `AGENTNEXUS_AUTH_ENABLED` | Enable authentication |
| `OMNIGENT_DATABASE_URL` | `AGENTNEXUS_DATABASE_URL` | Database connection string |

### Model Configuration

| Old (Deprecated) | New | Description |
|------------------|-----|-------------|
| `OMNIGENT_MODEL_DEFAULT` | `AGENTNEXUS_MODEL_DEFAULT` | Default model |
| `OMNIGENT_API_KEY` | `AGENTNEXUS_API_KEY` | API key |
| `OMNIGENT_BASE_URL` | `AGENTNEXUS_BASE_URL` | API base URL |

### Sandbox & Execution

| Old (Deprecated) | New | Description |
|------------------|-----|-------------|
| `OMNIGENT_SANDBOX_PROVIDER` | `AGENTNEXUS_SANDBOX_PROVIDER` | Sandbox provider (modal/e2b/k8s/...) |
| `OMNIGENT_RUNNER_ENV_PASSTHROUGH` | `AGENTNEXUS_RUNNER_ENV_PASSTHROUGH` | Environment variables to pass to runners |
| `OMNIGENT_WORKSPACE_DIR` | `AGENTNEXUS_WORKSPACE_DIR` | Workspace directory |

### Telemetry & Monitoring

| Old (Deprecated) | New | Description |
|------------------|-----|-------------|
| `OMNIGENT_TELEMETRY_ENABLED` | `AGENTNEXUS_TELEMETRY_ENABLED` | Enable telemetry |
| `OMNIGENT_OTEL_ENDPOINT` | `AGENTNEXUS_OTEL_ENDPOINT` | OpenTelemetry endpoint |
| `OMNIGENT_NO_UPDATE_CHECK` | `AGENTNEXUS_NO_UPDATE_CHECK` | Disable update check |

### Development & Testing

| Old (Deprecated) | New | Description |
|------------------|-----|-------------|
| `OMNIGENT_DEBUG` | `AGENTNEXUS_DEBUG` | Enable debug mode |
| `OMNIGENT_E2E_MODEL_FLOWS` | `AGENTNEXUS_E2E_MODEL_FLOWS` | Enable E2E model flow tests |
| `OMNIGENT_E2E_SMART_ROUTING` | `AGENTNEXUS_E2E_SMART_ROUTING` | Enable smart routing tests |

---

## Migration Steps

### For Development Environments

1. **Update your shell profile** (`~/.bashrc`, `~/.zshrc`, etc.):

   ```bash
   # Old (remove these)
   export OMNIGENT_CONFIG_HOME=~/.omnigent
   export OMNIGENT_LOG_LEVEL=DEBUG
   
   # New (add these)
   export AGENTNEXUS_CONFIG_HOME=~/.agentnexus
   export AGENTNEXUS_LOG_LEVEL=DEBUG
   ```

2. **Reload your shell**:
   ```bash
   source ~/.bashrc  # or ~/.zshrc
   ```

### For Docker Deployments

Update your `docker-compose.yml` or `.env` file:

```yaml
# docker-compose.yml
environment:
  # Old
  # OMNIGENT_AUTH_ENABLED: "1"
  # OMNIGENT_DATABASE_URL: "postgresql://..."
  
  # New
  AGENTNEXUS_AUTH_ENABLED: "1"
  AGENTNEXUS_DATABASE_URL: "postgresql://..."
```

### For Kubernetes Deployments

Update your ConfigMap or Secret:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: agentnexus-config
data:
  # Old keys removed, new keys added
  AGENTNEXUS_AUTH_ENABLED: "1"
  AGENTNEXUS_LOG_LEVEL: "INFO"
```

### For CI/CD Pipelines

Update GitHub Actions / GitLab CI / etc.:

```yaml
# .github/workflows/deploy.yml
env:
  # OMNIGENT_API_KEY: ${{ secrets.OMNIGENT_API_KEY }}  # Old
  AGENTNEXUS_API_KEY: ${{ secrets.AGENTNEXUS_API_KEY }}  # New
```

---

## Deprecation Timeline

- **v0.12 (Current)**: Both `OMNIGENT_*` and `AGENTNEXUS_*` supported with warnings
- **v1.0**: `OMNIGENT_*` variables still work but emit loud deprecation warnings
- **v2.0**: `OMNIGENT_*` variables no longer supported

---

## Checking for Deprecated Variables

Run this command to check if you're using deprecated variables:

```bash
env | grep OMNIGENT_
```

If any variables are found, update them to `AGENTNEXUS_*`.

---

## Programmatic Access

If you're writing code that accesses these variables, use the compatibility layer:

```python
from agentnexus.migration import EnvCompat, get_env_var

# Get with automatic fallback and warning
config_home = get_env_var("CONFIG_HOME")

# Or use the EnvCompat helper
auth_enabled = EnvCompat.getbool("AUTH_ENABLED", default=False)
log_level = EnvCompat.get("LOG_LEVEL", default="INFO")
```

This ensures your code works with both old and new variable names during the transition period.
