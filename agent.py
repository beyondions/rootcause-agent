import json
import os
import re
import sys
import time

from dotenv import load_dotenv
from groq import Groq, RateLimitError

import tools

load_dotenv()
client = Groq(api_key=os.environ["GROQ_API_KEY"])
MODEL = "openai/gpt-oss-120b"
# MODEL = "openai/gpt-oss-20b"

MAX_STEPS = 12

YES_NO_WORDS = {"is", "are", "was", "were", "does", "do", "did",
                "can", "could", "should", "has", "have"}

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
    """Only resolved past incidents count as history."""
    return [i for i in tools.find_similar_incidents(service)
            if i.get("resolved_by") or i.get("runbook")]


def find_people_to_contact(service):
    """Owner team + who made the recent change + who fixed it last time."""
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


def call_llm(messages, retries=4):
    """Call Groq; on rate limit, wait and retry."""
    for attempt in range(retries):
        try:
            return client.chat.completions.create(
                model=MODEL, messages=messages, tools=TOOLS,
                tool_choice="auto", temperature=0.3,
            )
        except RateLimitError:
            wait = 10 * (attempt + 1)
            print(f"  (rate limited, waiting {wait}s)")
            time.sleep(wait)
    raise RuntimeError("Groq rate limit: try again in a minute")


def summarize(trace):
    """Pull the key facts out of the tool results, for the UI."""
    out = {"root_cause": None, "evidence_path": [], "blast_radius": [], "incident_id": None}
    for step in trace:
        r = step["result"]
        if step["tool"] == "find_root_cause" and r and isinstance(r, list):
            out["root_cause"] = r[0]["service"]
        elif step["tool"] == "get_evidence_path" and r:
            out["evidence_path"] = r
        elif step["tool"] == "find_blast_radius" and isinstance(r, list):
            out["blast_radius"] = [x["service"] for x in r]
        elif step["tool"] == "create_incident" and isinstance(r, dict):
            out["incident_id"] = r.get("incident_id")
    return out


def first_line_rule(question):
    """Decide the first-line format in code instead of trusting the LLM."""
    words = question.strip().lower().split()
    first = words[0] if words else ""
    if first in {"who", "whom"}:
        return 'First line must be: "Contact <names>: <one short reason each>."'
    if first in YES_NO_WORDS:
        return 'First line must start with "Yes" or "No", then the reason.'
    return "First line must state the root cause in one sentence."


_service_names = None


def service_names():
    """All service names in the graph, longest first (so 'api-gateway' matches before 'ad')."""
    global _service_names
    if _service_names is None:
        rows = tools.graph.ro_query("MATCH (s:Service) RETURN s.name").result_set
        _service_names = sorted((r[0] for r in rows), key=len, reverse=True)
    return _service_names


def fix_yes_no(question, result):
    """For 'Is X causing...?' questions, decide Yes/No from the graph, not the LLM."""
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
    """The raw agent loop."""
    _created.clear()
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"{question}\n\n({first_line_rule(question)})"}]
    trace = []
    nudges = 0

    for _ in range(MAX_STEPS):
        msg = call_llm(messages).choices[0].message

        if not msg.tool_calls:
            content = (msg.content or "").strip()

            # Safety net: did the agent finish the required steps?
            missing = []
            if not any(s["tool"] == "get_evidence_path" and s["result"] for s in trace):
                missing.append("get_evidence_path")
            if not any(s["tool"] == "create_incident" for s in trace):
                missing.append("create_incident")

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

            return {"answer": content, "trace": trace, **summarize(trace)}

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
                             "content": json.dumps(result, default=str)})

    return {"answer": "Stopped: too many steps.", "trace": trace, **summarize(trace)}


def investigate(question, verbose=True):
    """Run the agent. Returns a dict: answer, trace, root_cause, evidence_path,
    blast_radius, incident_id."""
    return fix_yes_no(question, _investigate(question, verbose))

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
        }
        ok = all(checks.values())
        passed += ok
        print(f"  first line: {answer.splitlines()[0] if answer else '(empty)'}")
        for name, value in checks.items():
            print(f"  {'✓' if value else '✗'} {name}")
        cleanup_open_incidents()
        time.sleep(3)   # be gentle with the free-tier rate limit
    print(f"\n{passed}/{len(TESTS)} tests passed")

def print_result(result):
    print("\n=== ANSWER ===\n")
    print(result["answer"])
    print("\n=== FOR THE UI ===")
    print(json.dumps({k: v for k, v in result.items() if k not in ("answer", "trace")},
                     indent=2))

def interactive():
    print("Incident agent ready. Ask a question about the outage.")
    print("Commands: 'reset' = clear incidents opened this session, 'exit' = quit.\n")
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
            print("  (open incidents cleared)\n")
            continue
        print_result(investigate(question))
        print()

if __name__ == "__main__":
    if "--test" in sys.argv:
        run_tests()
    elif len(sys.argv) > 1:
        print_result(investigate(" ".join(sys.argv[1:])))
    else:
        interactive()