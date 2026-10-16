import json
import os
import re
import sys
import time

from dotenv import load_dotenv
from groq import Groq

import tools
from llm import chat

load_dotenv()
if not os.getenv("GROQ_API_KEY"):
    sys.exit("GROQ_API_KEY is not set")
client = Groq(api_key=os.environ["GROQ_API_KEY"])

MAX_STEPS = 12
CACHE_SECONDS = 600
NO_PAST_FIX = "No past fix found in the graph."
DROP_FIELDS = {
    "get_active_alerts": {"id", "fired_at"},
    "find_root_cause": {"time"},
    "get_recent_deploys": {"time"},
    "find_blast_radius": {"tier"},
    "find_similar_incidents": {"title"},
}

YES_NO_WORDS = {"is", "are", "was", "were", "does", "do", "did",
                "can", "could", "should", "has", "have"}
IMPACT_WORDS = ("break", "affected", "impact", "what happens if")
ANSWER_LABELS = ("root cause:", "evidence path:", "blast radius:", "contact:",
                 "suggested fix:", "incident:", "red herring:")

SYSTEM_PROMPT = """You are an on-call incident agent for an online shop.
Services and their dependencies live in a graph database. You MUST use the tools;
never guess a root cause.

Investigate in this order:
1. get_active_alerts
2. find_root_cause  (the top candidate is the root cause unless the data clearly says otherwise)
3. get_recent_deploys on the root cause, if find_root_cause did not already include them
4. find_blast_radius on the root cause
5. get_evidence_path to the root cause. Start from the service the user asked about if it
   is alerting and a path exists; otherwise start from the most severe alerting service.
6. find_people_to_contact and find_similar_incidents for the root cause
7. create_incident once, for the root cause
Call independent tools in the same step when you can.

STRICT RULES:
- Only state facts that came back from a tool. Never invent steps, commands, quotes,
  numbers or names. Quote runbook titles exactly.
- Never tell the user to wait or do nothing while tier-1 services are failing.

ANSWER FORMAT:
First line: follow the first-line instruction given with the question.
Then these lines:
Root cause: <service> (<deploy id>: <change>, by <author>)
Evidence path: <a> → <b> → ...
Blast radius: <services, most critical first>
Contact: <people and reasons, from find_people_to_contact>
Suggested fix: <runbook title from the similar past incident>
Incident: <incident id>
Red herring: <other candidate and why the graph ruled it out>  (only if one exists)"""

def find_similar_incidents(service):
    return [i for i in tools.find_similar_incidents(service)
            if i.get("resolved_by") or i.get("runbook")]


def find_people_to_contact(service):
    people = {}

    def add(name, reason):
        people.setdefault(name, []).append(reason)

    for owner in tools.find_owner(service):
        for engineer in owner["engineers"]:
            add(engineer, f"member of {owner['team']}, the team that owns {service}")
    for deploy in tools.get_recent_deploys(service):
        add(deploy["author"], f"authored deploy {deploy['id']} ({deploy['change']})")
    for inc in find_similar_incidents(service):
        if inc.get("resolved_by"):
            add(inc["resolved_by"], f"resolved similar incident {inc['id']}")

    ranked = sorted(people.items(), key=lambda kv: len(kv[1]), reverse=True)
    return [{"name": name, "reasons": reasons} for name, reasons in ranked]


_created = {} 

def create_incident(root_cause, title, summary):
    if root_cause in _created:
        return {"incident_id": _created[root_cause], "note": "already created"}
    incident_id = tools.create_incident(root_cause, title, summary)
    _created[root_cause] = incident_id
    return {"incident_id": incident_id}

def tool(name, description, properties=None, required=None):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object",
                       "properties": properties or {},
                       "required": required or []}}}


SERVICE = {"service": {"type": "string", "description": "Service name, e.g. 'cache'"}}

TOOLS = [
    tool("get_active_alerts", "List all alerts currently firing, newest first."),
    tool("find_root_cause",
         "Rank alerting services that could be the root cause. A service is ruled out if "
         "something it depends on is also alerting. Higher explains_count = more alerts "
         "explained. Includes recent deploys per candidate. First item = most likely cause."),
    tool("get_recent_deploys", "Deploys to a service in the last 30 minutes.",
         SERVICE, ["service"]),
    tool("find_blast_radius", "Services that break if this service fails, most critical first.",
         SERVICE, ["service"]),
    tool("get_evidence_path",
         "Dependency chain from a symptom service down to the root cause. Empty list = no path.",
         {"from_service": {"type": "string"}, "to_service": {"type": "string"}},
         ["from_service", "to_service"]),
    tool("find_people_to_contact",
         "Who to contact for a service, with reasons: owners, author of the recent deploy, "
         "and whoever resolved a similar past incident. First person = strongest match.",
         SERVICE, ["service"]),
    tool("find_similar_incidents",
         "Resolved past incidents caused by this service, who fixed them and the runbook used.",
         SERVICE, ["service"]),
    tool("create_incident", "Open a new incident for the root cause. Call once.",
         {"root_cause": {"type": "string"}, "title": {"type": "string"},
          "summary": {"type": "string"}},
         ["root_cause", "title", "summary"]),
]

