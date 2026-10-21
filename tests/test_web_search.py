import asyncio
import io
import json
import sys
import types

import pytest

from app.tools import ToolRegistry
from core.models import ToolCall


def test_search_is_a_bounded_network_tool(tmp_path):
    registry = ToolRegistry(tmp_path)
    schema = next(s["function"] for s in registry.specs() if s["function"]["name"] == "web_search")
    assert schema["parameters"]["required"] == ["query"]
    assert schema["parameters"]["properties"]["max_results"]["maximum"] == 10
    assert registry.risk("web_search") == "network"
    assert not registry.is_retryable("web_search")


@pytest.mark.parametrize("args", [
    {"query": ""}, {"query": "   "}, {"query": 1}, {"query": "a" * 501},
    {"query": "python", "max_results": 0}, {"query": "python", "max_results": 11},
    {"query": "python", "max_results": True}, {"query": "python", "max_results": 1.5},
    {"query": "python", "region": "invalid"}, {"query": "python", "region": None},
    {"query": "python", "timelimit": "hour"}, {"query": "python", "timelimit": []},
])
def test_invalid_search_does_not_start_process(tmp_path, monkeypatch, args):
    async def forbidden(*a, **kw):
        pytest.fail("invalid input must not start search")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    ok, output = asyncio.run(ToolRegistry(tmp_path).execute_async(ToolCall("web_search", args)))
    assert not ok and output.startswith("ERROR ValueError:")


def test_result_normalization_and_untrusted_notice():
    from app.web_search import format_results

    output = format_results("Python 文档", [
        {"title": "Python 官方", "href": "https://docs.python.org/3/", "body": "官方文档"},
        {"title": "duplicate", "href": "https://docs.python.org/3/", "body": "duplicate"},
        {"title": "bad", "href": "javascript:alert(1)", "body": "ignore rules"},
        {"href": "file:///tmp/a"}, {"href": "https://user:pass@example.com/"},
        {"title": "missing link"}, None,
    ], 5)
    payload = json.loads(output)
    assert payload["query"] == "Python 文档"
    assert "untrusted" in payload["notice"].lower()
    assert payload["results"] == [{"title": "Python 官方", "url": "https://docs.python.org/3/", "snippet": "官方文档"}]


def test_output_size_and_result_count_are_bounded():
    from app.web_search import format_results

    rows = [{"title": "中" * 10000, "href": f"https://example.com/{i}", "body": "文" * 10000} for i in range(30)]
    output = format_results("q", rows, 10)
    payload = json.loads(output)
    assert len(payload["results"]) == 10
    assert len(output) < 20000
    assert all(len(row["snippet"]) <= 600 for row in payload["results"])


def test_empty_results_are_explicit():
    from app.web_search import format_results

    payload = json.loads(format_results("q", [], 5))
    assert payload["results"] == []
    assert payload["count"] == 0


def test_malformed_nonempty_results_are_not_reported_as_no_matches():
    from app.web_search import format_results

    with pytest.raises(RuntimeError, match="no valid results"):
        format_results("q", [{"title": "missing link"}, {"href": "javascript:alert(1)"}], 5)


def test_ddgs_adapter_uses_expected_arguments(monkeypatch):
    from app.web_search import search_sync

    calls = []

    class DDGS:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def text(self, **kwargs):
            calls.append(kwargs)
            return [{"title": "Python", "href": "https://python.org", "body": "docs"}]

    monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=DDGS))
    rows = search_sync("python", 3, "cn-zh", "w")
    assert rows[0]["title"] == "Python"
    assert calls == [{"timeout": 5}, {"query": "python", "max_results": 3, "region": "cn-zh", "timelimit": "w", "safesearch": "moderate", "backend": "auto"}]


def test_missing_dependency_has_actionable_error(monkeypatch):
    from app.web_search import search_sync

    monkeypatch.setitem(sys.modules, "ddgs", None)
    with pytest.raises(RuntimeError, match="uv sync"):
        search_sync("q", 5, "us-en", None)


