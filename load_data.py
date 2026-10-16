import os
import sys

from dotenv import load_dotenv
from falkordb import FalkorDB

load_dotenv()

GRAPH_NAME = os.getenv("FALKORDB_GRAPH", "incidents")

client = FalkorDB(host=os.getenv("FALKORDB_HOST", "localhost"),
                  port=int(os.getenv("FALKORDB_PORT", "6379")),
                  username=os.getenv("FALKORDB_USERNAME") or None,
                  password=os.getenv("FALKORDB_PASSWORD") or None)
graph = client.select_graph(GRAPH_NAME)


def run(query, params=None):
    return graph.query(query, params or {})


SERVICES = [
    ("frontend", 1), ("api-gateway", 1), ("auth", 1), ("user-profile", 2),
    ("checkout", 1), ("cart", 1), ("payment", 1), ("fraud-detection", 2),
    ("product-catalog", 1), ("search", 2), ("inventory", 1),
    ("recommendation", 3), ("review", 3), ("ad", 3), ("currency", 2),
    ("shipping", 2), ("quote", 2), ("email", 3), ("notification", 3),
    ("loyalty", 3), ("accounting", 3),
    ("cache", 1), ("postgres", 1), ("kafka", 2), ("elasticsearch", 2),
]

DEPENDS_ON = [
    ("frontend", "api-gateway"),

    ("api-gateway", "auth"), ("api-gateway", "checkout"),
    ("api-gateway", "cart"), ("api-gateway", "product-catalog"),
    ("api-gateway", "search"), ("api-gateway", "recommendation"),
    ("api-gateway", "review"), ("api-gateway", "ad"),
    ("api-gateway", "currency"), ("api-gateway", "user-profile"),

    ("auth", "user-profile"), ("auth", "postgres"),
    ("user-profile", "postgres"),

    ("checkout", "payment"), ("checkout", "cart"), ("checkout", "shipping"),
    ("checkout", "currency"), ("checkout", "inventory"),
    ("checkout", "kafka"), ("checkout", "loyalty"),

    ("cart", "cache"), ("cart", "product-catalog"),
    ("payment", "cache"), ("payment", "fraud-detection"),
    ("fraud-detection", "postgres"),

    ("product-catalog", "postgres"), ("product-catalog", "inventory"),
    ("inventory", "postgres"),
    ("search", "elasticsearch"), ("search", "product-catalog"),
    ("recommendation", "product-catalog"),
    ("review", "postgres"),

    ("shipping", "quote"),
    ("notification", "email"), ("notification", "kafka"),
    ("loyalty", "postgres"),
    ("accounting", "kafka"), ("accounting", "postgres"),
]

TEAMS = [
    ("Platform",   ["cache", "postgres", "kafka", "elasticsearch", "api-gateway"]),
    ("Identity",   ["auth", "user-profile"]),
    ("Checkout",   ["checkout", "payment", "cart", "fraud-detection",
                    "accounting", "loyalty"]),
    ("Storefront", ["frontend", "product-catalog", "search", "recommendation",
                    "review", "ad", "currency"]),
    ("Logistics",  ["shipping", "quote", "inventory"]),
    ("Comms",      ["email", "notification"]),
]

ENGINEERS = [
    ("Priya", "Platform"), ("Arjun", "Platform"),
    ("Nikhil", "Identity"),
    ("Meera", "Checkout"), ("Karan", "Checkout"),
    ("Rohan", "Storefront"), ("Sana", "Storefront"),
    ("Dev", "Logistics"),
    ("Isha", "Comms"),
]

DEPLOYS = [
    ("D-1", "cache", "Config change: maxmemory lowered", 110, "Arjun"),
    ("D-5", "search", "Index mapping update", 95, "Rohan"),
    ("D-2", "recommendation", "Model refresh", 60, "Sana"),
    ("D-3", "frontend", "Banner text update", 30, "Rohan"),
    ("D-4", "shipping", "Quote timeout tweak", 5, "Dev"),
]

ALERTS = [
    ("A-1", "frontend", "Orders failing", "critical", 121),
    ("A-2", "api-gateway", "5xx error rate above 10%", "critical", 120),
    ("A-3", "checkout", "Checkout errors", "critical", 119),
    ("A-4", "payment", "Payment slow", "high", 118),
    ("A-5", "cart", "Cart slow", "high", 117),
    ("A-6", "cache", "Cache latency high", "warning", 115),
    ("A-7", "recommendation", "High CPU", "warning", 100),
]

PAST_INCIDENTS = [
    ("INC-101", "Cache eviction storm after config change", "cache",
     "Priya", "Roll back cache config"),
    ("INC-097", "Payment provider timeouts", "payment",
     "Meera", "Fail over payment provider"),
    ("INC-088", "Search outage after Elasticsearch node loss", "elasticsearch",
     "Arjun", "Restart Elasticsearch cluster"),
]