FUNCTIONS = {
    "get_active_alerts": tools.get_active_alerts,
    "find_root_cause": tools.find_root_cause,
    "get_recent_deploys": tools.get_recent_deploys,
    "find_blast_radius": tools.find_blast_radius,
    "get_evidence_path": tools.get_evidence_path,
    "find_people_to_contact": find_people_to_contact,
    "find_similar_incidents": find_similar_incidents,
    "create_incident": create_incident,
}

def run_tool(name, args):
    if name not in FUNCTIONS:
        return {"error": f"unknown tool {name}"}
    try:
        return FUNCTIONS[name](**args)
    except Exception as e:
        return {"error": str(e)}


def call_llm(messages):
    return chat(client, messages=messages, tools=TOOLS, tool_choice="auto",
                temperature=0.3, reasoning_effort="low")


def compact(tool_name, result):
    drop = DROP_FIELDS.get(tool_name, set())

    def strip(x):
        if isinstance(x, dict):
            return {k: strip(v) for k, v in x.items() if k not in drop}
        if isinstance(x, list):
            return [strip(v) for v in x]
        return x

    return json.dumps(strip(result), default=str, separators=(",", ":"))


def is_error(result):
    return isinstance(result, dict) and "error" in result


def summarize(trace):
    out = {"root_cause": None, "evidence_path": [], "blast_radius": [], "incident_id": None}
    for step in trace:
        r = step["result"]
        if step["tool"] == "find_root_cause" and r and isinstance(r, list):
            out["root_cause"] = r[0]["service"]
        elif step["tool"] == "get_evidence_path" and r and isinstance(r, list):
            out["evidence_path"] = r
        elif step["tool"] == "find_blast_radius" and isinstance(r, list):
            out["blast_radius"] = [x["service"] for x in r]
        elif step["tool"] == "create_incident" and isinstance(r, dict):
            out["incident_id"] = r.get("incident_id")
    return out


def facts_from_trace(trace):
    facts = summarize(trace)
    root = facts["root_cause"]
    facts.update({"deploy": None, "blast_service": None, "people": [], "runbook": None})
    for step in trace:
        r, service = step["result"], step["args"].get("service")
        if not r or not isinstance(r, list):
            continue
        if step["tool"] == "find_root_cause" and r[0].get("recent_deploys"):
            facts["deploy"] = r[0]["recent_deploys"][0]
        elif step["tool"] == "get_recent_deploys" and service == root:
            facts["deploy"] = r[0]
        elif step["tool"] == "find_blast_radius":
            facts["blast_service"] = service
        elif step["tool"] == "find_people_to_contact" and service == root:
            facts["people"] = r
        elif step["tool"] == "find_similar_incidents" and service == root:
            facts["runbook"] = next((i["runbook"] for i in r if i.get("runbook")), None)
    return facts


def fallback_answer(trace):
    f = facts_from_trace(trace)
    if not f["root_cause"]:
        return None
    d = f["deploy"]
    lines = [f"Root cause: {f['root_cause']}"
             + (f" ({d['id']}: {d['change']}, by {d['author']})" if d else "")]
    if f["evidence_path"]:
        lines.append("Evidence path: " + " → ".join(f["evidence_path"]))
    if f["blast_radius"]:
        lines.append("Blast radius: " + ", ".join(f["blast_radius"]))
    if f["people"]:
        lines.append("Contact: " + "; ".join(f"{p['name']} ({', '.join(p['reasons'])})"
                                             for p in f["people"]))
    if f["runbook"]:
        lines.append(f"Suggested fix: {f['runbook']}")
    if f["incident_id"]:
        lines.append(f"Incident: {f['incident_id']}")
    return "\n".join(lines)


def finish(trace, content, stats):
    result = {"answer": content, "trace": trace, **summarize(trace), "fallback": False,
              "model": stats["model"], "tokens": stats["tokens"],
              "llm_calls": stats["calls"], "cached": False}
    if not content:
        result["answer"] = fallback_answer(trace) or "Stopped: the model gave no answer."
        result["fallback"] = True
    return result


