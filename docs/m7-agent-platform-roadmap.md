# M7 — LangChain-ready agent platform roadmap

Canonical coordination issue: GitHub **#60**. Child implementation issues:
**#61–#67**. Prefer this document plus `AGENTS.md` over historical issue
sketches when layout or order disagrees.

Do **not** treat #60 as a request to rewrite existing sources, tasks, storage,
scoring, or Telegram delivery. M7 adds adapter layers around the working
plain-Python platform.

## Goal

Evolve `signal-agent` into a framework-neutral agent platform that can later
use LangChain and LangGraph **without** replacing deterministic CLI and
scheduled workflows.

## Preserve first

Already shipped and must remain supported throughout M7:

- `SignalItem` normalization
- `BaseSource` collectors and `fetch_from_sources()`
- `BaseTask` registry and `python main.py --task …`
- job-search, TW stock movers, watchlist, and market-assistant digests
- config / secrets / SQLite (with migrations) / run history
- Telegram digests and failure alerts
- optional OpenAI job scoring with rule fallback
- structured JSON logging
- Docker Compose and k3s CronJob deployment

Deterministic task paths stay available as the default production path.
Framework workflows are additive pilots, not replacements.

## Target architecture

```text
existing sources / tasks / scorers / notifiers / SQLite
              |
              v
framework-neutral tools + LLM gateway   (#61, #62)
              |
      +-------+--------+
      |                |
plain Python      LangChain adapters   (#63)
      |                |
      +-------+--------+
              |
       LangGraph workflows             (#64)
              |
     memory / RAG / evaluation         (#65, #66)
              |
     optional MCP adapters             (#67)
```

## Dependency direction (non-negotiable)

```text
domain logic (sources, tasks, scorers, notifiers, core)
        ^
        |  may depend on
        |
framework-neutral interfaces (app/llm, app/tools, app/memory, …)
        ^
        |  may depend on
        |
optional adapters (app/integrations/langchain, app/integrations/mcp, …)
```

Rules:

1. Framework code never leaks into `app/core`, `app/sources`, `app/scorers`,
   `app/notifiers`, or deterministic `app/tasks/*` modules.
2. LangChain / LangGraph / MCP stay **optional extras** until a workflow
   explicitly requires them.
3. Adapters wrap project-owned interfaces; they do not own business rules.
4. Child issues must not reimplement collectors, config loading, SQLite
   persistence, Telegram delivery, or deployment manifests already covered by
   M0–M6.

## Planned child work (execution order)

Work in this order unless a human asks otherwise. Each child issue is
independently implementable once its listed dependencies are done — no
repo-wide rewrite.

```text
1. #61  Framework-neutral LLM provider gateway — DONE (see `app/llm/`)
2. #62  Agent-callable tool protocol and registry — DONE (see `app/tools/`)
3. #63  Optional LangChain adapters for LLMs and tools — DONE (`.[langchain]`)
4. #64  Bounded LangGraph pilot: job_research_agent — DONE (`app/workflows/`)
5. #65  Durable agent memory and optional RAG            (needs #57; after #61/#64)
6. #66  Agent evaluation, tracing, and regression fixtures (needs #61/#62/#64; RAG cases after #65)
7. #67  Optional MCP interoperability for selected tools (needs #62; prefer after #63/#66)
```

`#61` and `#62` may proceed in parallel. Do not start `#63+` until both land.

## File-level direction

Use each GitHub issue body as the source of truth for acceptance criteria.
**Preferred package roots** (freeze these names in the first child PR that
creates them; rename only with an explicit follow-up that updates this map):

| Issue | Preferred packages / entry points |
|-------|-----------------------------------|
| #61 | `app/llm/` (`base`, `models`, `openai_provider`, `fake_provider`, `factory`); refactor `app/scorers/job_llm_scorer.py` onto the gateway |
| #62 | `app/tools/` (`base`, `models`, `registry`, job/stock/notification/health wrappers); allowlist only |
| #63 | optional `.[langchain]` extra; `app/integrations/langchain/`; no LangChain imports in domain packages |
| #64 | `app/workflows/` + additive `app/tasks/job_research_agent.py`; keep `job_search` unchanged |
| #65 | `app/memory/`; SQLite migrations for memory metadata; keyword retrieval before optional vectors |
| #66 | `app/evaluation/` + `evals/`; offline CI subset; redacted traces |
| #67 | optional `.[mcp]` extra; `app/integrations/mcp/`; localhost + allowlisted read tools by default |

## Implementation principles

1. Inspect existing code before adding abstractions; extract or wrap, do not
   duplicate.
2. Keep unrestricted tool execution impossible (no shell/SQL/filesystem/browser
   catch-alls).
3. Cap graph steps, LLM calls, and tool calls for any agent workflow.
4. Fail open to deterministic / rule-based paths when LLM or graph orchestration
   fails.
5. Never log API keys, mailbox credentials, or other secrets; redact tool args
   and traces.
6. CI must not call paid APIs; use fake providers and offline fixtures.
7. Add tests at every adapter and fallback boundary.
8. Preserve current CLI commands and scheduled deployment behavior; new tasks
   are additive.

## What M7 deliberately does not do

- Replace all tasks with autonomous agents
- Browser login automation for LinkedIn
- Automatic trading or brokerage integration
- Frontend UI
- Introducing a vector database before a concrete retrieval use case
- Public MCP exposure or unrestricted side-effect tools by default

## Closing #60

Shipping this roadmap (documented order, dependency direction, preferred
package roots, and child-issue map) satisfies **#60**. The checklist on the
GitHub issue body tracks **child implementation** (#61–#67), not roadmap
authorship — leave those boxes unchecked until each child PR lands.

Implementation belongs in the child PRs (#61–#67), each narrow and
independently reviewable.
