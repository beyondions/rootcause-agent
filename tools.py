import os

from dotenv import load_dotenv
from falkordb import FalkorDB

load_dotenv()

GRAPH_NAME = os.getenv("FALKORDB_GRAPH", "incidents")
NOW = 121

client = FalkorDB(host=os.getenv("FALKORDB_HOST", "localhost"),
                  port=int(os.getenv("FALKORDB_PORT", "6379")),
                  username=os.getenv("FALKORDB_USERNAME") or None,
                  password=os.getenv("FALKORDB_PASSWORD") or None)
graph = client.select_graph(GRAPH_NAME)


def q(query, params, cols):
    rows = graph.ro_query(query, params).result_set
    return [dict(zip(cols, row)) for row in rows]

def get_active_alerts():
    return q("""
        MATCH (a:Alert {status: 'firing'})-[:FIRED_ON]->(s:Service)
        RETURN a.id, s.name, a.message, a.severity, a.fired_at
        ORDER BY a.fired_at DESC
    """, {}, ["id", "service", "message", "severity", "fired_at"])

def _downstream(service):
    rows = graph.ro_query("""
        MATCH (:Service {name: $s})-[:DEPENDS_ON*1..]->(x:Service)
        RETURN DISTINCT x.name
    """, {"s": service}).result_set
    return {r[0] for r in rows}


def _upstream(service):
    rows = graph.ro_query("""
        MATCH (:Service {name: $s})<-[:DEPENDS_ON*1..]-(x:Service)
        RETURN DISTINCT x.name
    """, {"s": service}).result_set
    return {r[0] for r in rows}


def find_root_cause():
    alerting = {a["service"] for a in get_active_alerts()}
    candidates = []
    for svc in alerting:
        if _downstream(svc) & alerting:
            continue
        explains = sorted(_upstream(svc) & alerting)
        candidates.append({
            "service": svc,
            "explains": explains,
            "explains_count": len(explains),
            "recent_deploys": get_recent_deploys(svc),
        })
    candidates.sort(
        key=lambda c: (-c["explains_count"], -len(c["recent_deploys"]), c["service"]),
    )
    return candidates

def get_recent_deploys(service, window=30):
    return q("""
        MATCH (e:Engineer)-[:AUTHORED]->(d:Deploy)-[:DEPLOYED_TO]->(:Service {name: $service})
        WHERE d.time >= $since
        RETURN d.id, d.change, d.time, e.name
        ORDER BY d.time DESC
    """, {"service": service, "since": NOW - window},
       ["id", "change", "time", "author"])

def find_blast_radius(service):
    return q("""
        MATCH (:Service {name: $service})<-[:DEPENDS_ON*1..]-(x:Service)
        RETURN DISTINCT x.name, x.tier
        ORDER BY x.tier, x.name
    """, {"service": service}, ["service", "tier"])

def find_owner(service):
    return q("""
        MATCH (e:Engineer)-[:MEMBER_OF]->(t:Team)-[:OWNS]->(:Service {name: $service})
        RETURN t.name, collect(e.name)
    """, {"service": service}, ["team", "engineers"])

def find_similar_incidents(service):
    return q("""
        MATCH (i:Incident)-[:CAUSED_BY]->(:Service {name: $service})
        OPTIONAL MATCH (e:Engineer)-[:RESOLVED]->(i)
        OPTIONAL MATCH (i)-[:FIXED_WITH]->(r:Runbook)
        RETURN i.id, i.title, e.name, r.title
    """, {"service": service}, ["id", "title", "resolved_by", "runbook"])

def get_evidence_path(from_service, to_service):
    rows = q("""
        MATCH (a:Service {name: $a}), (b:Service {name: $b})
        RETURN [n IN nodes(shortestPath((a)-[:DEPENDS_ON*]->(b))) | n.name]
    """, {"a": from_service, "b": to_service}, ["path"])
    return rows[0]["path"] if rows else []

def create_incident(root_cause, title, summary):
    result = graph.query("""
        MATCH (s:Service {name: $svc})
        OPTIONAL MATCH (x:Incident) WITH s, count(x) AS n
        CREATE (i:Incident {id: 'INC-' + toString(200 + n), name: 'INC-' + toString(200 + n),
                            title: $title, summary: $summary,
                            status: 'open', opened_at: $now})-[:CAUSED_BY]->(s)
        RETURN i.id
    """, {"svc": root_cause, "title": title, "summary": summary, "now": NOW})
    return result.result_set[0][0]


if __name__ == "__main__":
    from pprint import pprint
    print("1. Active alerts");          pprint(get_active_alerts())
    print("\n2. Root cause candidates"); pprint(find_root_cause())
    print("\n3. Recent deploys: cache"); pprint(get_recent_deploys("cache"))
    print("\n4. Blast radius: cache");   pprint(find_blast_radius("cache"))
    print("\n5. Owner: cache");          pprint(find_owner("cache"))
    print("\n6. Past incidents: cache"); pprint(find_similar_incidents("cache"))
    print("\n7. Evidence path");         pprint(get_evidence_path("frontend", "cache"))