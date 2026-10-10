import json
import random

import httpx
import pytest

from skydispatch.ai.dispatcher import Dispatcher, employer_thread
from skydispatch.ai.llm import LLMError, LMStudioClient
from skydispatch.ai.tools import schemas_for
from skydispatch.sim.installed import format_ids

T = employer_thread("bluebird")


def scripted_dispatcher(career):
    """A dispatcher whose model always answers with one short line (the tests are about the rules, not the wording)."""
    def reply(req):
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "Roger that, Captain."}}]})
    s = career.settings
    career.employer_dispatch.rng = random.Random(3)
    return Dispatcher(career, s, LMStudioClient(s.ai, transport=httpx.MockTransport(reply)))


def down_dispatcher(career):
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
    d = scripted_dispatcher(career)
    thread = hire(career, d)
    assert thread == T
    assert kinds(career) == [("hr", "text"), ("assistant", "text"), ("assistant", "ask_time")]
    assert d.is_awaiting_availability(T)
    d.ask_availability(T)                                   # asking twice does not repeat the question
    assert kinds(career).count(("assistant", "ask_time")) == 1


def test_answering_with_free_text_assigns_one_flight_that_fits(career):
    d = scripted_dispatcher(career)
    hire(career, d)
    reply = d.chat("I've got about 90 minutes today", T)   # no LLM needed: the duration is parsed locally
    assert reply == "ok"
    cards = [m for m in career.db.messages(50, T) if m["kind"] == "offer"]
    assert len(cards) == 1                                  # the dispatcher picks; there is nothing to choose between
    job = career.db.job(json.loads(cards[0]["payload"])["job_id"])
    assert job.employer_id == "bluebird" and job.status == "accepted" and job.aircraft_id is None
    assert career.db.active_job().id == job.id
    assert d.availability(T) == 90 and not d.is_awaiting_availability(T)


def test_a_second_request_is_refused_until_the_flight_is_flown_or_abandoned(career):
    d = scripted_dispatcher(career)
    hire(career, d)
    first = d.handle_availability(T, 30)["assigned"]["job_id"]
    again = d.handle_availability(T, 120)
    assert "already have a flight assigned" in again["error"]
    assert career.db.active_job().id == first
    career.abandon_job()
    second = d.handle_availability(T, 120)["assigned"]["job_id"]
    assert second != first and career.db.active_job().id == second


def test_the_assigned_flight_is_armed_and_briefed(career):
    d = scripted_dispatcher(career)
    hire(career, d)
    job = career.db.job(d.handle_availability(T, 120)["assigned"]["job_id"])
    assert career.active_job.id == job.id and career.recorder is not None
    sent, real_chat = [], d.client.chat
    d.client.chat = lambda msgs, **kw: (sent.append(msgs), real_chat(msgs, **kw))[1]
    assert d.brief_job(job) == "Roger that, Captain."          # the model writes it, from facts computed in code
    facts = sent[-1][-1]["content"].lower()
    assert job.origin.lower() in facts and "company aircraft" in facts
    assert not career.db.jobs("offered", scope="bluebird")      # nothing is left for the pilot to pick from


def test_an_expired_medical_stops_the_dispatcher_assigning_flights(career):
    d = scripted_dispatcher(career)
    hire(career, d)
    career.db.x("UPDATE certificates SET expires_at = '2020-01-01T00:00:00+00:00' WHERE kind = 'medical'")
    result = d.handle_availability(T, 60)
    assert "medical" in result["error"].lower() and career.db.active_job() is None
    assert "can't roster you" in career.db.last_message(T)["content"]


def test_old_offers_can_still_be_declined(career):
    """Offers made by earlier versions are still in some databases."""
    d = scripted_dispatcher(career)
    hire(career, d)
    d.db.set_meta(f"await:{T}", "0")                          # the question had been answered back then
    jobs = career.employer_dispatch.offer_flights(career_employer("bluebird"), 60, 2)
    for j in jobs:
        d.decline_offer(j.id, T)
    assert d.is_awaiting_availability(T)
    last = career.db.last_message(T)
    assert last["kind"] == "ask_time" and last["content"].startswith("No problem.")


def career_employer(eid):
    from skydispatch.data.employers import get_employer
    return get_employer(eid)


def test_no_flights_when_fleet_not_installed_explains_why(career):
    d = scripted_dispatcher(career)
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
        assert tool["assigned"] and tool["minutes"] == 75
        return completion("Seventy-five minutes, perfect. That one is yours.")

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
    assert "set_availability" in company and "offer_flights" not in company and "apply_to_employer" not in company


def test_tools_job_board_and_qualifications(career):
    d = scripted_dispatcher(career)
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
    d = scripted_dispatcher(career)
    career.settings.sim.installed_aircraft = format_ids({"c172", "c152"})
    names = {a["type_id"] for a in d.tools.call("list_dealer", {})["aircraft"]}
    assert names == {"c172", "c152"}
    career.db.add_transaction(9_000_000, "t", "t")
    res = d.tools.call("buy_aircraft", {"type_id": "a320", "location_icao": "EGLL", "confirmed": True})
    assert "not installed" in res["error"]


def test_hr_reply_is_an_error_when_the_model_is_down(career):
    d = down_dispatcher(career)
    r = career.apply_to_employer("bluebird")
    with pytest.raises(LLMError):
        d.hr_reply(r.employer, True, r.message)               # no stock reply


def test_company_flights_never_appear_in_the_freelance_market(career):
    d = scripted_dispatcher(career)
    hire(career, d)
    d.handle_availability(T, 60)
    assert all(j.employer_id is None for j in career.db.jobs("offered"))
    assert career.db.active_job().employer_id == "bluebird"


def test_company_flights_cannot_be_picked_with_accept_job(career):
    d = scripted_dispatcher(career)
    hire(career, d)
    d.tools.thread = T
    assert "assigned" in d.tools.call("accept_job", {"job_id": 1})["error"]


def test_freelance_tools_are_for_owner_operators_only(career):
    d = scripted_dispatcher(career)
    assert "jobs" in d.tools.call("list_jobs", {})                           # owns the starter aircraft
    career.hangar.sell(career.db.hangar()[0].id)
    assert "own an aircraft" in d.tools.call("list_jobs", {})["error"]


def test_training_and_hiring_tools(career):
    d = scripted_dispatcher(career)
    status = d.tools.call("get_status", {})
    assert status["licence_valid"] and status["medical_valid"] and status["monthly_bills"] > 0 and status["ratings"] == []
    emp = {e["employer_id"]: e for e in d.tools.call("list_employers", {})["employers"]}
    assert "Recruiting" in emp["bluebird"]["hiring"] and "Not recruiting" in emp["atlas"]["hiring"]
    t = d.tools.call("get_training", {})
    assert {c["id"] for c in t["courses"]} == {"ir", "me", "tp", "jet", "airliner"} and t["monthly_bills"]
    assert "confirm" in d.tools.call("start_course", {"course_id": "ir"})["error"]
    assert "flight hours" in d.tools.call("start_course", {"course_id": "ir", "confirmed": True})["error"]
    assert "confirm" in d.tools.call("renew_credential", {"kind": "medical"})["error"]
    assert "valid for" in d.tools.call("renew_credential", {"kind": "medical", "confirmed": True})["error"]
    assert "not recruiting" in d.tools.call("apply_to_employer", {"employer_id": "atlas"})["error"].lower()