def validate():
    names = {name for name, _ in SERVICES}
    errors = []

    for a, b in DEPENDS_ON:
        for svc in (a, b):
            if svc not in names:
                errors.append(f"DEPENDS_ON uses unknown service: {svc}")

    owned = [svc for _, svcs in TEAMS for svc in svcs]
    for svc in owned:
        if svc not in names:
            errors.append(f"TEAMS uses unknown service: {svc}")
    for svc in names:
        if owned.count(svc) != 1:
            errors.append(f"{svc} has {owned.count(svc)} owners (expected 1)")

    teams = {t for t, _ in TEAMS}
    people = {e for e, _ in ENGINEERS}
    for e, t in ENGINEERS:
        if t not in teams:
            errors.append(f"Engineer {e} is in unknown team {t}")
    for _, svc, _, _, author in DEPLOYS:
        if svc not in names:
            errors.append(f"Deploy targets unknown service: {svc}")
        if author not in people:
            errors.append(f"Deploy author unknown: {author}")
    for _, svc, _, _, _ in ALERTS:
        if svc not in names:
            errors.append(f"Alert on unknown service: {svc}")
    for _, _, svc, resolver, _ in PAST_INCIDENTS:
        if svc not in names:
            errors.append(f"Incident on unknown service: {svc}")
        if resolver not in people:
            errors.append(f"Incident resolver unknown: {resolver}")

    if errors:
        print("Data errors found, nothing was loaded:")
        for err in errors:
            print("  -", err)
        sys.exit(1)


def load_services():
    for name, tier in SERVICES:
        run("MERGE (s:Service {name: $name}) SET s.tier = $tier",
            {"name": name, "tier": tier})


def load_dependencies():
    for caller, callee in DEPENDS_ON:
        run("""
            MATCH (a:Service {name: $caller}), (b:Service {name: $callee})
            MERGE (a)-[:DEPENDS_ON]->(b)
        """, {"caller": caller, "callee": callee})


def load_teams_and_people():
    for team, services in TEAMS:
        run("MERGE (:Team {name: $team})", {"team": team})
        for svc in services:
            run("""
                MATCH (t:Team {name: $team}), (s:Service {name: $svc})
                MERGE (t)-[:OWNS]->(s)
            """, {"team": team, "svc": svc})
    for name, team in ENGINEERS:
        run("""
            MERGE (e:Engineer {name: $name})
            WITH e
            MATCH (t:Team {name: $team})
            MERGE (e)-[:MEMBER_OF]->(t)
        """, {"name": name, "team": team})


def load_deploys():
    for dep_id, svc, change, time, author in DEPLOYS:
        run("""
            MERGE (d:Deploy {id: $id})
            SET d.name = $id, d.change = $change, d.time = $time
            WITH d
            MATCH (s:Service {name: $svc}), (e:Engineer {name: $author})
            MERGE (d)-[:DEPLOYED_TO]->(s)
            MERGE (e)-[:AUTHORED]->(d)
        """, {"id": dep_id, "svc": svc, "change": change,
              "time": time, "author": author})


def load_alerts():
    for alert_id, svc, msg, sev, fired_at in ALERTS:
        run("""
            MERGE (a:Alert {id: $id})
            SET a.name = $id, a.message = $msg, a.severity = $sev,
                a.fired_at = $fired_at, a.status = 'firing'
            WITH a
            MATCH (s:Service {name: $svc})
            MERGE (a)-[:FIRED_ON]->(s)
        """, {"id": alert_id, "svc": svc, "msg": msg,
              "sev": sev, "fired_at": fired_at})


def load_past_incidents():
    for inc_id, title, svc, resolver, runbook in PAST_INCIDENTS:
        run("""
            MERGE (i:Incident {id: $id})
            SET i.name = $id, i.title = $title, i.status = 'resolved'
            MERGE (r:Runbook {title: $runbook})
            SET r.name = $runbook
            WITH i, r
            MATCH (s:Service {name: $svc}), (e:Engineer {name: $resolver})
            MERGE (i)-[:CAUSED_BY]->(s)
            MERGE (e)-[:RESOLVED]->(i)
            MERGE (r)-[:FOR]->(s)
            MERGE (i)-[:FIXED_WITH]->(r)
        """, {"id": inc_id, "title": title, "svc": svc,
              "resolver": resolver, "runbook": runbook})


def summary():
    nodes = graph.ro_query("""
        MATCH (n) RETURN labels(n)[0] AS label, count(n) AS total
        ORDER BY label
    """).result_set
    edges = graph.ro_query("""
        MATCH ()-[r]->() RETURN type(r) AS rel, count(r) AS total
        ORDER BY rel
    """).result_set
    print("Nodes:")
    for label, total in nodes:
        print(f"  {label:<12} {total}")
    print("Relationships:")
    for rel, total in edges:
        print(f"  {rel:<12} {total}")


if __name__ == "__main__":
    validate()

    if "--reset" in sys.argv:
        try:
            graph.delete()
            print(f"Deleted graph '{GRAPH_NAME}'")
        except Exception:
            pass
        graph = client.select_graph(GRAPH_NAME)

    load_services()
    load_dependencies()
    load_teams_and_people()
    load_deploys()
    load_alerts()
    load_past_incidents()
    summary()