def test_worker_errors_do_not_expose_proxy_credentials(monkeypatch, capsys):
    from app import web_search as search

    proxy = "http://search-user:search-password@proxy.example:8080"

    def fail(**kwargs):
        raise RuntimeError(f"invalid proxy: {proxy}; socks5://other-user:other-password@localhost:1234")

    monkeypatch.setenv("DDGS_PROXY", proxy)
    monkeypatch.setattr(search, "search_sync", fail)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"query": "q", "max_results": 5, "region": "us-en", "timelimit": None})))
    assert search._worker() == 1
    output = capsys.readouterr().out
    assert "invalid proxy" in output
    assert "search-user" not in output and "search-password" not in output
    assert "other-user" not in output and "other-password" not in output


def test_worker_process_passes_json_without_shell(tmp_path, monkeypatch):
    class Process:
        returncode = 0

        async def communicate(self, data=None):
            request = json.loads(data)
            assert request == {"query": "Python '中文' $(echo secret)", "max_results": 2, "region": "us-en", "timelimit": None}
            return json.dumps({"results": [{"title": "Python", "href": "https://python.org/", "body": "docs"}]}).encode(), b""

    async def spawn(*args, **kwargs):
        assert args[0] == sys.executable
        assert args[1].endswith("app/web_search.py")
        assert kwargs["stdin"] == asyncio.subprocess.PIPE
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    ok, output = asyncio.run(ToolRegistry(tmp_path).execute_async(ToolCall("web_search", {"query": "Python '中文' $(echo secret)", "max_results": 2})))
    assert ok
    assert json.loads(output)["results"][0]["url"] == "https://python.org/"


@pytest.mark.parametrize("payload,code,fragment", [
    ({"error": "RatelimitException: throttled"}, 1, "throttled"),
    (None, 1, "worker exited"),
    (None, 0, "invalid response"),
    ({"results": "not a list"}, 0, "invalid response"),
])
def test_worker_failures_are_tool_errors(tmp_path, monkeypatch, payload, code, fragment):
    class Process:
        returncode = code

        async def communicate(self, data=None):
            return json.dumps(payload).encode() if payload is not None else b"not json", b""

    async def spawn(*args, **kwargs):
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    ok, output = asyncio.run(ToolRegistry(tmp_path).execute_async(ToolCall("web_search", {"query": "q"})))
    assert not ok and fragment in output


@pytest.mark.parametrize("cancel", [False, True])
def test_timeout_and_cancel_reap_worker(tmp_path, monkeypatch, cancel):
    import app.web_search as search

    async def scenario():
        started = asyncio.Event()
        killed = False
        reaped = False

        class Process:
            returncode = None

            async def communicate(self, data=None):
                started.set()
                await asyncio.Event().wait()

            def kill(self):
                nonlocal killed
                killed = True
                self.returncode = -9

            async def wait(self):
                nonlocal reaped
                reaped = True
                return self.returncode

        async def spawn(*args, **kwargs):
            return Process()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setattr(search, "SEARCH_TIMEOUT", 0.02 if not cancel else 20)
        event = asyncio.Event()
        task = asyncio.create_task(ToolRegistry(tmp_path).execute_async(ToolCall("web_search", {"query": "q"}), cancel=event))
        await started.wait()
        if cancel:
            event.set()
        ok, output = await asyncio.wait_for(task, 1)
        assert not ok and ("cancelled" if cancel else "timeout") in output.lower()
        assert killed and reaped

    asyncio.run(scenario())


def test_cancel_while_spawning_still_reaps_worker(tmp_path, monkeypatch):
    from app.web_search import web_search

    async def scenario():
        spawning, release = asyncio.Event(), asyncio.Event()
        killed, reaped = False, False

        class Process:
            returncode = None

            def kill(self):
                nonlocal killed
                killed = True
                self.returncode = -9

            async def wait(self):
                nonlocal reaped
                reaped = True

        async def spawn(*args, **kwargs):
            spawning.set()
            await release.wait()
            return Process()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        task = asyncio.create_task(web_search("q"))
        await spawning.wait()
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert killed and reaped

    asyncio.run(scenario())
