# Changelog

All notable changes to the AgentNexus VS Code extension are documented here.

## [0.1.0]

Initial release — a minimal, iframe-only client for a locally running AgentNexus
server.

- Open a running local AgentNexus server in an editor-beside panel.
- **AgentNexus: Open** command, available from the editor-title bar and the
command palette, plus an activity-bar view with an "Open AgentNexus" button.
- Automatically discovers a local server via `~/.agentnexus/local_server.pid`, or
point the extension at one with the `agentnexus.serverUrl` setting. Localhost
servers only in this build.

