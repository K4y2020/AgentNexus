# Migration Guide: Omnigent → AgentNexus

## Overview

The project has been renamed from **Omnigent** to **AgentNexus** to establish a unique brand identity. This guide helps you transition to the new naming.

## Command Line Changes

### Recommended Commands (New)

```bash
# Primary commands
agentnexus server          # Start server
agentnexus host            # Connect to server
nexus chat                 # Quick alias

# Examples
agentnexus host --server http://127.0.0.1:6767
nexus server --background
```

### Legacy Commands (Still Work)

```bash
# Deprecated but functional (will be removed in v2.0)
omnigent server
omni host --server http://127.0.0.1:6767
```

**⚠️ Warning**: `omnigent` and `omni` commands are deprecated and will be removed in version 2.0. Please update your scripts and documentation.

## Environment Variables

### New Variable Names

All `OMNIGENT_*` environment variables have been renamed to `AGENTNEXUS_*`:

```bash
# Old (deprecated)
OMNIGENT_AUTH_ENABLED=0
OMNIGENT_SERVER_URL=http://localhost:6767
OMNIGENT_CONFIG_HOME=~/.omnigent
OMNIGENT_DATA_DIR=~/.omnigent/data

# New (recommended)
AGENTNEXUS_AUTH_ENABLED=0
AGENTNEXUS_SERVER_URL=http://localhost:6767
AGENTNEXUS_CONFIG_HOME=~/.agentnexus
AGENTNEXUS_DATA_DIR=~/.agentnexus/data
```

### Backward Compatibility

The old `OMNIGENT_*` variables are still supported for backward compatibility but will be removed in v2.0.

## Configuration Directory

### Default Locations

- **New**: `~/.agentnexus/`
- **Old**: `~/.omnigent/` (still works)

### Manual Migration (Optional)

If you want to migrate your existing configuration:

```bash
# Backup first
cp -r ~/.omnigent ~/.omnigent.backup

# Copy to new location
cp -r ~/.omnigent ~/.agentnexus

# Update environment variables in your shell config
# ~/.bashrc, ~/.zshrc, etc.
```

## Python Package Changes

### Import Statements

The Python package name remains `agentnexus` internally:

```python
# Correct (unchanged)
from agentnexus import AgentSpec
from agentnexus.client import Client
import agentnexus
```

### PyPI Package

- **New**: `agentnexus` (recommended)
- **Old**: `omnigent` (deprecated)

```bash
# Install from PyPI
pip install agentnexus

# Or with uv
uv pip install agentnexus
```

## Docker Images

### New Image Names

```bash
# Old
docker pull omnigent/server:latest

# New (recommended)
docker pull agentnexus/server:latest
```

## GitHub Repository

- **New**: https://github.com/K4y2020/AgentNexus
- **Old**: https://github.com/agentnexus-ai/omnigent (archived)

## CI/CD Updates

### GitHub Actions

Update your workflows to use the new commands:

```yaml
# Before
- run: omnigent server --background

# After
- run: agentnexus server --background
```

### Environment Variables

Update all workflow files to use `AGENTNEXUS_*` instead of `OMNIGENT_*`.

## Timeline

| Version | Status | Notes |
|---------|--------|-------|
| 0.12.x | Both names work | Deprecation warnings |
| 1.x | Both names work | Stronger warnings |
| 2.0 | Old names removed | Breaking change |

## Migration Checklist

- [ ] Update CLI commands from `omnigent`/`omni` to `agentnexus`/`nexus`
- [ ] Rename environment variables from `OMNIGENT_*` to `AGENTNEXUS_*`
- [ ] Update Docker image references
- [ ] Update documentation and README files
- [ ] Update CI/CD pipelines
- [ ] Update shell scripts and automation
- [ ] Optionally migrate `~/.omnigent/` to `~/.agentnexus/`

## Need Help?

- **Issues**: https://github.com/K4y2020/AgentNexus/issues
- **Documentation**: https://github.com/K4y2020/AgentNexus/blob/main/README.md

## FAQ

### Q: Will my existing sessions still work?

Yes, existing sessions in `~/.omnigent/` will continue to work until you manually migrate to `~/.agentnexus/`.

### Q: Do I need to reinstall?

No, the package name remains the same internally. Just update your commands and environment variables.

### Q: What happens if I use both `OMNIGENT_*` and `AGENTNEXUS_*`?

The new `AGENTNEXUS_*` variables take precedence. If only old variables are set, they will be used with a deprecation warning.

### Q: Can I keep using `omni` command?

Yes, until version 2.0. We recommend updating to `nexus` or `agentnexus` to avoid breaking changes in the future.
