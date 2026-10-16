import time

import tools
from agent import investigate, cleanup_open_incidents, find_people_to_contact
from baseline import baseline_answer


def build_answer_key():
    """Read the correct answers from the graph, so nothing is hard-coded."""
    root = tools.find_root_cause()[0]["service"]
    deploys = tools.get_recent_deploys(root)
    people = [p["name"] for p in find_people_to_contact(root)]
    return {
        "root": root,
        "deploy_ids": [d["id"] for d in deploys],
        "authors": [d["author"] for d in deploys],
        "blast": [b["service"] for b in tools.find_blast_radius(root)],
        "people": people,
    }


KEY = build_answer_key()


def has(*words):
    return lambda a, r: all(w.lower() in a.lower() for w in words)


def has_any(*words):
    return lambda a, r: any(w.lower() in a.lower() for w in words)


def starts_no(a, r):
    return a.strip().lower().startswith("no")


def incident_opened(a, r):
    return bool(r.get("incident_id"))


QUESTIONS = [
    ("What is going on? Orders are failing.", [
        ("names root cause", has(KEY["root"])),
        ("names what changed (deploy)", has_any(*KEY["deploy_ids"])),
        ("takes action (opens incident)", incident_opened),
    ]),
    ("What changed recently that caused this?", [
        ("names the deploy", has_any(*KEY["deploy_ids"])),
        ("names who made it", has_any(*KEY["authors"])),
    ]),
    (f"What else breaks if {KEY['root']} goes down?", [
        ("lists full blast radius", has(*KEY["blast"])),
    ]),
    ("Is the recommendation service causing the outage?", [
        ("answers No", starts_no),
        ("points to the real root cause", has(KEY["root"])),
    ]),
    ("Who should I wake up right now?", [
        ("names the people to contact", has(*KEY["people"][:2])),
        ("gives a reason (deploy / past fix)",
         has_any(*KEY["deploy_ids"], "deploy", "resolved", "INC-")),
    ]),
]

print("Answer key (from the graph):", KEY)


def run():
    rows, totals = [], {"graph": 0, "rag": 0, "n": 0}
    for question, checks in QUESTIONS:
        print(f"\nQ: {question}")

        cleanup_open_incidents()
        g = investigate(question, verbose=False)
        time.sleep(3)
        b = baseline_answer(question)
        b_result = {"incident_id": None}          # RAG cannot take actions
        time.sleep(3)

        for label, check in checks:
            g_ok = check(g["answer"], g)
            b_ok = check(b["answer"], b_result)
            totals["graph"] += g_ok
            totals["rag"] += b_ok
            totals["n"] += 1
            rows.append((question, label, g_ok, b_ok))
            print(f"  {label:<42} graph {'✓' if g_ok else '✗'}   rag {'✓' if b_ok else '✗'}")

        print(f"  graph: {g['answer'].splitlines()[0] if g['answer'] else '(empty)'}")
        print(f"  rag:   {b['answer'].splitlines()[0] if b['answer'] else '(empty)'}")

    cleanup_open_incidents()

    lines = ["| Question | Check | Graph agent | Vector RAG |",
             "|---|---|---|---|"]
    for q, label, g_ok, b_ok in rows:
        lines.append(f"| {q} | {label} | {'✅' if g_ok else '❌'} | {'✅' if b_ok else '❌'} |")
    lines.append(f"| **Total** | | **{totals['graph']}/{totals['n']}** | "
                 f"**{totals['rag']}/{totals['n']}** |")

    with open("compare_results.md", "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nGraph agent: {totals['graph']}/{totals['n']}   "
          f"Vector RAG: {totals['rag']}/{totals['n']}")
    print("Saved compare_results.md")


if __name__ == "__main__":
    run()