# Development

Coding agents should treat [`AGENTS.md`](../AGENTS.md) as the durable project
map (module boundaries, completed milestones, PR expectations).

## Layout (high level)

```text
signal-agent/
├── main.py / app/cli/     # CLI entry
├── app/core/              # models, config, DB, market hours, logging
├── app/sources/           # one provider → list[SignalItem]
├── app/tasks/             # end-to-end workflows
├── app/scorers/ / app/llm/ / app/tools/ / app/memory/
├── app/runtime/ / app/scheduler/ / app/chat/ / app/setup/
├── app/browser/ / app/service/ / app/skills/
├── app/search/ / app/runtimes/   # vendor-neutral research + coding analysis
├── app/integrations/      # optional LangChain / MCP adapters
├── docs/                  # operator + developer docs (this tree)
└── tests/
```

There is **no** shared `app/core/pipeline.py`. Orchestration lives in tasks and
`fetch_from_sources()`.

## Local checks

```bash
make check          # pytest + ruff + package smoke
make test
make lint
make eval           # offline evaluation suite
```

CI runs `make test`, `make lint`, and `make package-smoke` on push/PR.

## Optional extras

```bash
pip install -e '.[langchain]'   # LangGraph pilot adapters
pip install -e '.[mcp]'         # localhost MCP adapter
```

Keep LangChain/MCP out of `core` / `sources` / `scorers` / deterministic tasks.

## Contributing

1. Read the GitHub issue and `AGENTS.md`.
2. Branch from `main` as `issue-N-short-slug`.
3. Implement narrowly; add tests; run `make check`.
4. Update README/`config.yaml`/docs when operator-facing behavior changes.
5. Open a PR that references the issue (`Closes #N` when accepting it).

Do not commit secrets, local databases, or runtime logs.

## Roadmaps

- Completed M7 plan: [roadmap/m7-agent-platform-roadmap.md](roadmap/m7-agent-platform-roadmap.md)
- Vendor adapters: [vendor-neutral-ai.md](vendor-neutral-ai.md)
