from app.eval import EvalTask, load_tasks, run_checks, run_eval
from app.providers.fake import FakeProvider
from core.models import Response, ToolCall


def test_load_default_tasks():
    tasks = load_tasks()
    assert len(tasks) == 10
    assert len({task.id for task in tasks}) == 10


def test_run_checks_file_and_command(tmp_path):
    (tmp_path / "a.txt").write_text("hello\n")
    task = EvalTask(
        id="t",
        prompt="p",
        checks=[
            {"type": "file_equals", "path": "a.txt", "text": "hello"},
            {"type": "file_contains", "path": "a.txt", "text": "ell"},
            {"type": "command", "command": "echo ok", "exit": 0, "contains": "ok"},
        ],
    )
    checks = run_checks(task, tmp_path)
    assert all(check["ok"] for check in checks)


def test_run_eval_with_scripted_provider(tmp_path):
    task = EvalTask(
        id="create_file",
        prompt="create hello.txt",
        checks=[{"type": "file_equals", "path": "hello.txt", "text": "hello"}],
    )

    def factory(_task):
        return FakeProvider(
            [
                Response(
                    tool_calls=[
                        ToolCall(
                            "write_file",
                            {"path": "hello.txt", "content": "hello"},
                            id="c1",
                        )
                    ]
                ),
                Response(content="done"),
            ]
        )

    report = run_eval([task], factory, tmp_path, model="fake")

    assert report.passed == 1
    assert report.total == 1
    assert report.previous_passed is None
    assert report.delta is None
    assert (tmp_path / ".vibe" / "evals").exists()
    assert (report.run_dir and __import__("pathlib").Path(report.run_dir, "report.json").exists())


def test_run_eval_reports_regression_delta(tmp_path):
    task = EvalTask(
        id="create_file",
        prompt="create hello.txt",
        checks=[{"type": "file_equals", "path": "hello.txt", "text": "hello"}],
    )

    def good_factory(_task):
        return FakeProvider(
            [
                Response(
                    tool_calls=[
                        ToolCall(
                            "write_file",
                            {"path": "hello.txt", "content": "hello"},
                            id="c1",
                        )
                    ]
                ),
                Response(content="done"),
            ]
        )

    first = run_eval([task], good_factory, tmp_path, model="fake")
    second = run_eval([task], good_factory, tmp_path, model="fake")

    assert first.passed == 1
    assert second.passed == 1
    assert second.previous_passed == 1
    assert second.delta == 0


def test_run_eval_failure(tmp_path):
    task = EvalTask(
        id="create_file",
        prompt="create hello.txt",
        checks=[{"type": "file_equals", "path": "hello.txt", "text": "hello"}],
    )

    def bad_factory(_task):
        return FakeProvider(["I cannot do that"])

    report = run_eval([task], bad_factory, tmp_path, model="fake")
    assert report.passed == 0
    assert report.results[0].passed is False
    assert report.results[0].checks[0]["ok"] is False
