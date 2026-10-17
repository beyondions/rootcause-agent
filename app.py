import html
import os
import re
import time

import streamlit as st
from dotenv import load_dotenv

CONFIG_KEYS = ["GROQ_API_KEY", "MODELS", "FALKORDB_HOST", "FALKORDB_PORT",
               "FALKORDB_USERNAME", "FALKORDB_PASSWORD", "FALKORDB_GRAPH"]
DEFAULT_QUESTION = "Checkout is failing. What's the root cause and who do I wake up?"
SEV_SHORT = {"critical": "crit", "high": "high", "warning": "warn"}

st.set_page_config(page_title="RootCause Agent", page_icon=":material/hub:",
                   layout="wide", initial_sidebar_state="collapsed")

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');
:root {
  --bg:#0b0f17; --panel:#121826; --panel2:#161e2e; --border:#232c3d;
  --text:#e6e9ef; --muted:#8b95a7; --faint:#5d6879;
  --red:#f05252; --red-soft:rgba(240,82,82,.14); --amber:#f59e0b; --amber-soft:rgba(245,158,11,.14);
  --green:#22c55e; --green-soft:rgba(34,197,94,.14); --blue:#60a5fa; --blue-soft:rgba(96,165,250,.14);
  --mono:"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
}
html, body { overflow-x:hidden; }
.stApp { font-family:Inter, -apple-system, "Segoe UI", Roboto, sans-serif;
  background:radial-gradient(1100px 480px at 8% -12%, rgba(240,82,82,.08), transparent 60%), var(--bg); }
header[data-testid="stHeader"], [data-testid="stToolbar"], footer, #MainMenu,
[data-testid="stSidebarCollapsedControl"], [data-testid="stDecoration"] { display:none !important; }
.block-container, [data-testid="stMainBlockContainer"] { padding-top:1.1rem !important;
  padding-bottom:1.5rem !important; max-width:1680px; }

.st-key-alerts, .st-key-graph, .st-key-diag, .st-key-askbar {
  background:var(--panel); border:1px solid var(--border); border-radius:14px; padding:14px 16px 10px; }
.st-key-askbar { padding:12px 14px 4px; margin-bottom:6px; }
.st-key-alerts, .st-key-graph, .st-key-diag { margin-bottom:18px; }
[data-testid="stForm"] { border:none !important; padding:0 !important; }
.stTextInput input { background:var(--panel2) !important; border-radius:10px !important;
  height:46px; font-size:15px; }
.stFormSubmitButton button { height:46px; border-radius:10px; font-weight:600; }
.st-key-reset button { height:34px; min-height:34px; border-radius:999px; font-size:12px; font-weight:600;
  background:var(--panel); border:1px solid var(--border); color:var(--muted); }
.st-key-reset button:hover { color:var(--text); border-color:var(--red); }
[data-testid="stGraphVizChart"] svg { max-width:100%; height:auto; }
[data-baseweb="tab-list"] { gap:6px; }
[data-baseweb="tab"] { font-weight:600; }

