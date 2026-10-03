import json
import random

import httpx

from skydispatch.ai.dispatcher import Dispatcher, employer_thread
from skydispatch.ai.llm import LMStudioClient
from skydispatch.ai.tools import schemas_for
from skydispatch.sim.installed import format_ids

T = employer_thread("bluebird")


def offline_dispatcher(career):
    def boom(req):
        raise httpx.ConnectError("down")
    s = career.settings
    career.employer_dispatch.rng = random.Random(3)
    return Dispatcher(career, s, LMStudioClient(s.ai, transport=httpx.MockTransport(boom)))


def completion(content="", tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return httpx.Response(200, json={"choices": [{"message": msg}]})


def hire(career, d):
    r = career.apply_to_employer("bluebird")
    return d.start_thread(r.employer, "Welcome aboard.")


def kinds(career, thread=T):
    return [(m["role"], m["kind"]) for m in career.db.messages(50, thread)]


def test_new_hire_gets_hr_welcome_greeting_and_availability_question(career):
    d = offline_dispatcher(career)
    thread = hire(career, d)
    assert thread == T
    assert kinds(career) == [("hr", "text"), ("assistant", "text"), ("assistant", "ask_time")]
    assert d.is_awaiting_availability(T)
    d.ask_availability(T)                                   # asking twice does not repeat the question
    assert kinds(career).count(("assistant", "ask_time")) == 1


def test_answering_with_free_text_creates_offers_that_fit(career):
    d = offline_dispatcher(career)
    hire(career, d)
    reply = d.chat("I've got about 90 minutes today", T)   # no LLM needed: the duration is parsed locally
    assert reply == "ok"
    offers = [m for m in career.db.messages(50, T) if m["kind"] == "offer"]
    assert 1 <= len(offers) <= 3
    for m in offers:
        job = career.db.job(json.loads(m["payload"])["job_id"])
        assert job.employer_id == "bluebird" and job.status == "offered" and job.aircraft_id is None
    assert d.availability(T) == 90 and not d.is_awaiting_availability(T)


def test_quick_chip_path_and_replanning_replaces_old_offers(career):
    d = offline_dispatcher(career)
    hire(career, d)
    d.handle_availability(T, 30)
    first = {j.id for j in career.db.jobs("offered", scope="bluebird")}
    d.handle_availability(T, 120)
    second = {j.id for j in career.db.jobs("offered", scope="bluebird")}
    assert first and second and not (first & second)       # old offers expired, new ones created
    assert all(career.db.job(i).status == "expired" for i in first)


def test_accept_offer_starts_the_job_and_withdraws_the_others(career):
    d = offline_dispatcher(career)
    hire(career, d)
    d.handle_availability(T, 120)
    jobs = career.db.jobs("offered", scope="bluebird")
    chosen = jobs[0]
    job = d.accept_offer(chosen.id, T)
    assert job.status == "accepted" and career.db.active_job().id == chosen.id
    assert all(career.db.job(j.id).status == "expired" for j in jobs[1:])
    assert career.db.messages(50, T)[-1]["role"] == "user"
    brief = d.brief_job(job).lower()                           # offline: the template briefing
    assert job.origin.lower() in brief and "company aircraft" in brief


def test_declining_every_offer_prompts_for_new_availability(career):
    d = offline_dispatcher(career)
    hire(career, d)
    d.handle_availability(T, 60)
    for j in career.db.jobs("offered", scope="bluebird"):
        d.decline_offer(j.id, T)
    assert d.is_awaiting_availability(T)
    last = career.db.last_message(T)
    assert last["kind"] == "ask_time" and last["content"].startswith("No problem.")


def test_no_flights_when_fleet_not_installed_explains_why(career):
    d = offline_dispatcher(career)
    hire(career, d)
    career.settings.sim.installed_aircraft = format_ids({"baron"})
    result = d.handle_availability(T, 60)
    assert "installed" in result["error"]
    assert d.is_awaiting_availability(T)
    assert "installed" in career.db.last_message(T)["content"]


def test_llm_tool_flow_in_employer_thread(career):
    s = career.settings
    career.employer_dispatch.rng = random.Random(8)
    seen = {"system": ""}

    def h(req):
        body = json.loads(req.content)
        seen["system"] = body["messages"][0]["content"]
        names = [t["function"]["name"] for t in body.get("tools", [])]
        assert "set_availability" in names and "apply_to_employer" not in names
        if not any(m["role"] == "tool" for m in body["messages"]):
            return completion("", [{"id": "1", "type": "function", "function": {
                "name": "set_availability", "arguments": json.dumps({"minutes": 75})}}])
        tool = json.loads([m for m in body["messages"] if m["role"] == "tool"][0]["content"])
        assert tool["offers"] and tool["minutes"] == 75
        return completion("Seventy-five minutes, perfect. Have a look at these.")

    d = Dispatcher(career, s, LMStudioClient(s.ai, transport=httpx.MockTransport(h)))
    career.apply_to_employer("bluebird")
    d.db.set_meta(f"await:{T}", "0")                      # not the deterministic path: the model decides
    reply = d.chat("my evening is free until about half past nine", T)
    assert reply.startswith("Seventy-five")
    assert any(m["kind"] == "offer" for m in career.db.messages(50, T))
    assert "Bluebird Bush Air" in seen["system"] and "company aircraft" in seen["system"]


def test_general_thread_has_job_board_tools_and_employer_thread_does_not():
    general = {s["function"]["name"] for s in schemas_for("general")}
    company = {s["function"]["name"] for s in schemas_for(T)}
    assert {"list_employers", "apply_to_employer"} <= general and "set_availability" not in general
    assert {"set_availability", "offer_flights"} <= company and "apply_to_employer" not in company


def test_tools_job_board_and_qualifications(career):
    d = offline_dispatcher(career)
    emp = d.tools.call("list_employers", {})["employers"]
    assert len(emp) == 10 and emp[0]["qualifies"] and not emp[2]["qualifies"] and emp[2]["missing"]
    q = d.tools.call("get_qualifications", {})
    assert q["total_hours"] == 0 and "skill_level" in q
    res = d.tools.call("apply_to_employer", {"employer_id": "bluebird"})
    assert res["accepted"] is True
    assert "error" in d.tools.call("set_availability", {"minutes": 60})      # general thread: not allowed
    status = d.tools.call("get_status", {})
    assert status["location"] == "EGLL" and "recent_experience_hours" in status


def test_dealer_and_purchases_respect_installed_aircraft(career):
    d = offline_dispatcher(career)
    career.settings.sim.installed_aircraft = format_ids({"c172", "c152"})
    names = {a["type_id"] for a in d.tools.call("list_dealer", {})["aircraft"]}
    assert names == {"c172", "c152"}
    career.db.add_transaction(9_000_000, "t", "t")
    res = d.tools.call("buy_aircraft", {"type_id": "a320", "location_icao": "EGLL", "confirmed": True})
    assert "not installed" in res["error"]


def test_hr_reply_falls_back_to_facts_offline(career):
    d = offline_dispatcher(career)
    r = career.apply_to_employer("bluebird")
    assert d.hr_reply(r.employer, True, r.message) == r.message


def test_employer_offers_not_in_freelance_market(career):
    d = offline_dispatcher(career)
    hire(career, d)
    d.handle_availability(T, 60)
    assert all(j.employer_id is None for j in career.db.jobs("offered"))
    assert career.db.jobs("offered", scope="bluebird")
