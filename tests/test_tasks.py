import pytest

from app.tasks import BaseTask, DummyTask, FetchTask, build_task, list_tasks


def test_list_tasks_returns_registered_task_names() -> None:
    assert list_tasks() == ["dummy", "fetch"]


def test_build_task_returns_task_instance() -> None:
    task = build_task("dummy", config_path="config.yaml")

    assert isinstance(task, DummyTask)
    assert isinstance(task, BaseTask)
    assert task.config_path == "config.yaml"


def test_build_task_supports_fetch_task() -> None:
    task = build_task("fetch", config_path="config.yaml")

    assert isinstance(task, FetchTask)
    assert task.name == "fetch"


def test_build_task_unknown_name_raises_clear_error() -> None:
    with pytest.raises(ValueError, match="Unknown task: missing-task"):
        build_task("missing-task", config_path="config.yaml")

    with pytest.raises(ValueError, match="Available tasks: dummy, fetch"):
        build_task("missing-task", config_path="config.yaml")
