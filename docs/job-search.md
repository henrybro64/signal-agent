# Job search

`job_search` collects openings from configured sources, scores them against a
Markdown profile, persists new items in SQLite, and can send a Telegram digest.

## Sources

| Source | Notes |
|--------|--------|
| `job104` | 104 job search HTTP collector |
| `linkedin_email` | Prefer job-alert **email** (`.eml` or IMAP), not LinkedIn login scraping |
| `indeed` | Best-effort; may hit anti-bot blocks |
| `glassdoor` | Best-effort; may hit anti-bot blocks |

Failed sources are recorded and skipped; they do not abort unrelated sources.

### LinkedIn email

Either:

- `LINKEDIN_EMAIL_SOURCE_PATH` → a `.eml` file or directory of alerts, or
- IMAP: `LINKEDIN_EMAIL_IMAP_HOST` / `_USERNAME` / `_PASSWORD`, plus optional
  `sources.linkedin_email.imap_mailbox` and `timeout_seconds`.

IMAP searches `SINCE` the lookback window and known LinkedIn alert senders so
the whole inbox is not downloaded.

## Job profile

Scoring reads a Markdown preference file (default packaged example:
`app/profiles/henry_job_profile.md`). Sections typically include target roles,
strong skills, preferred companies, avoid roles, reject keywords, and location /
remote preferences.

To use your own profile:

1. Copy `app/profiles/henry_job_profile.md` to something like
   `app/profiles/my_job_profile.md` (or keep it outside the repo).
2. Point the job-search profile at it:

```yaml
tasks:
  job_search:
    profiles:
      - name: default
        sources: [job104, linkedin_email]
        keywords: [python, backend]
        locations: [Taiwan]
        scorer_profile: app/profiles/my_job_profile.md
```

When `profiles` is omitted, top-level `sources` / `keywords` / `locations` become
a single `default` profile.

## Scoring

1. **Rule scorer** always runs (default path).
2. Optional **LLM scorer** runs when `scoring.llm_enabled: true`, the rule score
   clears `llm_min_rule_score`, and the per-run LLM budget remains.
3. LLM failures fall open to rules (`[rule_fallback]` reasons).

Digest caps: `digest_max_strong_matches`, `digest_max_maybe_matches`,
`include_rejected_summary`.

## Run

```bash
signal-agent run job_search
# or
python main.py --task job_search
```

Do **not** automate applying to jobs or messaging recruiters — out of scope by
project policy.
