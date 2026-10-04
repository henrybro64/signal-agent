from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
K3S_DIR = ROOT / "deploy" / "k3s"


def test_k3s_required_manifest_files_exist() -> None:
    assert {
        "configmap.yaml",
        "cronjob-job-search.yaml",
        "cronjob-tw-stock-movers.yaml",
        "namespace.yaml",
        "pvc.yaml",
        "secret.example.yaml",
    } <= {path.name for path in K3S_DIR.iterdir()}


def test_k3s_yaml_files_are_parseable() -> None:
    yaml_paths = sorted(K3S_DIR.glob("*.yaml"))

    assert yaml_paths
    for path in yaml_paths:
        manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(manifest, dict), path.name


def test_namespace_manifest_creates_signal_agent_namespace() -> None:
    manifest = _load_yaml("namespace.yaml")

    assert manifest["apiVersion"] == "v1"
    assert manifest["kind"] == "Namespace"
    assert manifest["metadata"]["name"] == "signal-agent"


def test_secret_example_manifest_matches_expected_secret_shape() -> None:
    manifest = _load_yaml("secret.example.yaml")

    assert manifest["apiVersion"] == "v1"
    assert manifest["kind"] == "Secret"
    assert manifest["metadata"]["name"] == "signal-agent-secrets"
    assert manifest["metadata"]["namespace"] == "signal-agent"
    assert manifest["type"] == "Opaque"
    assert manifest["stringData"] == {
        "OPENAI_API_KEY": "replace-me",
        "ANTHROPIC_API_KEY": "",
        "PERPLEXITY_API_KEY": "",
        "CODEX_API_KEY": "",
        "TELEGRAM_BOT_TOKEN": "replace-me",
        "TELEGRAM_CHAT_ID": "replace-me",
        "LINKEDIN_EMAIL_IMAP_HOST": "imap.gmail.com",
        "LINKEDIN_EMAIL_IMAP_USERNAME": "replace-me@gmail.com",
        "LINKEDIN_EMAIL_IMAP_PASSWORD": "replace-me",
        "LINKEDIN_EMAIL_IMAP_MAILBOX": "INBOX",
    }


def test_configmap_manifest_embeds_repo_config_yaml() -> None:
    manifest = _load_yaml("configmap.yaml")
    repo_config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    embedded_config = yaml.safe_load(manifest["data"]["config.yaml"])

    assert manifest["apiVersion"] == "v1"
    assert manifest["kind"] == "ConfigMap"
    assert manifest["metadata"]["name"] == "signal-agent-config"
    assert manifest["metadata"]["namespace"] == "signal-agent"
    assert embedded_config == repo_config


def test_pvc_manifest_persists_signal_agent_sqlite_data() -> None:
    manifest = _load_yaml("pvc.yaml")

    assert manifest["apiVersion"] == "v1"
    assert manifest["kind"] == "PersistentVolumeClaim"
    assert manifest["metadata"]["name"] == "signal-agent-data"
    assert manifest["metadata"]["namespace"] == "signal-agent"
    assert manifest["spec"]["accessModes"] == ["ReadWriteOnce"]
    assert manifest["spec"]["resources"]["requests"]["storage"] == "1Gi"


def test_job_search_cronjob_manifest_runs_expected_cli_command() -> None:
    manifest = _load_yaml("cronjob-job-search.yaml")
    container = _cronjob_container(manifest)

    assert manifest["kind"] == "CronJob"
    assert manifest["metadata"]["name"] == "job-search-daily"
    assert manifest["metadata"]["namespace"] == "signal-agent"
    assert manifest["spec"]["timeZone"] == "Asia/Taipei"
    assert manifest["spec"]["schedule"] == "5 8,18 * * *"
    assert manifest["spec"]["jobTemplate"]["spec"]["activeDeadlineSeconds"] == 1800
    assert container["image"] == "signal-agent:local"
    assert container["command"] == [
        "python",
        "main.py",
        "--task",
        "job_search",
        "--config",
        "/app/config.yaml",
    ]
    assert container["env"] == [
        {
            "name": "SIGNAL_AGENT_ENV",
            "value": "production",
        }
    ]
    assert container["envFrom"] == [
        {
            "secretRef": {
                "name": "signal-agent-secrets",
            }
        }
    ]
    assert container["volumeMounts"] == [
        {
            "name": "config",
            "mountPath": "/app/config.yaml",
            "subPath": "config.yaml",
            "readOnly": True,
        },
        {
            "name": "data",
            "mountPath": "/app/data",
        },
    ]
    assert _cronjob_volume_names(manifest) == ["config", "data"]


def test_tw_stock_movers_cronjob_manifest_runs_expected_cli_command() -> None:
    manifest = _load_yaml("cronjob-tw-stock-movers.yaml")
    container = _cronjob_container(manifest)
    command = container["command"]

    assert manifest["kind"] == "CronJob"
    assert manifest["metadata"]["name"] == "tw-stock-movers-every-10-min"
    assert manifest["metadata"]["namespace"] == "signal-agent"
    assert manifest["spec"]["timeZone"] == "Asia/Taipei"
    assert manifest["spec"]["schedule"] == "*/10 * * * 1-5"
    assert container["image"] == "signal-agent:local"
    assert command[:2] == ["/bin/sh", "-lc"]
    script = command[2]
    assert "--task tw_stock_movers" in script
    assert "--task stock_watchlist" in script
    assert container["envFrom"] == [
        {
            "secretRef": {
                "name": "signal-agent-secrets",
            }
        }
    ]
    assert _cronjob_volume_names(manifest) == ["config", "data"]


def test_cronjob_manifest_comments_explain_schedule_assumptions() -> None:
    job_search_text = (K3S_DIR / "cronjob-job-search.yaml").read_text(
        encoding="utf-8"
    )
    tw_stock_text = (K3S_DIR / "cronjob-tw-stock-movers.yaml").read_text(
        encoding="utf-8"
    )

    assert "Asia/Taipei" in job_search_text
    assert "Asia/Taipei" in tw_stock_text
    assert "Taiwan market hours" in tw_stock_text
    assert "stock_watchlist" in tw_stock_text
    assert "off-hours runs no-op" in tw_stock_text
    assert ":05 offset" in job_search_text


def test_secret_example_is_not_gitignored_template_only() -> None:
    secret_example = (K3S_DIR / "secret.example.yaml").read_text(encoding="utf-8")

    assert "replace-me" in secret_example
    assert "Example only" in secret_example


def _load_yaml(name: str) -> dict:
    return yaml.safe_load((K3S_DIR / name).read_text(encoding="utf-8"))


def _cronjob_container(manifest: dict) -> dict:
    return manifest["spec"]["jobTemplate"]["spec"]["template"]["spec"][
        "containers"
    ][0]


def _cronjob_volume_names(manifest: dict) -> list[str]:
    return [
        volume["name"]
        for volume in manifest["spec"]["jobTemplate"]["spec"]["template"]["spec"][
            "volumes"
        ]
    ]
