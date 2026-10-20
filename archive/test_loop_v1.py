def test_single_final():
    model = FakeModel([Response(content="final")])
    r = run("hi", model, tools={})
    assert (r.text, r.steps, r.status) == ("final", 1, "done")



def test_tool_then_final():
    model = FakeModel([
        Response(tool_calls=[ToolCall("add", {"a": 1, "b": 2})]),
        Response(content="3"),
    ])
    r = run("1+2", model, tools={"add": lambda a, b: a + b})
    assert (r.text, r.steps, r.status) == ("3", 2, "done")


def test_max_steps():
    model = FakeModel([Response(tool_calls=[ToolCall("add", {"a": 1, "b": 1})])] * 5)
    r = run("x", model, tools={"add": lambda a, b: a + b}, max_steps=2)
    assert (r.status, r.steps) == ("stopped", 2)

def test_cancel():
    model = FakeModel([Response(tool_calls=[ToolCall("add", {"a": 1, "b": 1})])] * 5)
    r = run("x", model, tools={"add": lambda a, b: a + b}, cancel_after=1)
    assert r.status == "cancelled"


def test_unknown_tool_recovers():
    model = FakeModel([
        Response(tool_calls=[ToolCall("nope", {})]),
        Response(content="recovered"),
    ])
    r = run("x", model, tools={})
    assert (r.text, r.status) == ("recovered", "done")

def test_events(tmp_path):
    log = tmp_path / "run.jsonl"
    model = FakeModel([Response(content="final")])
    run("hi", model, tools={}, log_path=log)
    lines = log.read_text().splitlines()
    assert any('"type": "step"' in x for x in lines)
    assert any('"type": "model"' in x for x in lines)