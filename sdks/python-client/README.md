# agentnexus-client

Python client SDK for the [agentnexus](https://github.com/K4y2020/AgentNexus)
server API.

`agentnexus-client` is a typed client for driving agentnexus sessions over the
server's HTTP + SSE API — creating sessions, sending turns, and streaming
responses. It shares the `StreamEvent` / `SessionStreamEventType` types that the
server emits, so streamed envelopes are validated against a single source of
truth.

It is released in lockstep with the core `agentnexus` package at a matching
version:

```bash
pip install agentnexus-client
```

See the [agentnexus repository](https://github.com/K4y2020/AgentNexus) for full
documentation.
