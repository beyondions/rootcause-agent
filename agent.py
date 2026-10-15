import json
import os
from dotenv import load_dotenv
from groq import Groq

import tools

load_dotenv()
client = Groq(api_key=os.environ["API_Key"])
MODEL = "openai/gpt-oss-120b"
MAX_STEPS = 10


SYSTEM_PROMPT = """You are an on-call incident agent for an online shop.
Services and their dependencies live in a graph database. You MUST use the tools;
never guess a root cause.

Investigate in this order:
1. get_active_alerts: see what is firing.
2. find_root_cause: find which alerting service explains the others.
3. get_recent_deploys on the top candidate: look for a recent change.
4. find_blast_radius on the root cause: see what is affected.
5. get_evidence_path from the loudest alerting service to the root cause.
6. find_owner and find_similar_incidents for the root cause.
7. create_incident exactly once, for the root cause.

Then write a short answer for a tired engineer at 2 AM:
- Root cause (service + the deploy, if any)
- Evidence path (the chain of services)
- Blast radius
- Owner and suggested fix (from past incidents)
- Incident ID you opened
Mention any red herring candidate and why you ruled it out."""

def tool(name, description, properties=None, required=None):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties or {},
                "required": required or [],
            },
        },
    }

SERVICE = {"service": {"type": "string", "description": "Service name, e.g. 'cache'"}}

TOOLS = [
    tool("get_active_alerts", "List all alerts currently firing, newest first."),
    tool("find_root_cause",
         "Rank alerting services that could be the root cause. A service is ruled out "
         "if something it depends on is also alerting. Higher explains_count = more "
         "alerts explained. Includes recent deploys per candidate."),
    tool("get_recent_deploys", "Deploys to a service in the last 30 minutes.",
         SERVICE, ["service"]),
    tool("find_blast_radius",
         "All services that break if this service fails, most critical first.",
         SERVICE, ["service"]),
    tool("get_evidence_path",
         "Chain of dependencies from a symptom service down to the root cause.",
         {"from_service": {"type": "string"}, "to_service": {"type": "string"}},
         ["from_service", "to_service"]),
    tool("find_owner", "Team and engineers who own a service.", SERVICE, ["service"]),
    tool("find_similar_incidents",
         "Past incidents caused by this service, who resolved them, and the runbook used.",
         SERVICE, ["service"]),
    tool("create_incident", "Open a new incident for the root cause. Call exactly once.",
         {"root_cause": {"type": "string"},
          "title": {"type": "string"},
          "summary": {"type": "string"}},
         ["root_cause", "title", "summary"]),
]

# Map tool names to the real Python functions in tools.py
FUNCTIONS = {
    "get_active_alerts": tools.get_active_alerts,
    "find_root_cause": tools.find_root_cause,
    "get_recent_deploys": tools.get_recent_deploys,
    "find_blast_radius": tools.find_blast_radius,
    "get_evidence_path": tools.get_evidence_path,
    "find_owner": tools.find_owner,
    "find_similar_incidents": tools.find_similar_incidents,
    "create_incident": tools.create_incident,
}


def run_tool(name, args):
    """Run one tool safely; errors go back to the LLM instead of crashing."""
    try:
        return FUNCTIONS[name](**args)
    except Exception as e:
        return {"error": str(e)}


def investigate(question):
    """The agent loop. Returns the final answer and a trace of tool calls."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    trace = []

    for _ in range(MAX_STEPS):
        response = client.chat.completions.create(
            model=MODEL, messages=messages, tools=TOOLS, tool_choice="auto",
        )
        msg = response.choices[0].message

        if not msg.tool_calls:
            return msg.content, trace

        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name,
                              "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ],
        })

        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments or "{}")
            result = run_tool(tc.function.name, args)
            trace.append({"tool": tc.function.name, "args": args, "result": result})
            print(f"  → {tc.function.name}({args})")
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, default=str),
            })

    return "Stopped: too many steps without a final answer.", trace


if __name__ == "__main__":
    question = "Orders are failing. What is going on?"
    print(f"Question: {question}\n")
    answer, trace = investigate(question)
    print("\n=== ANSWER ===\n")
    print(answer)