.rc-header { display:flex; align-items:center; gap:14px; }
.rc-logo { width:38px; height:38px; border-radius:11px; flex:none;
  background:conic-gradient(from 200deg, var(--red), #f97316, var(--red));
  box-shadow:0 0 0 4px rgba(240,82,82,.12); position:relative; }
.rc-logo:after { content:""; position:absolute; inset:11px; border-radius:50%; background:var(--bg); }
.rc-title { font-size:22px; font-weight:700; letter-spacing:-.02em; color:var(--text); line-height:1.1; }
.rc-sub { color:var(--muted); font-size:13px; margin-top:2px; }
.rc-pills { display:flex; flex-wrap:wrap; gap:8px; justify-content:flex-end; }
.pill { display:inline-flex; align-items:center; gap:7px; padding:6px 11px; border-radius:999px;
  background:var(--panel); border:1px solid var(--border); color:var(--muted); font-size:12px; white-space:nowrap; }
.pill b { color:var(--text); font-weight:600; }
.dot { width:7px; height:7px; border-radius:50%; background:var(--green); box-shadow:0 0 0 3px var(--green-soft); }

.kpis { display:grid; grid-template-columns:repeat(5, minmax(0,1fr)); gap:16px; margin:6px 0 18px; }
@media (max-width:1100px) { .kpis { grid-template-columns:repeat(2, minmax(0,1fr)); } }
.kpi { background:var(--panel); border:1px solid var(--border); border-radius:14px; padding:12px 16px; min-width:0; }
.kpi-label { color:var(--muted); font-size:11px; font-weight:600; letter-spacing:.08em; text-transform:uppercase; }
.kpi-value { color:var(--text); font-size:24px; font-weight:700; letter-spacing:-.02em; margin-top:4px;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.kpi-sub { color:var(--muted); font-size:12px; margin-top:2px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.kpi.red { border-color:rgba(240,82,82,.45); background:linear-gradient(180deg, var(--red-soft), var(--panel) 70%); }
.kpi.red .kpi-value { color:#ff7b74; }
.kpi.amber .kpi-value { color:#fbbf24; }
.kpi.none .kpi-value { color:var(--faint); }

.panel-title { display:flex; align-items:center; justify-content:space-between; margin-bottom:10px; }
.panel-title h3 { font-size:13px !important; font-weight:700 !important; letter-spacing:.06em;
  text-transform:uppercase; color:var(--muted) !important; margin:0 !important; padding:0 !important; }
.count { background:var(--panel2); border:1px solid var(--border); color:var(--text);
  border-radius:999px; font-size:11px; padding:2px 9px; font-weight:600; }

.alert { display:flex; gap:10px; padding:7px 10px; border-radius:10px; background:var(--panel2);
  border:1px solid transparent; margin-bottom:8px; }
.alert.is-root { border-color:rgba(240,82,82,.55); background:linear-gradient(90deg, var(--red-soft), var(--panel2)); }
.alert.is-ruled { opacity:.6; border-style:dashed; border-color:#3a4456; }
.sev { width:4px; border-radius:4px; flex:none; }
.sev-critical { background:var(--red); } .sev-high { background:var(--amber); } .sev-warning { background:#64748b; }
.alert-body { min-width:0; flex:1; }
.alert-top { display:flex; flex-wrap:wrap; justify-content:space-between; align-items:center; gap:4px 8px;
  font-size:13px; color:var(--text); }
.alert-top b { white-space:nowrap; }
.alert .t { color:var(--faint); font-family:var(--mono); font-size:11px; white-space:nowrap; }
.alert-bottom { display:flex; justify-content:space-between; align-items:center; gap:8px; margin-top:2px; }
.alert-msg { color:var(--muted); font-size:12px; min-width:0; }
.alert-meta { display:flex; gap:4px; flex-wrap:wrap; }
.tag { font-size:10px; font-weight:700; letter-spacing:.06em; text-transform:uppercase;
  padding:2px 7px; border-radius:6px; background:var(--panel); color:var(--muted); border:1px solid var(--border); }
.tag-critical { color:#ff8a83; background:var(--red-soft); border-color:transparent; }
.tag-high { color:#fbbf24; background:var(--amber-soft); border-color:transparent; }
.tag-root { color:#fff; background:var(--red); border-color:transparent; }
.tag-symptom { color:#fbbf24; border-color:rgba(245,158,11,.35); }
.tag-dep { color:#93c5fd; border-color:rgba(96,165,250,.4); }
.sw-dep { background:#10233a; border:1.5px solid #93c5fd; }
.sw-caller { background:#1f2633; border:1.5px solid #6b7280; }
.sw-alert-outline { background:transparent; border:2px solid var(--amber); }

.legend { display:flex; flex-wrap:wrap; justify-content:flex-end; gap:4px 10px; color:var(--muted); font-size:10.5px; }
.legend span { display:inline-flex; align-items:center; gap:6px; }
.sw { width:11px; height:11px; border-radius:3px; display:inline-block; }
.sw-root { background:var(--red); } .sw-blast { background:var(--amber); }
.sw-herring { border:1.5px dashed #6b7280; } .sw-path { width:16px; height:3px; background:var(--red); border-radius:2px; }
.sw-alert { background:#3b2a0a; border:1.5px solid var(--amber); }
.sw-asked { background:#0f1d33; border:2px solid var(--blue); }
.kpi.blue { border-color:rgba(96,165,250,.45); background:linear-gradient(180deg, var(--blue-soft), var(--panel) 70%); }
.kpi.blue .kpi-value { color:#93c5fd; }
.asked-name { font-size:24px; font-weight:700; color:#93c5fd; letter-spacing:-.02em; line-height:1.1; }
.status-row { display:flex; flex-wrap:wrap; gap:6px; margin-top:7px; }
.chip.green { background:var(--green-soft); border-color:rgba(34,197,94,.35); color:#86efac; }
.chip.blue { background:var(--blue-soft); border-color:rgba(96,165,250,.35); color:#bfdbfe; }
.note { color:var(--muted); font-size:12.5px; line-height:1.45; }

.sec { padding:9px 0; border-top:1px solid var(--border); }
.sec:first-of-type { border-top:none; padding-top:2px; }
.sec-label { color:var(--faint); font-size:10.5px; font-weight:700; letter-spacing:.09em; text-transform:uppercase; margin-bottom:7px; }
.root-name { font-size:26px; font-weight:700; color:#ff7b74; letter-spacing:-.02em; line-height:1.1; }
.deploy { margin-top:7px; display:flex; gap:8px; align-items:flex-start; color:var(--text); font-size:13px; }
.deploy code, .mono { font-family:var(--mono); font-size:11.5px; background:var(--red-soft); color:#ff9f99;
  padding:2px 6px; border-radius:6px; white-space:nowrap; }
.muted { color:var(--muted); }
.chain { display:flex; flex-wrap:wrap; align-items:center; gap:6px; }
.chip { font-size:12px; padding:4px 9px; border-radius:7px; background:var(--panel2); border:1px solid var(--border); color:var(--text); }
.chip.red { background:var(--red); border-color:var(--red); color:#fff; font-weight:600; }
.chip.amber { background:var(--amber-soft); border-color:rgba(245,158,11,.35); color:#fcd34d; }
.arrow { color:var(--red); font-size:12px; }
.person { display:flex; gap:10px; align-items:center; margin-bottom:7px; }
.avatar { width:30px; height:30px; border-radius:50%; flex:none; display:flex; align-items:center; justify-content:center;
  font-weight:700; font-size:13px; color:#fff; background:linear-gradient(135deg, #6366f1, #8b5cf6); }
.pname { color:var(--text); font-size:13.5px; font-weight:600; }
.preason { color:var(--muted); font-size:12px; line-height:1.4; }
.fix { background:var(--green-soft); border:1px solid rgba(34,197,94,.35); border-radius:10px; padding:9px 11px; }
.fix-title { color:#86efac; font-weight:600; font-size:14px; }
.fix-src { color:var(--muted); font-size:12px; margin-top:2px; }
.ruled { color:var(--muted); font-size:12.5px; line-height:1.45; }
.ruled b { color:var(--text); font-weight:600; }

.empty { text-align:left; padding:6px 2px 4px; }
.empty-title { color:var(--text); font-size:15px; font-weight:600; margin-bottom:10px; }
.step { display:flex; gap:10px; align-items:flex-start; color:var(--muted); font-size:13px; margin-bottom:9px; }
.step i { font-style:normal; width:22px; height:22px; border-radius:50%; flex:none; display:flex; align-items:center;
  justify-content:center; background:var(--panel2); border:1px solid var(--border); color:var(--text); font-size:11px; font-weight:700; }

.answer { white-space:pre-wrap; word-break:break-word; font-family:var(--mono); font-size:12.5px; line-height:1.6;
  color:var(--text); background:var(--panel); border:1px solid var(--border); border-radius:12px; padding:14px 16px; }
.meta { display:flex; flex-wrap:wrap; gap:6px; margin:2px 0 10px; }
.badge { font-size:11px; font-weight:600; padding:3px 9px; border-radius:999px; border:1px solid var(--border);
  color:var(--muted); background:var(--panel); }
.badge.blue { color:#93c5fd; background:var(--blue-soft); border-color:transparent; }
.badge.green { color:#86efac; background:var(--green-soft); border-color:transparent; }
.badge.amber { color:#fcd34d; background:var(--amber-soft); border-color:transparent; }
.trace { list-style:none; padding:0; margin:0; counter-reset:t; }
.trace li { counter-increment:t; display:flex; gap:10px; align-items:baseline; padding:7px 10px; border-radius:9px;
  background:var(--panel); border:1px solid var(--border); margin-bottom:8px; font-size:12.5px; word-break:break-word; }
.trace li:before { content:counter(t); color:var(--faint); font-family:var(--mono); font-size:11px; min-width:16px; }
.trace code { font-family:var(--mono); color:#93c5fd; background:none; padding:0; }
.trace .args { color:var(--muted); font-family:var(--mono); font-size:11.5px; }
.docs { margin:10px 0 0; padding-left:18px; color:var(--muted); font-size:12px; line-height:1.55; }
.docs li { font-size:12.5px !important; color:var(--muted); margin:0 0 3px !important; }
.col-head { color:var(--muted); font-size:12px; font-weight:700; letter-spacing:.06em; text-transform:uppercase; margin:2px 0 8px; }
</style>
"""


def load_config():
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    try:
        secrets = dict(st.secrets)
    except Exception:
        secrets = {}
    for key in CONFIG_KEYS:
        if key in secrets:
            os.environ[key] = str(secrets[key])


def render(markup):
    st.markdown(markup, unsafe_allow_html=True)


esc = html.escape

st.markdown(CSS, unsafe_allow_html=True)
load_config()

if not os.getenv("GROQ_API_KEY"):
    st.error("GROQ_API_KEY is not set. Add it to .streamlit/secrets.toml or .env.")
    st.stop()

try:
    import tools
    import agent
    import llm
except SystemExit as e:
    st.error(str(e))
    st.stop()
except Exception as e:
    st.error(f"Cannot connect to FalkorDB ({e}). Is it running?")
    st.stop()

try:
    tools.check_graph()
except SystemExit as e:
    st.error(str(e))
    st.stop()


def changed_code_files():
    loaded_at = getattr(agent, "LOADED_AT", None)
    if loaded_at is None:
        return ["agent.py"]
    return [os.path.basename(m.__file__) for m in (agent, tools, llm)
            if os.path.getmtime(m.__file__) > loaded_at]


@st.cache_data
def graph_structure():
    services = tools.graph.ro_query(
        "MATCH (s:Service) RETURN s.name, s.tier ORDER BY s.name").result_set
    edges = tools.graph.ro_query(
        "MATCH (a:Service)-[:DEPENDS_ON]->(b:Service) RETURN a.name, b.name").result_set
    return [tuple(s) for s in services], [tuple(e) for e in edges]


def clock(minutes):
    h, m = divmod(minutes, 60)
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def trace_result(result, tool, service=None):
    for step in reversed(result["trace"]):
        if step["tool"] != tool or not isinstance(step["result"], list):
            continue
        if service is None or step["args"].get("service") == service:
            return step["result"]
    return []


def incident_info(incident_id):
    rows = tools.graph.ro_query("""
        MATCH (i:Incident {id: $id})-[:CAUSED_BY]->(s:Service)
        RETURN i.status, s.name, i.title
    """, {"id": incident_id}).result_set
    return rows[0] if rows else None


def diagnosis(result):
    root = result["root_cause"]
    facts = agent.facts_from_trace(result["trace"])
    people = facts["people"] or (agent.find_people_to_contact(root) if root else [])
    past = trace_result(result, "find_similar_incidents", root)
    return {
        "root": root,
        "deploy": facts["deploy"],
        "path": result["evidence_path"],
        "blast": result["blast_radius"],
        "people": people,
        "fix": next((i for i in past if i.get("runbook")), None),
        "candidates": trace_result(result, "find_root_cause"),
    }


def is_check(result):
    return bool(result) and result.get("mode") == "service_check"


def asked_services(result):
    return [c["service"] for c in result.get("service_checks", [])] if is_check(result) else []


def active_incident(result):
    candidates = trace_result(result, "find_root_cause")
    root = (result.get("active_incident") or {}).get("root_cause")
    deploy = None
    if candidates and candidates[0]["service"] == root and candidates[0]["recent_deploys"]:
        deploy = candidates[0]["recent_deploys"][0]
    return root, deploy


def dependency_set(result):
    return {d for c in result.get("service_checks", []) for d in c.get("dependencies", [])}


def dot_graph(alerting, result):
    services, edges = graph_structure()
    root = result["root_cause"] if result else None
    blast = set(result["blast_radius"]) if result else set()
    path = result["evidence_path"] if result else []
    path_edges = set(zip(path, path[1:]))
    lines = ['digraph G {', 'rankdir=LR; bgcolor="transparent"; nodesep=0.08; ranksep=0.28; pad=0.05; '
             'splines=true;',
             'node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=15, '
             'height=0.36, margin="0.08,0.03", penwidth=1.2];',
             'edge [color="#2f3a4e", arrowsize=0.55, penwidth=1];']
    for name, _ in services:
        if name == root:
            attrs = 'fillcolor="#f05252", color="#fca5a5", fontcolor="white", penwidth=2'
        elif name in blast:
            attrs = 'fillcolor="#f59e0b", color="#fbbf24", fontcolor="#1a1205"'
        elif root and name in alerting:
            attrs = 'fillcolor="#161e2e", color="#6b7280", fontcolor="#9ca3af", style="rounded,filled,dashed"'
        elif name in alerting:
            attrs = 'fillcolor="#3b2a0a", color="#f59e0b", fontcolor="#fcd34d"'
        else:
            attrs = 'fillcolor="#161e2e", color="#2a3446", fontcolor="#8b95a7"'
        lines.append(f'"{name}" [{attrs}];')
    for a, b in edges:
        attrs = ' [color="#f05252", penwidth=2.6, arrowsize=0.7]' if (a, b) in path_edges else ""
        lines.append(f'"{a}" -> "{b}"{attrs};')
    lines.append("}")
    return "\n".join(lines)


def check_neighbourhood(result):
    _, edges = graph_structure()
    asked = set(asked_services(result))
    deps = dependency_set(result) - asked
    callers = {a for a, b in edges if b in asked} - asked - deps
    return asked, deps, callers


def check_graph(alerting, result, full=False):
    services, edges = graph_structure()
    asked, deps, callers = check_neighbourhood(result)
    focus = asked | deps | callers
    lines = ['digraph G {', 'rankdir=LR; bgcolor="transparent"; nodesep=0.12; ranksep=0.4; pad=0.1; '
             'splines=true;',
             'node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=15, '
             'height=0.4, margin="0.12,0.05", penwidth=1.4];',
             'edge [color="#2f3a4e", arrowsize=0.6, penwidth=1];']
    styles = {
        "asked": {"fillcolor": "#0f1d33", "color": "#60a5fa", "fontcolor": "#bfdbfe", "penwidth": 3},
        "dep": {"fillcolor": "#10233a", "color": "#93c5fd", "fontcolor": "#dbeafe", "penwidth": 1.6},
        "caller": {"fillcolor": "#1f2633", "color": "#6b7280", "fontcolor": "#cbd5e1", "penwidth": 1.4},
        "other": {"fillcolor": "#161e2e", "color": "#232c3d", "fontcolor": "#5d6879"},
    }
    for name, _ in services:
        if not full and name not in focus:
            continue
        role = ("asked" if name in asked else "dep" if name in deps
                else "caller" if name in callers else "other")
        style = dict(styles[role])
        if name in alerting and role != "other":
            style.update(color="#f59e0b", penwidth=2.6)
        attrs = ", ".join(f'{k}="{v}"' for k, v in style.items())
        lines.append(f'"{name}" [{attrs}];')
    for a, b in edges:
        if a in focus and b in focus:
            if b in deps and a in asked | deps:
                attrs = ' [color="#93c5fd", penwidth=2, arrowsize=0.7]'
            elif b in asked:
                attrs = ' [color="#9ca3af", penwidth=1.8, arrowsize=0.7]'
            else:
                attrs = ' [color="#3a4558"]'
        elif full:
            attrs = ' [color="#1e2635"]'
        else:
            continue
        lines.append(f'"{a}" -> "{b}"{attrs};')
    lines.append("}")
    return "\n".join(lines)


def kpi(label, value, sub, tone=""):
    return (f'<div class="kpi {tone}"><div class="kpi-label">{esc(label)}</div>'
            f'<div class="kpi-value">{esc(str(value))}</div>'
            f'<div class="kpi-sub" title="{esc(sub)}">{esc(sub)}</div></div>')


def model_name(result):
    return result["model"].split("/")[-1] if result.get("model") else "no LLM call"


def run_kpi(result):
    model = model_name(result)
    run = f"{model} · {result.get('llm_calls', 0)} LLM calls"
    run += " · cached" if result.get("cached") else ""
    run += " · fallback" if result.get("fallback") else ""
    return kpi("Agent run", f"{result.get('tokens', 0):,} tokens", run)


def check_kpis(result):
    checks = result.get("service_checks", [])
    owners = " · ".join(f"{', '.join(c['engineers']) or 'no owner'} ({c['team'] or 'no team'})"
                        for c in checks)
    deps = sorted(dependency_set(result))
    alerting_deps = sorted({d["service"] for c in checks for d in c.get("alerting_dependencies", [])})
    return [kpi("Root cause", f"No alert on {', '.join(c['service'] for c in checks)}",
                f"Owner: {owners}", "blue"),
            kpi("Dependencies", f"{len(deps)} service{'s' if len(deps) != 1 else ''}",
                f"{', '.join(alerting_deps)} alerting" if alerting_deps
                else ("none alerting" if deps else "no dependencies"),
                "amber" if alerting_deps else ""),
            kpi("Incident", "—", "None opened", "none"),
            run_kpi(result)]


def kpi_strip(alerts, result, tiers):
    counts = {s: sum(a["severity"] == s for a in alerts) for s in ("critical", "high", "warning")}
    cards = [kpi("Active alerts", len(alerts),
                 f"{counts['critical']} critical · {counts['high']} high · {counts['warning']} warning",
                 "amber")]
    if not result:
        cards += [kpi("Root cause", "—", "Ask the agent to investigate", "none"),
                  kpi("Blast radius", "—", "Services that break with it", "none"),
                  kpi("Incident", "—", "None opened yet", "none"),
                  kpi("Agent run", "—", f"Models: {os.getenv('MODELS') or 'default chain'}", "none")]
        return '<div class="kpis">' + "".join(cards) + "</div>"
    if is_check(result):
        return '<div class="kpis">' + "".join(cards + check_kpis(result)) + "</div>"
    d = diagnosis(result)
    dep = d["deploy"]
    cards.append(kpi("Root cause", d["root"] or "unknown",
                     f"{dep['id']} · {dep['change']} · {dep['author']}" if dep else "No recent deploy",
                     "red"))
    tier1 = sum(tiers.get(s) == 1 for s in d["blast"])
    cards.append(kpi("Blast radius", f"{len(d['blast'])} services",
                     f"{tier1} tier-1 · if {d['root']} fails", "amber"))
    info = incident_info(result["incident_id"]) if result.get("incident_id") else None
    cards.append(kpi("Incident", result.get("incident_id") or "—",
                     f"{info[0]} · {info[1]}" if info else "Not in graph (demo reset)"
                     if result.get("incident_id") else "None opened"))
    cards.append(run_kpi(result))
    return '<div class="kpis">' + "".join(cards) + "</div>"


def alerts_panel(alerts, result):
    root = result["root_cause"] if result else None
    blast = set(result["blast_radius"]) if result else set()
    deps = dependency_set(result) if is_check(result) else set()
    rows = []
    for a in sorted(alerts, key=lambda a: a["fired_at"], reverse=True):
        sev, svc = a["severity"], a["service"]
        cls, tags = "", [f'<span class="tag tag-{sev}">{esc(SEV_SHORT.get(sev, sev))}</span>']
        if svc == root:
            cls = " is-root"
            tags.append('<span class="tag tag-root">root</span>')
        elif svc in blast:
            tags.append('<span class="tag tag-symptom">symptom</span>')
        elif svc in deps:
            tags.append('<span class="tag tag-dep">dependency</span>')
        elif root and not is_check(result):
            cls = " is-ruled"
            tags.append('<span class="tag">ruled out</span>')
        rows.append(
            f'<div class="alert{cls}"><span class="sev sev-{sev}"></span><div class="alert-body">'
            f'<div class="alert-top"><b>{esc(svc)}</b><span class="alert-meta">{"".join(tags)}</span></div>'
            f'<div class="alert-bottom"><span class="alert-msg">{esc(a["message"])}</span>'
            f'<span class="t">{clock(a["fired_at"])}</span></div></div></div>')
    return (f'<div class="panel-title"><h3>Active alerts</h3><span class="count">{len(alerts)}</span></div>'
            + "".join(rows))


def check_panel(result):
    parts = []
    root, dep = active_incident(result)
    for c in result.get("service_checks", []):
        risk = (f'<span class="chip amber">at risk: depends on {esc(root)}</span>'
                if c["at_risk"] else '<span class="chip green">not affected by the incident</span>')
        parts.append(f'<div class="sec"><div class="sec-label">Service check</div>'
                     f'<div class="asked-name">{esc(c["service"])}</div><div class="status-row">'
                     f'<span class="chip blue">no active alerts</span>{risk}</div></div>')
        alerting_deps = {d["service"] for d in c.get("alerting_dependencies", [])}
        chips = "".join(f'<span class="chip{" amber" if d in alerting_deps else ""}">{esc(d)}</span>'
                        for d in c.get("dependencies", []))
        parts.append(f'<div class="sec"><div class="sec-label">Dependencies</div>'
                     + (f'<div class="chain">{chips}</div>' if chips else "")
                     + f'<div class="note" style="margin-top:7px">{esc(agent.dependency_sentence(c))}</div></div>')
        people = "".join(
            f'<div class="person"><span class="avatar">{esc(e[:1])}</span><div>'
            f'<div class="pname">{esc(e)}</div>'
            f'<div class="preason">owns {esc(c["service"])} ({esc(c["team"] or "unknown team")})</div></div></div>'
            for e in c["engineers"]) or '<div class="note">No owner found in the graph.</div>'
        parts.append(f'<div class="sec"><div class="sec-label">Responsible for {esc(c["service"])}</div>{people}</div>')
    if root:
        deploy = (f'<div class="deploy"><code>{esc(dep["id"])}</code><span>{esc(dep["change"])} '
                  f'<span class="muted">by {esc(dep["author"])}</span></span></div>') if dep else ""
        parts.append(f'<div class="sec"><div class="sec-label">Active incident elsewhere</div>'
                     f'<div class="ruled"><b>{esc(root)}</b></div>{deploy}</div>')
    parts.append('<div class="sec"><div class="note">No incident opened: the service you asked about '
                 'is not alerting.</div></div>')
    return "".join(parts)


def diagnosis_panel(result):
    head = '<div class="panel-title"><h3>Diagnosis</h3></div>'
    if not result:
        steps = ["Reads the alerts that are firing right now",
                 "Walks DEPENDS_ON edges in FalkorDB to find the one service that explains them",
                 "Pulls the recent deploy, the owners and the past fix from the graph",
                 "Opens an incident and tells you who to wake up"]
        return (head + '<div class="empty"><div class="empty-title">Ask the agent to start an investigation</div>'
                + "".join(f'<div class="step"><i>{n}</i><span>{esc(s)}</span></div>'
                          for n, s in enumerate(steps, 1)) + "</div>")
    if is_check(result):
        return head + check_panel(result)
    d = diagnosis(result)
    parts = [head]
    dep = d["deploy"]
    deploy = (f'<div class="deploy"><code>{esc(dep["id"])}</code><span>{esc(dep["change"])} '
              f'<span class="muted">by {esc(dep["author"])}</span></span></div>') if dep else \
        '<div class="deploy muted">No recent deploy on this service</div>'
    parts.append(f'<div class="sec"><div class="sec-label">Root cause</div>'
                 f'<div class="root-name">{esc(d["root"] or "unknown")}</div>{deploy}</div>')
    if d["path"]:
        chain = '<span class="arrow">→</span>'.join(
            f'<span class="chip{" red" if s == d["root"] else ""}">{esc(s)}</span>' for s in d["path"])
        parts.append(f'<div class="sec"><div class="sec-label">Evidence path</div><div class="chain">{chain}</div></div>')
    if d["people"]:
        people = "".join(
            f'<div class="person"><span class="avatar">{esc(p["name"][:1])}</span><div>'
            f'<div class="pname">{esc(p["name"])}</div>'
            f'<div class="preason">{esc(" · ".join(short_reason(r) for r in p["reasons"]))}</div></div></div>'
            for p in d["people"][:3])
        parts.append(f'<div class="sec"><div class="sec-label">Wake up</div>{people}</div>')
    fix = d["fix"]
    if fix:
        src = f"Used in {fix['id']}" + (f" · resolved by {fix['resolved_by']}" if fix.get("resolved_by") else "")
        parts.append(f'<div class="sec"><div class="sec-label">Suggested fix</div><div class="fix">'
                     f'<div class="fix-title">{esc(fix["runbook"])}</div><div class="fix-src">{esc(src)}</div></div></div>')
    else:
        parts.append(f'<div class="sec"><div class="sec-label">Suggested fix</div>'
                     f'<div class="ruled">{esc(agent.NO_PAST_FIX)}</div></div>')
    cands = d["candidates"]
    if len(cands) > 1:
        top = cands[0]["explains_count"]
        ruled = "".join(
            f'<div class="ruled"><b>{esc(c["service"])}</b>: explains {c["explains_count"]} '
            f'alert{"s" if c["explains_count"] != 1 else ""} vs {top}'
            f'{", no recent deploy" if not c["recent_deploys"] else ""}</div>'
            for c in cands[1:])
        parts.append(f'<div class="sec"><div class="sec-label">Ruled out</div>{ruled}</div>')
    return "".join(parts)


def short_reason(reason):
    m = re.match(r"member of (.+), the team that owns (.+)", reason)
    if m:
        return f"owns {m.group(2)} ({m.group(1)})"
    m = re.match(r"authored deploy (\S+)", reason)
    if m:
        return f"made deploy {m.group(1)}"
    m = re.match(r"resolved similar incident (\S+)", reason)
    if m:
        return f"fixed {m.group(1)} last time"
    return reason


def answer_box(text):
    return f'<div class="answer">{esc(text)}</div>'


def reset_demo():
    agent.cleanup_open_incidents()
    agent._answer_cache.clear()
    for key in ("result", "rag", "question_asked", "error"):
        st.session_state.pop(key, None)


head_left, head_right = st.columns([5, 1], vertical_alignment="center")
with head_left:
    render('<div class="rc-header"><div class="rc-logo"></div><div>'
           '<div class="rc-title">RootCause Agent</div>'
           '<div class="rc-sub">On-call agent that traverses a FalkorDB dependency graph '
           'to find the real cause of an outage</div></div>'
           '<div style="flex:1"></div><div class="rc-pills">'
           f'<span class="pill"><span class="dot"></span>FalkorDB · <b>{esc(tools.GRAPH_NAME)}</b></span>'
           f'<span class="pill">Demo clock <b>{clock(tools.NOW)}</b></span>'
           '</div></div>')
with head_right, st.container(key="reset"):
    if st.button("Reset demo", width="stretch", help="Delete open incidents and cached answers"):
        reset_demo()
        st.toast("Demo reset: open incidents and cached answers cleared.")

with st.container(key="askbar"):
    with st.form("ask", border=False):
        q_col, t_col, b_col = st.columns([7, 1.9, 1.2], vertical_alignment="center")
        with q_col:
            question = st.text_input("Question", value=DEFAULT_QUESTION,
                                     label_visibility="collapsed",
                                     placeholder="Ask about the outage...")
        with t_col:
            compare = st.toggle("Compare with vector RAG")
        with b_col:
            asked = st.form_submit_button("Investigate", type="primary", width="stretch")

if asked and question.strip():
    st.session_state.pop("rag", None)
    st.session_state.pop("error", None)
    with st.spinner("Walking the dependency graph..."):
        try:
            st.session_state["result"] = agent.ask(question)
            st.session_state["question_asked"] = question
        except Exception as e:
            st.session_state.pop("result", None)
            st.session_state["error"] = f"Agent failed: {e}"
    if compare and "result" in st.session_state:
        with st.spinner("Asking the vector RAG baseline..."):
            try:
                from baseline import baseline_answer
                st.session_state["rag"] = baseline_answer(question)
            except Exception as e:
                st.session_state["error"] = f"Baseline failed: {e}"

changed = changed_code_files()
if changed:
    st.warning(f"{', '.join(changed)} changed on disk after this app loaded it, so answers still come "
               "from the old code. Restart Streamlit (Ctrl+C, then `streamlit run app.py`).",
               icon=":material/warning:")

if "error" in st.session_state:
    st.error(st.session_state["error"])

if st.session_state.get("result", {}).get("cached"):
    answered = st.session_state["result"].get("answered_at")
    when = time.strftime("%H:%M:%S", time.localtime(answered)) if answered else "earlier"
    st.caption(f"Showing a cached answer from {when}. Reset demo to clear the cache.")

result = st.session_state.get("result")
alerts = tools.get_active_alerts()
alerting = {a["service"] for a in alerts}
services, _ = graph_structure()
tiers = dict(services)

render(kpi_strip(alerts, result, tiers))

col_alerts, col_graph, col_diag = st.columns([1.05, 2.35, 1.2], gap="medium")
with col_alerts:
    with st.container(key="alerts"):
        render(alerts_panel(alerts, result))
with col_graph:
    with st.container(key="graph"):
        if is_check(result):
            legend = ('<div class="legend"><span><i class="sw sw-asked"></i>asked about</span>'
                      '<span><i class="sw sw-dep"></i>depends on</span>'
                      '<span><i class="sw sw-caller"></i>called by</span>'
                      '<span><i class="sw sw-alert-outline"></i>alerting</span></div>')
        else:
            legend = ('<div class="legend"><span><i class="sw sw-root"></i>root cause</span>'
                      '<span><i class="sw sw-path"></i>evidence path</span>'
                      '<span><i class="sw sw-blast"></i>blast radius</span>'
                      '<span><i class="sw sw-herring"></i>ruled out</span>'
                      '<span><i class="sw sw-alert"></i>alerting</span></div>')
        title = "Service neighbourhood" if is_check(result) else "Dependency graph"
        render(f'<div class="panel-title"><h3>{title}</h3>{legend}</div>')
        if is_check(result):
            full = st.toggle("Show full graph", value=False, key="full_graph")
            st.graphviz_chart(check_graph(alerting, result, full),
                              width="stretch" if full else "content")
        else:
            st.graphviz_chart(dot_graph(alerting, result), width="stretch")
with col_diag:
    with st.container(key="diag"):
        render(diagnosis_panel(result))

if result:
    n_tools = len(result["trace"])
    rag = st.session_state.get("rag")
    tab_answer, tab_tools, tab_rag = st.tabs(
        ["Agent answer", f"Tools called ({n_tools})", "Graph agent vs vector RAG"])
    with tab_answer:
        model = model_name(result)
        badges = [f'<span class="badge blue">{esc(model)}</span>',
                  f'<span class="badge">{result.get("tokens", 0):,} tokens</span>',
                  f'<span class="badge">{result.get("llm_calls", 0)} LLM calls</span>',
                  '<span class="badge green">cached</span>' if result.get("cached")
                  else '<span class="badge">fresh</span>']
        if result.get("fallback"):
            badges.append('<span class="badge amber">fallback answer</span>')
        render(f'<div class="meta">{"".join(badges)}</div>'
               + answer_box(f'Q: {st.session_state.get("question_asked", "")}\n\n{result["answer"]}'))
    with tab_tools:
        items = "".join(
            f'<li><span><code>{esc(s["tool"])}</code> <span class="args">{esc(str(s["args"]))}</span>'
            + (' <span class="badge amber">called by code</span>' if s.get("called_by") == "code" else "")
            + "</span></li>" for s in result["trace"])
        render(f'<ol class="trace">{items}</ol>')
    with tab_rag:
        if rag:
            g_col, r_col = st.columns(2, gap="medium")
            with g_col:
                render('<div class="col-head">Graph agent (FalkorDB)</div>' + answer_box(result["answer"]))
            with r_col:
                docs = "".join(f"<li>{esc(doc)}</li>" for doc in rag["retrieved"])
                render('<div class="col-head">Vector RAG (TF-IDF top-k)</div>' + answer_box(rag["answer"])
                       + f'<div class="col-head" style="margin-top:12px">Retrieved documents</div>'
                       f'<ul class="docs">{docs}</ul>')
        else:
            st.caption("Turn on **Compare with vector RAG** and ask again to see both answers side by side.")
