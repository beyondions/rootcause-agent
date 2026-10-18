# Cypher queries

Every Cypher query the product runs, grouped by file. All of them are parameterised
(`$service`, `$id`, …), so no user input is ever concatenated into Cypher. Read-only queries
go through `ro_query`; only the incident, reset and loader queries write.

## Agent tools ([`tools.py`](tools.py))

These are the queries behind the tools the LLM can call.

**`get_active_alerts`**: which alerts are firing right now, and on which service?

```cypher
MATCH (a:Alert {status: 'firing'})-[:FIRED_ON]->(s:Service)
RETURN a.id, s.name, a.message, a.severity, a.fired_at
ORDER BY a.fired_at DESC
```

**`_downstream`** (used by `find_root_cause` and service checks): what does this service
depend on, at any depth?

```cypher
MATCH (:Service {name: $s})-[:DEPENDS_ON*1..]->(x:Service)
RETURN DISTINCT x.name
```

**`_upstream`** (used by `find_root_cause`): what depends on this service, at any depth?

```cypher
MATCH (:Service {name: $s})<-[:DEPENDS_ON*1..]-(x:Service)
RETURN DISTINCT x.name
```

`find_root_cause` runs both traversals for each alerting service and combines them in
Python. A service whose downstream set contains another alerting service is a victim and is
skipped. The rest are ranked by `|upstream ∩ alerting|`, then by number of recent deploys.

**`get_recent_deploys`**: what changed on this service in the last 30 minutes, and who did it?

```cypher
MATCH (e:Engineer)-[:AUTHORED]->(d:Deploy)-[:DEPLOYED_TO]->(:Service {name: $service})
WHERE d.time >= $since
RETURN d.id, d.change, d.time, e.name
ORDER BY d.time DESC
```

**`find_blast_radius`**: what breaks if this service fails, most critical tier first?

```cypher
MATCH (:Service {name: $service})<-[:DEPENDS_ON*1..]-(x:Service)
RETURN DISTINCT x.name, x.tier
ORDER BY x.tier, x.name
```

**`find_owner`** (used by `find_people_to_contact` and service checks): which team owns this
service, and who is on it?

```cypher
MATCH (e:Engineer)-[:MEMBER_OF]->(t:Team)-[:OWNS]->(:Service {name: $service})
RETURN t.name, collect(e.name)
```

**`find_similar_incidents`**: what went wrong with this service before, who fixed it, and
with which runbook?

```cypher
MATCH (i:Incident)-[:CAUSED_BY]->(:Service {name: $service})
OPTIONAL MATCH (e:Engineer)-[:RESOLVED]->(i)
OPTIONAL MATCH (i)-[:FIXED_WITH]->(r:Runbook)
RETURN i.id, i.title, e.name, r.title
```

**`get_evidence_path`**: what is the shortest dependency chain from the symptom to the cause?
FalkorDB only allows `shortestPath` inside `RETURN`/`WITH`, which is why it is written this way.

```cypher
MATCH (a:Service {name: $a}), (b:Service {name: $b})
RETURN [n IN nodes(shortestPath((a)-[:DEPENDS_ON*]->(b))) | n.name]
```

`find_people_to_contact` (in `agent.py`) has no query of its own. It combines `find_owner`,
`get_recent_deploys` and `find_similar_incidents`.

**`create_incident`**: the action. Three queries in order.

Does the service exist?

```cypher
MATCH (s:Service {name: $svc}) RETURN s.name
```

Is there already an open incident for it? If so, it is reused.

```cypher
MATCH (i:Incident {status: 'open'})-[:CAUSED_BY]->(:Service {name: $svc})
RETURN i.id ORDER BY i.id LIMIT 1
```

Otherwise, open the next `INC-2xx` and link it to the root cause:

```cypher
MATCH (s:Service {name: $svc})
OPTIONAL MATCH (x:Incident)
WITH s, max(toInteger(substring(x.id, 4))) AS top
WITH s, 'INC-' + toString(CASE WHEN top IS NULL OR top < 200 THEN 201 ELSE top + 1 END) AS new_id
CREATE (i:Incident {id: new_id, name: new_id,
                    title: $title, summary: $summary,
                    status: 'open', opened_at: $now})-[:CAUSED_BY]->(s)
RETURN i.id
```

**`check_graph`**: is the graph loaded? Runs at startup.

```cypher
MATCH (s:Service) RETURN count(s)
```

## Agent internals ([`agent.py`](agent.py))

**`service_names`**: which service names exist, so they can be matched in the question?
Run once, then kept in memory.

```cypher
MATCH (s:Service) RETURN s.name
```

**`cleanup_open_incidents`**: delete the incidents the agent opened. Used by "Reset demo",
the CLI `reset` command, the tests and `compare.py`.

```cypher
MATCH (i:Incident {status: 'open'}) DETACH DELETE i
```

**`open_incident_count`**: how many incidents are open? Used by the tests to check that a
service check opened none.

```cypher
MATCH (i:Incident {status: 'open'}) RETURN count(i)
```

## Web app ([`app.py`](app.py))

**`graph_structure`**: all services and dependency edges, for drawing the graph. Run once,
then cached.

```cypher
MATCH (s:Service) RETURN s.name, s.tier ORDER BY s.name
```

```cypher
MATCH (a:Service)-[:DEPENDS_ON]->(b:Service) RETURN a.name, b.name
```

**`incident_info`**: what is the status of the incident the agent opened, and which service
caused it?

```cypher
MATCH (i:Incident {id: $id})-[:CAUSED_BY]->(s:Service)
RETURN i.status, s.name, i.title
```

## Data loader ([`load_data.py`](load_data.py))

Every write uses `MERGE`, so running the loader twice gives the same graph.
`--reset` deletes the whole graph first, with `graph.delete()` (a client call, not Cypher).

Services, with their tier:

```cypher
MERGE (s:Service {name: $name}) SET s.tier = $tier
```

Dependencies:

```cypher
MATCH (a:Service {name: $caller}), (b:Service {name: $callee})
MERGE (a)-[:DEPENDS_ON]->(b)
```

Teams, the services they own, and the engineers on them:

```cypher
MERGE (:Team {name: $team})
```

```cypher
MATCH (t:Team {name: $team}), (s:Service {name: $svc})
MERGE (t)-[:OWNS]->(s)
```

```cypher
MERGE (e:Engineer {name: $name})
WITH e
MATCH (t:Team {name: $team})
MERGE (e)-[:MEMBER_OF]->(t)
```

Deploys, the service each one went to, and who authored it:

```cypher
MERGE (d:Deploy {id: $id})
SET d.name = $id, d.change = $change, d.time = $time
WITH d
MATCH (s:Service {name: $svc}), (e:Engineer {name: $author})
MERGE (d)-[:DEPLOYED_TO]->(s)
MERGE (e)-[:AUTHORED]->(d)
```

Firing alerts:

```cypher
MERGE (a:Alert {id: $id})
SET a.name = $id, a.message = $msg, a.severity = $sev,
    a.fired_at = $fired_at, a.status = 'firing'
WITH a
MATCH (s:Service {name: $svc})
MERGE (a)-[:FIRED_ON]->(s)
```

Past incidents, who resolved them and with which runbook:

```cypher
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
```

Summary printed after loading: how many nodes per label, and relationships per type?

```cypher
MATCH (n) RETURN labels(n)[0] AS label, count(n) AS total
ORDER BY label
```

```cypher
MATCH ()-[r]->() RETURN type(r) AS rel, count(r) AS total
ORDER BY rel
```
