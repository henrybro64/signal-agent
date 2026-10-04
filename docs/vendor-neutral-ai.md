# Vendor-neutral AI integrations (#93)

Existing deterministic tasks require no AI credentials when `scoring.llm_enabled`
is false. Optional job scoring continues to fall back to rules when generation
fails. No live AI calls run in CI.

## Reasoning providers

OpenAI, Anthropic, and the offline fake provider share `app.llm.LLMProvider`.
Tasks, scorers, and workflows use project-owned models and errors. Configure
provider/model identifiers through the existing model CLI:

```sh
signal-agent models set anthropic/<your-model>
signal-agent models fallback add openai/<your-backup-model>
signal-agent models status
# This performs a small paid call when a real provider is selected:
signal-agent models test
```

`models set` keeps the legacy `scoring.llm_model` alias synchronized. When editing
YAML by hand, remove that alias or keep it equal to `llm.model`:

```yaml
llm:
  provider: anthropic
  model: <your-model>
  fallbacks:
    - openai/<your-backup-model>
providers:
  anthropic:
    api_key_env: TEAM_CLAUDE_KEY
  openai:
    api_key_env: OPENAI_API_KEY
```

Put the keys in `.env` or the process environment. Custom environment names are
resolved explicitly: a missing alias does not fall back to another key. Every
fallback gets its own vendor credential. Configuration output, logs, failure
alerts, and tool/chat traces use a shared redaction inventory, including aliases.

The adapters declare their implemented capabilities. Text and structured JSON
are supported; native tool calls, streaming, and vision are rejected before HTTP
calls. The router preserves these requests so capability checks remain effective.
`LLMRequest.metadata` is opaque and preserved by routing; adapters currently ignore
it. Usage is normalized into `LLMUsage` when supplied by the provider.

OpenAI uses the existing Responses adapter. Anthropic uses the Messages API with
schema instructions and **local JSON Schema validation**, not a claim of native
constrained decoding. Both adapters reject responses that violate the requested
schema. Schema references must be local; validation never fetches remote schemas.
Unsupported model features can still return project-owned provider errors: these
capabilities describe adapter support, not every model offered by a vendor.

## Web research

`app.search.SearchProvider` is independent of text generation. Its Perplexity
adapter uses Sonar chat completions for an answer and citations. Enable it with:

```yaml
search:
  provider: perplexity
  model: sonar
  max_results: 5
  timeout_seconds: 20
providers:
  perplexity:
    api_key_env: PERPLEXITY_API_KEY
```

```sh
signal-agent skill enable research
signal-agent tools
signal-agent chat
```

The optional `research` skill registers `research_web` through the existing tool
allowlist and read-permission gate. It is disabled by default. The fake backend
is available for offline tests. Real research calls can incur provider charges.

## Bounded coding runtime

`app.runtimes.AgentRuntime` is separate from `app/runtime`, which manages the local
daemon. `signal-agent agent-run` consumes `runtime.default`:

- `builtin` performs analysis through the configured LLM gateway.
- `codex` runs the installed Codex CLI, using `CODEX_API_KEY` (or its configured
  alias) and optional `runtime.model`.
- `fake` returns a deterministic result for offline tests.

All runtimes require the existing execute permission, approval, and audit policy.
The default is disabled. For interactive approval, configure:

```yaml
runtime:
  default: builtin
  # model: <Codex model>  # used only by the codex adapter
tools:
  permissions:
    tools:
      runtime_execute: ask
```

```sh
signal-agent agent-run --action plan 'Review this supplied code: def add(a,b): return a-b'
```

`ask` fails closed without an interactive terminal. An operator may explicitly
configure `runtime_execute: allow` for unattended use. The side-effect budget
and permission audit log still apply. Allowed actions are `analyze`, `summarize`,
`plan`, and `propose_patch`; patches are text for human review, never applied.

The Codex adapter replaces the earlier unimplemented custom HTTP endpoint. It
uses fixed CLI arguments, supplies context through stdin, and normalizes process
failure, timeout, malformed events, and unexpected tool actions. It runs in an
empty temporary workspace with isolated configuration, a read-only sandbox,
shell/unified-exec tools disabled, web search and apps disabled, and ephemeral
sessions. It does not inherit the user's plugins, MCP configuration, or other
provider credentials. Unsupported CLI options cause a failure, not a less
restricted retry. Use a CLI supporting the documented flags below.

This initial scope has **no automatic repository reads, shell commands, or
repository mutations**. Supply selected code in the goal; `working_directory`
requests are rejected. Prompts/results are not written to project traces by this
adapter. The provider still receives the supplied context.

Official interface references: [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
and [Codex configuration](https://learn.chatgpt.com/docs/config-file/config-reference).

## Adding an adapter

1. Implement the relevant project-owned LLM, search, or runtime protocol in its
   adapter package. Normalize failures without including provider payloads or keys.
2. Register it in that package's factory. LLM configuration and model management
   obtain supported names from `supported_llm_providers()`.
3. Declare capabilities for LLM adapters; unsupported features must fail clearly.
4. Add credential configuration and include it in the shared secret inventory.
5. Add mocked transport tests, credential isolation, error normalization, and
   capability tests. Keep tests independent of paid APIs.

No workflow should import a vendor SDK or select vendors with its own branches.