def question_kind(question):
    q = question.strip().lower()
    first = q.split()[0] if q else ""
    if first in YES_NO_WORDS:
        return "yes_no"
    if first in {"who", "whom"}:
        return "who"
    if any(w in q for w in IMPACT_WORDS):
        return "impact"
    return "cause"


def answers_question(kind, line, facts):
    low = line.lower()
    if kind == "who":
        people = facts["people"]
        return low.startswith("contact") and bool(people) and people[0]["name"].lower() in low
    if not low or low.startswith(ANSWER_LABELS):
        return False
    if kind == "impact":
        return all(s in low for s in facts["blast_radius"])
    return facts["root_cause"] in low


def code_first_line(kind, facts):
    root = facts["root_cause"]
    if not root:
        return None
    if kind == "who":
        if not facts["people"]:
            return None
        return "Contact " + "; ".join(f"{p['name']}: {p['reasons'][0]}"
                                      for p in facts["people"]) + "."
    if kind == "impact":
        blast = facts["blast_radius"]
        if not blast:
            return None
        return (f"If {facts['blast_service'] or root} fails, {len(blast)} services break: "
                f"{', '.join(blast)}.")
    d = facts["deploy"]
    if d:
        return f"{root} is the root cause: {d['id']} ({d['change']}) by {d['author']}."
    return f"{root} is the root cause."


def fix_first_line(question, result):
    kind = question_kind(question)
    if kind == "yes_no":
        return result
    facts = facts_from_trace(result["trace"])
    lines = result["answer"].splitlines()
    first = lines[0].strip() if lines else ""
    if answers_question(kind, first, facts):
        return result
    line = code_first_line(kind, facts)
    if line:
        result["answer"] = "\n".join([line] + lines)
    return result


def past_fix_line(result):
    root = result["root_cause"]
    incidents = next((s["result"] for s in reversed(result["trace"])
                      if s["tool"] == "find_similar_incidents"
                      and s["args"].get("service") == root
                      and isinstance(s["result"], list)), None)
    if incidents is None:
        incidents = find_similar_incidents(root)
        result["trace"].append({"tool": "find_similar_incidents", "args": {"service": root},
                                "result": incidents, "called_by": "code"})
    inc = next((i for i in incidents if i.get("runbook")), None)
    if not inc:
        return f"Suggested fix: {NO_PAST_FIX}"
    source = f"used in {inc['id']}"
    if inc.get("resolved_by"):
        source += f", resolved by {inc['resolved_by']}"
    return f"Suggested fix: {inc['runbook']} ({source})"


def fix_suggested_fix(result):
    if not result["root_cause"]:
        return result
    line = past_fix_line(result)
    out, placed = [], False
    for existing in result["answer"].splitlines():
        label = existing.strip().lstrip("*-• ").lower()
        if label.startswith("suggested fix"):
            if not placed:
                out.append(line)
                placed = True
            continue
        if label.startswith("incident:") and not placed:
            out.append(line)
            placed = True
        out.append(existing)
    if not placed:
        out.append(line)
    result["answer"] = "\n".join(out)
    return result


def expected_runbook():
    root = tools.find_root_cause()[0]["service"]
    return next((i["runbook"] for i in find_similar_incidents(root) if i.get("runbook")),
                NO_PAST_FIX)


def first_line_rule(question):
    words = question.strip().lower().split()
    first = words[0] if words else ""
    if first in {"who", "whom"}:
        return 'First line must be: "Contact <names>: <one short reason each>."'
    if first in YES_NO_WORDS:
        return 'First line must start with "Yes" or "No", then the reason.'
    return "First line must state the root cause in one sentence."


_service_names = None


def service_names():
    global _service_names
    if _service_names is None:
        rows = tools.graph.ro_query("MATCH (s:Service) RETURN s.name").result_set
        _service_names = sorted((r[0] for r in rows), key=len, reverse=True)
    return _service_names


def fix_yes_no(question, result):
    words = question.strip().lower().split()
    root = result.get("root_cause")
    if not words or words[0] not in YES_NO_WORDS or not root:
        return result
    asked = next((s for s in service_names()
                  if re.search(rf"\b{re.escape(s)}\b", question.lower())), None)
    if not asked:
        return result
    if asked == root:
        first = f"Yes, {root} is the root cause."
    else:
        first = f"No, {asked} is not the root cause; {root} is."
    lines = result["answer"].splitlines()
    result["answer"] = "\n".join([first] + lines[1:])
    return result

