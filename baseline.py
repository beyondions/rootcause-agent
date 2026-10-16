"""
baseline.py: plain vector-RAG baseline, for comparison with the graph agent.

Same data as the graph, but stored as flat text documents.
Retrieval = TF-IDF vector similarity (top-k). The LLM answers ONLY from the
retrieved text: no graph, no tools, no multi-hop traversal.
"""
import math
import os
import re
import sys
from collections import Counter
from tokenize import tokenize

from dotenv import load_dotenv
from groq import Groq

from load_data import (SERVICES, DEPENDS_ON, TEAMS, ENGINEERS,
                       DEPLOYS, ALERTS, PAST_INCIDENTS)

load_dotenv()
client = Groq(api_key=os.environ["GROQ_API_KEY"])
MODEL = "openai/gpt-oss-120b"
TOP_K = 8


def clock(minutes):
    h, m = divmod(minutes, 60)
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


# ---------------- 1. Turn the same data into text documents ----------------

def build_documents():
    docs = []
    for name, tier in SERVICES:
        docs.append(f"Service {name} is a tier {tier} service.")
    for caller, callee in DEPENDS_ON:
        docs.append(f"Service {caller} depends on service {callee}.")
    for team, services in TEAMS:
        docs.append(f"Team {team} owns: {', '.join(services)}.")
    for engineer, team in ENGINEERS:
        docs.append(f"{engineer} is an engineer on the {team} team.")
    for dep_id, svc, change, t, author in DEPLOYS:
        docs.append(f"Deploy {dep_id} to {svc} at {clock(t)} by {author}: {change}.")
    for alert_id, svc, msg, sev, t in ALERTS:
        docs.append(f"Alert {alert_id} ({sev}) on {svc} at {clock(t)}: {msg}.")
    for inc_id, title, svc, resolver, runbook in PAST_INCIDENTS:
        docs.append(f"Past incident {inc_id}: {title}. Cause: {svc}. "
                    f"Resolved by {resolver} using runbook '{runbook}'.")
    return docs


STOPWORDS = {"is", "are", "the", "a", "an", "on", "at", "of", "to", "in", "and",
             "or", "by", "for", "what", "why", "who", "how", "going", "so",
             "should", "i", "me", "my", "it", "this", "that", "right", "now",
             "service", "team", "engineer"}


def stem(word):
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            break
    return word.rstrip("e") if len(word) > 3 else word


def tokenize(text):
    return [stem(t) for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOPWORDS]


class VectorIndex:
    def __init__(self, docs):
        self.docs = docs
        tokenized = [tokenize(d) for d in docs]
        df = Counter(term for toks in tokenized for term in set(toks))
        n = len(docs)
        self.idf = {t: math.log((n + 1) / (c + 1)) + 1 for t, c in df.items()}
        self.vectors = [self._vector(toks) for toks in tokenized]

    def _vector(self, tokens):
        tf = Counter(tokens)
        vec = {t: c * self.idf.get(t, 0) for t, c in tf.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1
        return {t: v / norm for t, v in vec.items()}

    def search(self, query, k=TOP_K):
        q = self._vector(tokenize(query))
        scored = [(sum(q.get(t, 0) * w for t, w in vec.items()), doc)
                  for vec, doc in zip(self.vectors, self.docs)]
        scored.sort(reverse=True)
        return [doc for score, doc in scored[:k] if score > 0]


INDEX = VectorIndex(build_documents())

def baseline_answer(question):
    # A realistic RAG bot searches with the question PLUS the current alerts.
    alert_text = " ".join(f"{svc} {msg}" for _, svc, msg, _, _ in ALERTS)
    retrieved = INDEX.search(f"{question} {alert_text}", k=TOP_K)

    context = "\n".join(f"- {d}" for d in retrieved)
    response = client.chat.completions.create(
        model=MODEL,
        temperature=0.3,
        messages=[
            {"role": "system", "content":
                "You are an on-call assistant. Answer the user's question directly, "
                "using ONLY the context below. If the context doesn't contain the answer, say so."},
            {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
        ],
    )
    return {"answer": (response.choices[0].message.content or "").strip(),
            "retrieved": retrieved}

if __name__ == "__main__":
    question = " ".join(sys.argv[1:]) or "Orders are failing. What is going on?"
    result = baseline_answer(question)
    print(f"Question: {question}\n")
    print("=== RETRIEVED (top-k by vector similarity) ===")
    for doc in result["retrieved"]:
        print(f"  - {doc}")
    print("\n=== BASELINE ANSWER ===\n")
    print(result["answer"])