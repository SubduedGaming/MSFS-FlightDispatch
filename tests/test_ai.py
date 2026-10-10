import json

import httpx
import pytest

from skydispatch.ai.dispatcher import Dispatcher
from skydispatch.ai.llm import LLMError, LMStudioClient
from skydispatch.core.config import Settings


def make_client(handler, settings=None):
    s = settings or Settings()
    return LMStudioClient(s.ai, transport=httpx.MockTransport(handler)), s


def completion(content="", tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return httpx.Response(200, json={"choices": [{"message": msg}]})


def test_list_models_and_health():
    def h(req):
        assert req.url.path == "/v1/models"
        return httpx.Response(200, json={"data": [{"id": "qwen2.5-7b"}]})
    client, _ = make_client(h)
    assert client.list_models() == ["qwen2.5-7b"]
    ok, msg = client.health()
    assert ok and "qwen" in msg


def test_health_no_models_and_unreachable():
    c, _ = make_client(lambda r: httpx.Response(200, json={"data": []}))
    assert c.health()[0] is False

    def boom(req):
        raise httpx.ConnectError("refused")
    c, _ = make_client(boom)
    ok, msg = c.health()
    assert not ok and "LM Studio" in msg


def test_native_tool_calling_flow(career):
    calls = []

    def h(req):
        body = json.loads(req.content)
        calls.append(body)
        if not any(m["role"] == "tool" for m in body["messages"]):
            return completion("", [{"id": "c1", "type": "function",
                                    "function": {"name": "list_jobs", "arguments": json.dumps({"limit": 2})}}])
        tool_msg = [m for m in body["messages"] if m["role"] == "tool"][0]
        assert json.loads(tool_msg["content"])["count"] >= 1
        return completion("I have **two** good ones for you, Captain.")

    client, s = make_client(h)
    d = Dispatcher(career, s, client)
    reply = d.chat("What jobs do you have?")
    assert reply == "I have two good ones for you, Captain."      # markdown stripped
    assert len(calls) == 2 and "tools" in calls[0]
    msgs = career.db.messages()
    assert [m["role"] for m in msgs] == ["user", "assistant"]


def test_prompt_mode_tool_protocol(career):
    s = Settings()
    s.ai.tool_mode = "prompt"
    seen = []

    def h(req):
        body = json.loads(req.content)
        seen.append(body)
        assert "tools" not in body
        last = body["messages"][-1]
        if last["role"] == "user" and "<tool_result" not in last["content"]:
            return completion('<tool_call>{"name": "get_status", "arguments": {}}</tool_call>')
        assert "<tool_result" in last["content"] and "balance" in last["content"]
        return completion("You've got money in the bank.")

    client, _ = make_client(h, s)
    assert Dispatcher(career, s, client).chat("How am I doing?") == "You've got money in the bank."


def test_a_server_that_rejects_tools_is_an_error_not_a_silent_switch(career):
    def h(req):
        body = json.loads(req.content)
        assert "tools" in body                                  # native tool calling, as configured
        return httpx.Response(400, json={"error": {"message": "model does not support tools"}})

    client, s = make_client(h)
    with pytest.raises(LLMError, match="does not support tools"):
        Dispatcher(career, s, client).chat("hi")


def test_accept_via_tool_enforces_rules(career):
    d = Dispatcher(career, Settings(), None)
    jid = career.db.add_job(kind="cargo", title="Big", origin="EGLL", dest="EGPH", distance_nm=300, cargo_lb=5000,
                            payout=5000, expires_at="2999-01-01T00:00:00+00:00")
    res = d.tools.call("accept_job", {"job_id": jid, "aircraft_id": career.db.hangar()[0].id})
    assert "Cargo limit" in res["error"]


def test_a_down_model_raises_everywhere_and_nothing_is_made_up(career):
    def boom(req):
        raise httpx.ConnectError("down")
    client, s = make_client(boom)
    d = Dispatcher(career, s, client)
    job = career.db.jobs("offered")[0]
    career.db.set_job(job.id, aircraft_id=career.db.hangar()[0].id)
    with pytest.raises(LLMError):
        d.brief_job(career.db.job(job.id))
    with pytest.raises(LLMError):
        d.react("low_fuel", "Low fuel: 5 gal remaining")
    with pytest.raises(LLMError):
        d.chat("hello")
    assert [m for m in career.db.messages() if m["role"] == "assistant"] == []


def test_an_empty_model_answer_is_an_error(career):
    client, s = make_client(lambda r: completion(""))
    with pytest.raises(LLMError, match="empty answer"):
        Dispatcher(career, s, client).chat("hello")


def test_enhance_jobs_rewrites_briefings(career):
    jobs = career.db.jobs("offered")[:2]
    payload = {str(j.id): f"Custom brief for job {j.id} from the client." for j in jobs}

    def h(req):
        return completion("Sure!\n" + json.dumps(payload))
    client, s = make_client(h)
    n = Dispatcher(career, s, client).enhance_jobs(jobs)
    assert n == 2
    assert career.db.job(jobs[0].id).briefing.startswith("Custom brief")


def test_thinking_blocks_stripped(career):
    client, s = make_client(lambda r: completion("<think>hmm let me think</think>All clear."))
    assert Dispatcher(career, s, client).chat("status?") == "All clear."