def _investigate(question, verbose=True):
    _created.clear()
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"{question}\n\n({first_line_rule(question)})"}]
    trace = []
    nudges = 0
    stats = {"model": None, "tokens": 0, "calls": 0}

    for _ in range(MAX_STEPS):
        response = call_llm(messages)
        stats["calls"] += 1
        stats["model"] = response.model
        stats["tokens"] += response.usage.total_tokens if response.usage else 0
        msg = response.choices[0].message

        if not msg.tool_calls:
            content = (msg.content or "").strip()

            missing = [name for name in ("get_evidence_path", "create_incident")
                       if not any(s["tool"] == name and s["result"] and not is_error(s["result"])
                                  for s in trace)]

            if (missing or not content) and nudges < 3:
                nudges += 1
                if verbose:
                    print(f"  (nudge: missing {missing or ['final answer']})")
                if content:
                    messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content":
                    "You are not finished. "
                    + (f"Still required: {', '.join(missing)}. Call these tools. " if missing else "")
                    + "Then write the final answer in the required format."})
                continue

            return finish(trace, content, stats)

        messages.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [{"id": tc.id, "type": "function",
                                         "function": {"name": tc.function.name,
                                                      "arguments": tc.function.arguments}}
                                        for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result = run_tool(tc.function.name, args)
            trace.append({"tool": tc.function.name, "args": args, "result": result})
            if verbose:
                print(f"  → {tc.function.name}({args})")
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": compact(tc.function.name, result)})

    return finish(trace, "", stats)


def investigate(question, verbose=True):
    result = fix_yes_no(question, _investigate(question, verbose))
    result = fix_first_line(question, result)
    result = fix_suggested_fix(result)
    print(f"  (tokens: {result['tokens']}, llm calls: {result['llm_calls']}, "
          f"model: {result['model']})")
    return result


_answer_cache = {}


def normalize(question):
    return " ".join(re.findall(r"[a-z0-9-]+", question.lower()))


def ask(question):
    key = normalize(question)
    hit = _answer_cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        print("  (cached answer, 0 tokens)")
        return {**hit[1], "tokens": 0, "llm_calls": 0, "cached": True}
    result = investigate(question)
    if not result["fallback"]:
        _answer_cache[key] = (time.time(), result)
    return result

TESTS = [
    ("Orders are failing. What is going on?",
     lambda a: "cache" in a.lower()),
    ("Why is checkout so slow?",
     lambda a: "cache" in a.splitlines()[0].lower()),
    ("Is the recommendation service causing the outage?",
     lambda a: a.lower().startswith("no")),
    ("Who should I wake up right now?",
     lambda a: "priya" in a.splitlines()[0].lower()
               and not a.lower().startswith("no")),
]

def cleanup_open_incidents():
    tools.graph.query("MATCH (i:Incident {status: 'open'}) DETACH DELETE i")

def run_tests():
    cleanup_open_incidents()
    runbook = expected_runbook()
    passed = 0
    for question, check_first_line in TESTS:
        print(f"\nQ: {question}")
        result = investigate(question, verbose=False)
        answer = result["answer"]
        checks = {
            "answer ok": bool(answer) and check_first_line(answer),
            "root cause = cache": result["root_cause"] == "cache",
            "one incident": result["incident_id"] is not None and len(_created) == 1,
            "used tools": len(result["trace"]) >= 4,
            "runbook from graph": runbook.lower() in answer.lower(),
        }
        ok = all(checks.values())
        passed += ok
        print(f"  first line: {answer.splitlines()[0] if answer else '(empty)'}")
        for name, value in checks.items():
            print(f"  {'✓' if value else '✗'} {name}")
        cleanup_open_incidents()
        time.sleep(3)
    print(f"\n{passed}/{len(TESTS)} tests passed")

def print_result(result):
    print("\n=== ANSWER ===\n")
    print(result["answer"])
    print("\n=== FOR THE UI ===")
    print(json.dumps({k: v for k, v in result.items() if k not in ("answer", "trace")},
                     indent=2))

def interactive():
    print("Incident agent ready. Ask a question about the outage.")
    print("Commands: 'reset' = clear open incidents and cached answers, 'exit' = quit.\n")
    while True:
        try:
            question = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question:
            continue
        if question.lower() in {"exit", "quit", "q"}:
            break
        if question.lower() == "reset":
            cleanup_open_incidents()
            _answer_cache.clear()
            print("  (open incidents and cached answers cleared)\n")
            continue
        try:
            print_result(ask(question))
        except Exception as e:
            print(f"  (error: {e})")
        print()

if __name__ == "__main__":
    tools.check_graph()
    if "--test" in sys.argv:
        run_tests()
    elif len(sys.argv) > 1:
        print_result(investigate(" ".join(sys.argv[1:])))
    else:
        interactive()