# Deployment options

Choose the smallest path that matches how you want the agent to run.

| Option | Best when | How |
|--------|-----------|-----|
| **Local CLI / runtime** | Laptop or always-on login session; iterating quickly | `signal-agent setup` → `start` / `schedule` |
| **systemd --user** | Personal Linux box; survive logout; one user | `signal-agent service install` |
| **Docker Compose + host cron/timers** | Ubuntu VM; isolate deps; simple ops | [ubuntu-vm-deployment.md](ubuntu-vm-deployment.md) |
| **k3s CronJobs** | Already run a cluster; shared PVC for SQLite | `deploy/k3s/` manifests |

Do **not** remove earlier stages when adding a later one. Secrets stay in `.env`
or Kubernetes Secrets — never in Git.

## Local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
signal-agent setup --non-interactive --provider fake
signal-agent start
```

## systemd --user

```bash
signal-agent service install --no-enable
signal-agent service status
```

Uses the active interpreter path (venv-safe). Unit files never embed secrets;
they load `.env` via `EnvironmentFile=`.

## Docker Compose

```bash
cp .env.example .env   # fill secrets
docker compose run --rm signal-agent --list-tasks
docker compose run --rm signal-agent --task job_search
```

Unattended VM scheduling: [Ubuntu VM runbook](ubuntu-vm-deployment.md)
(`deploy/ubuntu/` cron wrappers and optional systemd timers).

## k3s (optional)

```bash
kubectl apply -f deploy/k3s/namespace.yaml
# copy secret.example.yaml → secret.yaml, fill values, apply
kubectl apply -f deploy/k3s/
```

ConfigMap must stay aligned with root `config.yaml` (CI checks this).
