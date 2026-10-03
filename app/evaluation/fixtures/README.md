# Evaluation fixtures

Local deterministic Markdown/text samples used by offline eval cases and as an
example memory document path for local RAG experiments:

```yaml
memory:
  namespaces:
    job_research:
      documents:
        - app/evaluation/fixtures/company-notes.md
```

Deployment-safe `config.yaml` / k3s defaults leave `documents` empty.

Do not place secrets, `.env` files, or mailbox credentials here. Ingest also
refuses content that matches credential patterns.
