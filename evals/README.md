# Offline evaluation assets

Canonical case YAML and sample fixtures ship inside the installed package:

- `app/evaluation/cases/` — offline suite cases used by `signal-agent-eval`
- `app/evaluation/fixtures/` — sample Markdown documents for local memory ingest

This top-level `evals/` directory remains as a developer pointer. Prefer the
packaged paths so Docker images and `pip install` wheels keep the suite green.
