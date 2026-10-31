"""Cold-chain alerting demo API and static frontend server."""
from __future__ import annotations

import json
import math
import os
import sqlite3
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).parent
DATA_PATH = ROOT / "starter_kit" / "data" / "sensor_readings.json"
WEB_ROOT = ROOT / "web"
DB_PATH = Path(os.environ.get("COLDCHAIN_DB", ROOT / "data" / "coldchain.sqlite3"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

DEFAULT_POLICY = {
    "min_temp_c": 2.0,
    "max_temp_c": 8.0,
    "high_tolerance_min": 15,
    "low_tolerance_min": 10,
    "max_gap_min": 7,
    "recovery_readings": 2,
    "recovery_min_duration_min": 0,
    "recovery_min_temp_c": 2.0,
    "recovery_max_temp_c": 8.0,
    "allowed_lateness_min": 10,
    "extreme_high_c": 12.0,
    "extreme_low_c": -5.0,
    "min_humidity_pct": 30.0,
    "max_humidity_pct": 80.0,
    "high_humidity_tolerance_min": 10,
    "low_humidity_tolerance_min": 10,
    "extreme_high_humidity_pct": 95.0,
    "extreme_low_humidity_pct": 15.0,
}

SAMPLE_POLICIES = {
    "SAMPLE-PHARMA": {**DEFAULT_POLICY, "product_name": "Sample Pharma", "min_temp_c": 2.0, "max_temp_c": 8.0,
                       "high_tolerance_min": 15, "low_tolerance_min": 10, "extreme_high_c": 12.0,
                       "extreme_low_c": -5.0, "recovery_min_temp_c": 2.0, "recovery_max_temp_c": 8.0},
    "SAMPLE-GROCERY": {**DEFAULT_POLICY, "product_name": "Sample Grocery", "min_temp_c": 0.0, "max_temp_c": 10.0,
                        "high_tolerance_min": 20, "low_tolerance_min": 15, "extreme_high_c": 15.0,
                        "extreme_low_c": -8.0, "recovery_min_temp_c": 0.0, "recovery_max_temp_c": 10.0},
}

SAMPLE_ASSIGNMENTS = {f"T-{i:02d}": {"shipment_id": f"SHIP-T-{i:02d}", "product_id": "SAMPLE-PHARMA"} for i in range(1, 26)}
SAMPLE_ASSIGNMENTS["T-03"]["product_id"] = "SAMPLE-GROCERY"


def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS incidents (incident_id TEXT PRIMARY KEY, truck_id TEXT, direction TEXT, started_at TEXT, triggered_at TEXT, ended_at TEXT, peak_temp REAL, duration_min REAL, severity TEXT, status TEXT, evidence TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS policies (product_id TEXT PRIMARY KEY, policy_json TEXT NOT NULL, version INTEGER NOT NULL, effective_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS policy_history (id INTEGER PRIMARY KEY AUTOINCREMENT, product_id TEXT NOT NULL, version INTEGER NOT NULL, effective_at TEXT NOT NULL, changed_at TEXT NOT NULL, policy_json TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS truck_assignments (truck_id TEXT PRIMARY KEY, shipment_id TEXT NOT NULL, product_id TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS ingested_readings (event_id TEXT PRIMARY KEY, truck_id TEXT NOT NULL, sensor_id TEXT, event_timestamp TEXT NOT NULL, received_timestamp TEXT NOT NULL, temperature_c REAL NOT NULL, humidity_pct REAL)")
        current = conn.execute("SELECT COUNT(*) FROM policies").fetchone()[0]
        if current == 0:
            legacy = {row["key"]: json.loads(row["value"]) for row in conn.execute("SELECT key,value FROM settings")}
            for product_id, sample in SAMPLE_POLICIES.items():
                policy = sample.copy()
                if product_id == "SAMPLE-PHARMA":
                    policy.update({k: v for k, v in legacy.items() if k in policy})
                conn.execute("INSERT INTO policies(product_id,policy_json,version,effective_at) VALUES(?,?,1,?)", (product_id, json.dumps(policy), "2026-01-01T00:00:00+00:00"))
                conn.execute("INSERT INTO policy_history(product_id,version,effective_at,changed_at,policy_json) VALUES(?,?,?,?,?)", (product_id, 1, "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", json.dumps(policy)))
        for truck, assignment in SAMPLE_ASSIGNMENTS.items():
            conn.execute("INSERT OR IGNORE INTO truck_assignments(truck_id,shipment_id,product_id) VALUES(?,?,?)", (truck, assignment["shipment_id"], assignment["product_id"]))


def load_policies():
    with db_connect() as conn:
        return {row["product_id"]: {**DEFAULT_POLICY, **json.loads(row["policy_json"]), "product_id": row["product_id"], "version": row["version"], "effective_at": row["effective_at"]}
                for row in conn.execute("SELECT * FROM policies ORDER BY product_id")}


def load_assignments():
    with db_connect() as conn:
        rows = conn.execute("SELECT truck_id,shipment_id,product_id FROM truck_assignments")
        result = {row["truck_id"]: {"shipment_id": row["shipment_id"], "product_id": row["product_id"]} for row in rows}
    return {**SAMPLE_ASSIGNMENTS, **result}


def save_product_policy(payload):
    product_id = str(payload.get("product_id", "")).strip().upper()
    if not product_id:
        raise ValueError("Product ID is required.")
    policy = DEFAULT_POLICY.copy()
    policy.update({k: v for k, v in payload.items() if k in policy or k == "product_name"})
    for key in ("min_temp_c", "max_temp_c", "extreme_high_c", "extreme_low_c", "recovery_min_temp_c", "recovery_max_temp_c",
                "min_humidity_pct", "max_humidity_pct", "extreme_high_humidity_pct", "extreme_low_humidity_pct"):
        policy[key] = float(policy.get(key, policy.get("min_temp_c" if key == "recovery_min_temp_c" else "max_temp_c")))
    for key in ("high_tolerance_min", "low_tolerance_min", "max_gap_min", "recovery_readings", "recovery_min_duration_min", "allowed_lateness_min",
                "high_humidity_tolerance_min", "low_humidity_tolerance_min"):
        policy[key] = int(policy.get(key, DEFAULT_POLICY.get(key, 0)))
    if policy["min_temp_c"] >= policy["max_temp_c"]:
        raise ValueError("Minimum temperature must be lower than maximum temperature.")
    if policy["recovery_min_temp_c"] > policy["recovery_max_temp_c"]:
        raise ValueError("Recovery minimum must be lower than or equal to its maximum.")
    if policy["extreme_low_c"] >= policy["min_temp_c"] or policy["extreme_high_c"] <= policy["max_temp_c"]:
        raise ValueError("Extreme limits must sit outside the normal temperature range.")
    if not 0 <= policy["min_humidity_pct"] < policy["max_humidity_pct"] <= 100:
        raise ValueError("Humidity limits must satisfy 0 <= minimum < maximum <= 100%.")
    if policy["extreme_low_humidity_pct"] >= policy["min_humidity_pct"] or policy["extreme_high_humidity_pct"] <= policy["max_humidity_pct"] or not 0 <= policy["extreme_low_humidity_pct"] < policy["extreme_high_humidity_pct"] <= 100:
        raise ValueError("Extreme humidity limits must sit outside the humidity range and within 0-100%.")
    if min(policy["high_tolerance_min"], policy["low_tolerance_min"], policy["high_humidity_tolerance_min"], policy["low_humidity_tolerance_min"], policy["recovery_min_duration_min"], policy["allowed_lateness_min"]) < 0:
        raise ValueError("Tolerance, recovery duration, and lateness values cannot be negative.")
    if min(policy["max_gap_min"], policy["recovery_readings"]) < 1:
        raise ValueError("Maximum gap must be positive and recovery requires at least one reading.")
    now = datetime.now().astimezone().isoformat()
    effective_at = str(payload.get("effective_at") or now)
    with db_connect() as conn:
        row = conn.execute("SELECT version FROM policies WHERE product_id=?", (product_id,)).fetchone()
        version = (row["version"] + 1) if row else 1
        conn.execute("INSERT INTO policies(product_id,policy_json,version,effective_at) VALUES(?,?,?,?) ON CONFLICT(product_id) DO UPDATE SET policy_json=excluded.policy_json,version=excluded.version,effective_at=excluded.effective_at", (product_id, json.dumps(policy), version, effective_at))
        conn.execute("INSERT INTO policy_history(product_id,version,effective_at,changed_at,policy_json) VALUES(?,?,?,?,?)", (product_id, version, effective_at, now, json.dumps(policy)))
    return {**policy, "product_id": product_id, "version": version, "effective_at": effective_at}


def save_assignment(payload):
    truck = str(payload.get("truck_id", "")).strip().upper()
    shipment = str(payload.get("shipment_id", "")).strip().upper()
    product = str(payload.get("product_id", "")).strip().upper()
    if not truck or not shipment or product not in load_policies():
        raise ValueError("Choose a truck, shipment ID, and existing product policy.")
    with db_connect() as conn:
        conn.execute("INSERT INTO truck_assignments(truck_id,shipment_id,product_id) VALUES(?,?,?) ON CONFLICT(truck_id) DO UPDATE SET shipment_id=excluded.shipment_id,product_id=excluded.product_id", (truck, shipment, product))
    return {"truck_id": truck, "shipment_id": shipment, "product_id": product}


def get_policy():
    result = DEFAULT_POLICY.copy()
    with db_connect() as conn:
        for row in conn.execute("SELECT key, value FROM settings"):
            try:
                result[row["key"]] = json.loads(row["value"])
            except (ValueError, TypeError):
                pass
    return result


def save_policy(payload):
    policy = DEFAULT_POLICY.copy()
    policy.update(payload)
    policy["min_temp_c"] = float(policy["min_temp_c"])
    policy["max_temp_c"] = float(policy["max_temp_c"])
    policy["extreme_high_c"] = float(policy["extreme_high_c"])
    policy["extreme_low_c"] = float(policy["extreme_low_c"])
    for key in ("high_tolerance_min", "low_tolerance_min", "max_gap_min", "recovery_readings"):
        policy[key] = int(policy[key])
    if policy["min_temp_c"] >= policy["max_temp_c"]:
        raise ValueError("Minimum temperature must be lower than maximum temperature.")
    if policy["extreme_low_c"] >= policy["min_temp_c"] or policy["extreme_high_c"] <= policy["max_temp_c"]:
        raise ValueError("Extreme limits must sit outside the normal temperature range.")
    if min(policy["high_tolerance_min"], policy["low_tolerance_min"], policy["max_gap_min"], policy["recovery_readings"]) < 1:
        raise ValueError("Tolerance, gap, and recovery values must be at least 1.")
    with db_connect() as conn:
        for key, value in policy.items():
            conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
    return policy


def load_readings():
    sample = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    raw = [{**item, "received_timestamp": item.get("received_timestamp", item["timestamp"]), "source": "SAMPLE"}
           for item in sample["readings"]]
    with db_connect() as conn:
        raw.extend({"truck_id": row["truck_id"], "sensor_id": row["sensor_id"], "timestamp": row["event_timestamp"],
                    "received_timestamp": row["received_timestamp"], "temperature_c": row["temperature_c"],
                    "humidity_pct": row["humidity_pct"], "event_id": row["event_id"], "source": "INGESTED"}
                   for row in conn.execute("SELECT * FROM ingested_readings"))
    readings, quality_events, seen = [], [], set()
    for index, item in enumerate(raw, 1):
        truck = item.get("truck_id", "UNKNOWN") if isinstance(item, dict) else "UNKNOWN"
        timestamp = item.get("timestamp") if isinstance(item, dict) else None
        try:
            if not isinstance(item, dict) or not isinstance(truck, str) or not truck.strip():
                raise ValueError("Missing or invalid truck ID")
            parsed_time = datetime.fromisoformat(timestamp)
            temperature = float(item["temperature_c"])
            humidity = float(item["humidity_pct"]) if item.get("humidity_pct") is not None else None
            if not math.isfinite(temperature) or (humidity is not None and not math.isfinite(humidity)):
                raise ValueError("Non-finite sensor value")
        except (ValueError, TypeError, KeyError) as exc:
            quality_events.append({"type": "MALFORMED", "truck_id": truck, "timestamp": timestamp or "—",
                                   "details": str(exc), "source_record": index})
            continue
        if temperature < -40 or temperature > 60 or (humidity is not None and not 0 <= humidity <= 100):
            quality_events.append({"type": "IMPLAUSIBLE", "truck_id": truck, "timestamp": timestamp,
                                   "details": "Sensor value is outside demo plausibility bounds (-40 to 60°C; humidity 0 to 100%).",
                                   "source_record": index})
            continue
        if humidity is None:
            quality_events.append({"type": "HUMIDITY_MISSING", "truck_id": truck, "sensor_id": item.get("sensor_id"),
                                   "timestamp": timestamp, "details": "Temperature is available, but humidity is missing; no humidity alert decision was made for this sample.",
                                   "source_record": index})
        key = (truck, item.get("sensor_id"), parsed_time.isoformat(), temperature, humidity)
        if key in seen:
            quality_events.append({"type": "DUPLICATE", "truck_id": truck, "timestamp": timestamp,
                                   "details": "Repeated truck, timestamp, temperature, and humidity; duplicate ignored.",
                                   "source_record": index})
            continue
        seen.add(key)
        item = dict(item)
        item["temperature_c"] = temperature
        item["_time"] = parsed_time
        item["received_timestamp"] = item.get("received_timestamp", timestamp)
        readings.append(item)
    return readings, quality_events, len(raw)


def ingest_reading(payload):
    truck = str(payload.get("truck_id", "")).strip().upper()
    sensor = str(payload.get("sensor_id", "")).strip()
    event_timestamp = str(payload.get("event_timestamp", payload.get("timestamp", ""))).strip()
    if not truck or not sensor:
        raise ValueError("Truck ID and sensor ID are required.")
    event_time = datetime.fromisoformat(event_timestamp)
    temperature = float(payload["temperature_c"])
    humidity = float(payload["humidity_pct"]) if payload.get("humidity_pct") is not None else None
    if not math.isfinite(temperature) or temperature < -40 or temperature > 60:
        raise ValueError("Temperature must be finite and within the demo plausibility range (-40°C to 60°C).")
    if humidity is not None and (not math.isfinite(humidity) or not 0 <= humidity <= 100):
        raise ValueError("Humidity must be between 0% and 100%.")
    received = str(payload.get("received_timestamp") or datetime.now().astimezone().isoformat())
    event_id = str(payload.get("event_id") or f"{truck}|{sensor}|{event_time.isoformat()}|{temperature}|{humidity}")
    assignments = load_assignments()
    product_id = assignments.get(truck, {}).get("product_id", "SAMPLE-PHARMA")
    allowed_lateness = load_policies().get(product_id, DEFAULT_POLICY).get("allowed_lateness_min", 10)
    prior = [r["_time"] for r in load_readings()[0] if r["truck_id"] == truck]
    latest = max(prior) if prior else event_time
    late = (latest - event_time).total_seconds() / 60 > allowed_lateness
    try:
        with db_connect() as conn:
            conn.execute("INSERT INTO ingested_readings(event_id,truck_id,sensor_id,event_timestamp,received_timestamp,temperature_c,humidity_pct) VALUES(?,?,?,?,?,?,?)",
                         (event_id, truck, sensor, event_time.isoformat(), received, temperature, humidity))
    except sqlite3.IntegrityError:
        raise ValueError("Duplicate event ID; event was not inserted.")
    return {"event_id": event_id, "truck_id": truck, "event_timestamp": event_time.isoformat(),
            "received_timestamp": received, "late": late, "handling": "REPLAYED_AND_RECALCULATED" if late else "ORDERED_BY_EVENT_TIME"}


def detect_episodes(readings, policies, assignments):
    by_truck = {}
    for reading in readings:
        key = (reading["truck_id"], reading.get("sensor_id") or "")
        by_truck.setdefault(key, []).append(reading)
    episodes = []
    for (truck, sensor_id), rows in sorted(by_truck.items()):
        rows.sort(key=lambda row: row["_time"])
        assignment = assignments.get(truck, {"shipment_id": f"SHIP-{truck}", "product_id": "SAMPLE-PHARMA"})
        policy = policies.get(assignment["product_id"], policies.get("SAMPLE-PHARMA", DEFAULT_POLICY))
        current = None
        prior_time = None
        for row in rows:
            temp, timestamp = float(row["temperature_c"]), row["_time"]
            direction = "HIGH" if temp > policy["max_temp_c"] else "LOW" if temp < policy["min_temp_c"] else None
            if current and prior_time and (timestamp - prior_time).total_seconds() / 60 > policy["max_gap_min"]:
                current["closure"] = "UNKNOWN"
                episodes.append(current)
                current = None
            if current:
                if direction == current["direction"]:
                    current["readings"].append(row)
                    current["recovery_readings"] = []
                elif direction is None:
                    recovery_min = policy.get("recovery_min_temp_c", policy["min_temp_c"])
                    recovery_max = policy.get("recovery_max_temp_c", policy["max_temp_c"])
                    if recovery_min <= temp <= recovery_max:
                        current["recovery_readings"].append(row)
                        recovery = current["recovery_readings"]
                        stable_duration = (recovery[-1]["_time"] - recovery[0]["_time"]).total_seconds() / 60
                        if len(recovery) >= policy.get("recovery_readings", 2) and stable_duration >= policy.get("recovery_min_duration_min", 0):
                            current["closure"] = "RESOLVED"
                            current["resolved_at"] = timestamp.isoformat()
                            episodes.append(current)
                            current = None
                    else:
                        current["recovery_readings"] = []
                else:
                    current["closure"] = "UNKNOWN"
                    episodes.append(current)
                    current = None
                    if direction:
                        current = {"truck_id": truck, "sensor_id": sensor_id or None, "shipment_id": assignment["shipment_id"],
                                   "product_id": assignment["product_id"], "policy": policy, "direction": direction,
                                   "readings": [row], "recovery_readings": []}
            elif direction:
                current = {"truck_id": truck, "sensor_id": sensor_id or None, "shipment_id": assignment["shipment_id"],
                           "product_id": assignment["product_id"], "policy": policy, "direction": direction,
                           "readings": [row], "recovery_readings": []}
            prior_time = timestamp
        if current:
            current["closure"] = "RECOVERY_CHECK" if current["recovery_readings"] else "ACTIVE"
            episodes.append(current)

    incidents, alerts = [], []
    for ep in episodes:
        evidence = ep["readings"]
        start, end = evidence[0]["_time"], evidence[-1]["_time"]
        duration = (end - start).total_seconds() / 60
        policy = ep["policy"]
        tolerance = policy["high_tolerance_min"] if ep["direction"] == "HIGH" else policy["low_tolerance_min"]
        extreme_limit = policy["extreme_high_c"] if ep["direction"] == "HIGH" else policy["extreme_low_c"]
        extreme_row = next((row for row in evidence if (float(row["temperature_c"]) >= extreme_limit if ep["direction"] == "HIGH" else float(row["temperature_c"]) <= extreme_limit)), None)
        duration_trigger = next((row for row in evidence if (row["_time"] - start).total_seconds() / 60 >= tolerance), None)
        triggered = min((r for r in (extreme_row, duration_trigger) if r), key=lambda r: r["_time"], default=None)
        peak = max(float(row["temperature_c"]) for row in evidence) if ep["direction"] == "HIGH" else min(float(row["temperature_c"]) for row in evidence)
        threshold_value = policy["max_temp_c"] if ep["direction"] == "HIGH" else policy["min_temp_c"]
        incident_id = f'{ep["truck_id"]}-{ep["direction"]}-{start.strftime("%Y%m%dT%H%M%S")}'
        policy = ep["policy"]
        closure = ep.get("closure", "ACTIVE")
        state = "ACTIVE" if triggered and closure == "ACTIVE" else "MONITORING" if closure == "ACTIVE" else closure
        incident = {"incident_id": incident_id, "metric": "TEMPERATURE", "unit": "°C", "threshold_value": threshold_value,
                    "truck_id": ep["truck_id"], "sensor_id": ep.get("sensor_id"),
                    "shipment_id": ep["shipment_id"], "product_id": ep["product_id"], "policy_version": policy.get("version", 1),
                    "direction": ep["direction"], "started_at": start.isoformat(), "ended_at": end.isoformat(),
                    "resolved_at": ep.get("resolved_at"), "duration_min": duration, "tolerance_min": tolerance,
                    "peak_temp_c": peak, "status": state, "recovery_confirmations": len(ep.get("recovery_readings", [])),
                    "alert_id": f'ALT-{incident_id}' if triggered else None,
                    "latest_temperature_c": float(ep.get("recovery_readings", [])[-1]["temperature_c"] if ep.get("recovery_readings") else evidence[-1]["temperature_c"]),
                    "evidence": [{"timestamp": r["timestamp"], "temperature_c": r["temperature_c"], "humidity_pct": r.get("humidity_pct")} for r in evidence]}
        incidents.append(incident)
        if triggered:
            alerts.append({
                "alert_id": incident["alert_id"], "incident_id": incident_id,
                "truck_id": ep["truck_id"], "direction": ep["direction"], "started_at": start.isoformat(),
                "triggered_at": triggered["_time"].isoformat(), "ended_at": end.isoformat(),
                "duration_min": duration, "tolerance_min": tolerance, "peak_temp_c": peak,
                "severity": "CRITICAL", "status": state, "product_id": ep["product_id"], "metric": "TEMPERATURE",
                "unit": "°C", "threshold_value": threshold_value,
                "shipment_id": ep["shipment_id"], "policy_version": policy.get("version", 1),
                "delivery_status": "SIMULATED_ALERT_EMITTED",
                "evidence": [{"timestamp": r["timestamp"], "temperature_c": r["temperature_c"], "humidity_pct": r.get("humidity_pct")} for r in evidence],
                "reason": (f'Extreme {ep["direction"].lower()} threshold {extreme_limit}°C crossed at {triggered["timestamp"]}.'
                           if extreme_row and triggered is extreme_row else
                           f'{ep["direction"].title()} excursion exceeded its {tolerance}-minute tolerance at {triggered["timestamp"]}.'),
                "immediate": bool(extreme_row and triggered is extreme_row)
            })
    return episodes, incidents, alerts


def detect_humidity_episodes(readings, policies, assignments):
    """Create independent, policy-driven humidity incidents when humidity is present."""
    by_sensor = {}
    for row in readings:
        if row.get("humidity_pct") is not None:
            by_sensor.setdefault((row["truck_id"], row.get("sensor_id") or ""), []).append(row)
    incidents, alerts, episode_count = [], [], 0
    for (truck, sensor_id), rows in sorted(by_sensor.items()):
        rows.sort(key=lambda row: row["_time"])
        assignment = assignments.get(truck, {"shipment_id": f"SHIP-{truck}", "product_id": "SAMPLE-PHARMA"})
        policy = policies.get(assignment["product_id"], policies.get("SAMPLE-PHARMA", DEFAULT_POLICY))
        current, prior_time = None, None
        completed = []
        for row in rows:
            value, timestamp = float(row["humidity_pct"]), row["_time"]
            direction = "HIGH" if value > policy["max_humidity_pct"] else "LOW" if value < policy["min_humidity_pct"] else None
            if current and prior_time and (timestamp - prior_time).total_seconds() / 60 > policy["max_gap_min"]:
                current["closure"] = "UNKNOWN"
                completed.append(current)
                current = None
            if current:
                if direction == current["direction"]:
                    current["readings"].append(row)
                    current["recovery_readings"] = []
                elif direction is None:
                    recovery = policy.get("recovery_readings", 2)
                    current["recovery_readings"].append(row)
                    confirmations = current["recovery_readings"]
                    stable_min = (confirmations[-1]["_time"] - confirmations[0]["_time"]).total_seconds() / 60
                    if len(confirmations) >= recovery and stable_min >= policy.get("recovery_min_duration_min", 0):
                        current["closure"] = "RESOLVED"
                        current["resolved_at"] = timestamp.isoformat()
                        completed.append(current)
                        current = None
                else:
                    current["closure"] = "UNKNOWN"
                    completed.append(current)
                    current = None
                    if direction:
                        current = {"truck_id": truck, "sensor_id": sensor_id or None, "shipment_id": assignment["shipment_id"],
                                   "product_id": assignment["product_id"], "policy": policy, "direction": direction,
                                   "readings": [row], "recovery_readings": []}
            elif direction:
                current = {"truck_id": truck, "sensor_id": sensor_id or None, "shipment_id": assignment["shipment_id"],
                           "product_id": assignment["product_id"], "policy": policy, "direction": direction,
                           "readings": [row], "recovery_readings": []}
            prior_time = timestamp
        if current:
            current["closure"] = "RECOVERY_CHECK" if current["recovery_readings"] else "ACTIVE"
            completed.append(current)

        for episode in completed:
            episode_count += 1
            evidence = episode["readings"]
            start, end = evidence[0]["_time"], evidence[-1]["_time"]
            duration = (end - start).total_seconds() / 60
            high = episode["direction"] == "HIGH"
            tolerance = policy["high_humidity_tolerance_min"] if high else policy["low_humidity_tolerance_min"]
            threshold = policy["max_humidity_pct"] if high else policy["min_humidity_pct"]
            extreme = policy["extreme_high_humidity_pct"] if high else policy["extreme_low_humidity_pct"]
            extreme_row = next((row for row in evidence if (float(row["humidity_pct"]) >= extreme if high else float(row["humidity_pct"]) <= extreme)), None)
            duration_row = next((row for row in evidence if (row["_time"] - start).total_seconds() / 60 >= tolerance), None)
            trigger = min((row for row in (extreme_row, duration_row) if row), key=lambda row: row["_time"], default=None)
            peak = max(float(row["humidity_pct"]) for row in evidence) if high else min(float(row["humidity_pct"]) for row in evidence)
            incident_id = f"{truck}-HUMIDITY-{episode['direction']}-{start.strftime('%Y%m%dT%H%M%S')}"
            closure = episode.get("closure", "ACTIVE")
            status = "ACTIVE" if trigger and closure == "ACTIVE" else "MONITORING" if closure == "ACTIVE" else closure
            incident = {"incident_id": incident_id, "metric": "HUMIDITY", "unit": "%", "threshold_value": threshold,
                        "truck_id": truck, "sensor_id": sensor_id or None, "shipment_id": episode["shipment_id"],
                        "product_id": episode["product_id"], "policy_version": policy.get("version", 1),
                        "direction": episode["direction"], "started_at": start.isoformat(), "ended_at": end.isoformat(),
                        "resolved_at": episode.get("resolved_at"), "duration_min": duration, "tolerance_min": tolerance,
                        "latest_value": float(episode.get("recovery_readings", [])[-1]["humidity_pct"] if episode.get("recovery_readings") else evidence[-1]["humidity_pct"]),
                        "peak_value": peak, "status": status,
                        "recovery_confirmations": len(episode.get("recovery_readings", [])),
                        "evidence": [{"timestamp": row["timestamp"], "humidity_pct": row["humidity_pct"], "temperature_c": row["temperature_c"]} for row in evidence]}
            incidents.append(incident)
            if trigger:
                alerts.append({"alert_id": f"ALT-{incident_id}", "incident_id": incident_id, "truck_id": truck,
                               "direction": episode["direction"], "metric": "HUMIDITY", "unit": "%", "threshold_value": threshold,
                               "started_at": start.isoformat(), "triggered_at": trigger["_time"].isoformat(), "ended_at": end.isoformat(),
                               "duration_min": duration, "tolerance_min": tolerance, "peak_value": peak, "severity": "CRITICAL",
                               "status": status, "product_id": episode["product_id"], "shipment_id": episode["shipment_id"],
                               "policy_version": policy.get("version", 1), "delivery_status": "SIMULATED_ALERT_EMITTED",
                               "evidence": [{"timestamp": row["timestamp"], "humidity_pct": row["humidity_pct"], "temperature_c": row["temperature_c"]} for row in evidence],
                               "reason": (f"Extreme humidity {episode['direction'].lower()} threshold {extreme}% crossed at {trigger['timestamp']}."
                                          if extreme_row and trigger is extreme_row else
                                          f"Humidity {episode['direction'].lower()} excursion reached its {tolerance}-minute tolerance at {trigger['timestamp']}."),
                               "immediate": bool(extreme_row and trigger is extreme_row)})
    return incidents, alerts, episode_count


def evaluate_sensor_health(readings, policies, assignments):
    """Evaluate sensor confidence separately from the cargo temperature policy."""
    grouped = {}
    for row in readings:
        sensor_id = row.get("sensor_id")
        if sensor_id:
            grouped.setdefault((row["truck_id"], sensor_id), []).append(row)
    events, sensors, cross_sensor = [], [], {}

    # Compare readings only when sensors share a truck and event timestamp.
    simultaneous = {}
    for row in readings:
        if row.get("sensor_id"):
            simultaneous.setdefault((row["truck_id"], row["_time"]), []).append(row)
    for (truck, timestamp), rows in simultaneous.items():
        if len(rows) < 2:
            continue
        for index, left in enumerate(rows):
            for right in rows[index + 1:]:
                temperature_differs = abs(float(left["temperature_c"]) - float(right["temperature_c"])) >= 5.0
                humidity_differs = (left.get("humidity_pct") is not None and right.get("humidity_pct") is not None and
                                    abs(float(left["humidity_pct"]) - float(right["humidity_pct"])) >= 25.0)
                if temperature_differs or humidity_differs:
                    for row in (left, right):
                        key = (truck, row["sensor_id"])
                        cross_sensor.setdefault(key, set()).add(timestamp)

    priority = {"UNKNOWN": 0, "HEALTHY": 1, "STALE": 2, "SUSPECT": 3, "FAULTY": 4}
    for (truck, sensor_id), rows in sorted(grouped.items()):
        rows.sort(key=lambda row: row["_time"])
        assignment = assignments.get(truck, {"shipment_id": f"SHIP-{truck}", "product_id": "SAMPLE-PHARMA"})
        policy = policies.get(assignment["product_id"], policies.get("SAMPLE-PHARMA", DEFAULT_POLICY))
        indicator_types, gap_count, jump_clusters, stuck_count = set(), 0, 0, 0
        gaps = [(b["_time"] - a["_time"]).total_seconds() / 60 for a, b in zip(rows, rows[1:])]
        for index, gap in enumerate(gaps):
            if gap > policy["max_gap_min"]:
                gap_count += 1
                events.append({"type": "SENSOR_GAP", "truck_id": truck, "sensor_id": sensor_id,
                               "timestamp": rows[index + 1]["timestamp"], "details":
                               f"No sample for {gap:.0f} minutes, exceeding the {policy['max_gap_min']}-minute continuity limit. This is stale telemetry, not proof of sensor failure."})

        # A spike and its immediate return are one anomaly, not two independent signals.
        skip_pair = set()
        transient_returns = []
        humidity_transient_returns = []
        temps = [float(row["temperature_c"]) for row in rows]
        for index in range(1, len(rows)):
            interval = (rows[index]["_time"] - rows[index - 1]["_time"]).total_seconds() / 60
            if interval <= 0 or index in skip_pair:
                continue
            delta = abs(temps[index] - temps[index - 1])
            if delta / interval >= 2.0:
                jump_clusters += 1
                indicator_types.add("JUMP")
                transient = (index + 1 < len(rows) and
                             abs(temps[index + 1] - temps[index - 1]) <= 2.5 and
                             rows[index + 1]["_time"] - rows[index]["_time"] <= timedelta(minutes=policy["max_gap_min"]))
                if transient:
                    transient_returns.append(index + 1)
                    skip_pair.add(index + 1)
                events.append({"type": "SENSOR_ANOMALY", "truck_id": truck, "sensor_id": sensor_id,
                               "timestamp": rows[index]["timestamp"], "details":
                               f"Temperature changed by {delta:.1f}°C in {interval:.0f} minutes; " +
                               ("the following sample returned near baseline, so this is treated as a temporary anomaly." if transient else
                                "follow-up readings are needed; temperature remains included in cargo monitoring.")})

        humidity_values = [float(row["humidity_pct"]) if row.get("humidity_pct") is not None else None for row in rows]
        humidity_jump_count = 0
        humidity_skip_pair = set()
        for index in range(1, len(rows)):
            previous, value = humidity_values[index - 1], humidity_values[index]
            interval = (rows[index]["_time"] - rows[index - 1]["_time"]).total_seconds() / 60
            if previous is None or value is None or interval <= 0 or index in humidity_skip_pair or abs(value - previous) / interval < 5.0:
                continue
            humidity_jump_count += 1
            indicator_types.add("HUMIDITY_JUMP")
            transient = (index + 1 < len(rows) and humidity_values[index + 1] is not None and
                         abs(humidity_values[index + 1] - previous) <= 15.0 and
                         rows[index + 1]["_time"] - rows[index]["_time"] <= timedelta(minutes=policy["max_gap_min"]))
            if transient:
                humidity_transient_returns.append(index + 1)
                humidity_skip_pair.add(index + 1)
            events.append({"type": "SENSOR_HUMIDITY_ANOMALY", "truck_id": truck, "sensor_id": sensor_id,
                           "timestamp": rows[index]["timestamp"], "details":
                           f"Humidity changed by {abs(value - previous):.1f} percentage points in {interval:.0f} minutes; " +
                           ("the next sample returned near baseline." if transient else
                            "follow-up readings are needed; the humidity signal remains in alert evaluation.")})

        humidity_stuck_count = 0
        humidity_run_start = 0
        for index in range(1, len(rows) + 1):
            same = (index < len(rows) and humidity_values[index] is not None and humidity_values[humidity_run_start] is not None and
                    humidity_values[index] == humidity_values[humidity_run_start])
            contiguous = index < len(rows) and (rows[index]["_time"] - rows[index - 1]["_time"]).total_seconds() / 60 <= policy["max_gap_min"]
            if same and contiguous:
                continue
            run = rows[humidity_run_start:index]
            run_values = [humidity_values[j] for j in range(humidity_run_start, index) if humidity_values[j] is not None]
            duration = (run[-1]["_time"] - run[0]["_time"]).total_seconds() / 60 if run else 0
            if run_values and len(run_values) >= 4 and duration >= 15:
                humidity_stuck_count += 1
                # Temperature and humidity flatlines on one device are one signal category.
                indicator_types.add("STUCK")
                events.append({"type": "SENSOR_HUMIDITY_STUCK", "truck_id": truck, "sensor_id": sensor_id,
                               "timestamp": run[0]["timestamp"], "details":
                               f"Humidity stayed at {run_values[0]:.1f}% for {len(run_values)} consecutive readings over {duration:.0f} minutes. Flatline alone remains suspicious, not conclusive."})
            humidity_run_start = index

        run_start = 0
        for index in range(1, len(rows) + 1):
            same_value = index < len(rows) and temps[index] == temps[run_start]
            contiguous = index < len(rows) and (rows[index]["_time"] - rows[index - 1]["_time"]).total_seconds() / 60 <= policy["max_gap_min"]
            if same_value and contiguous:
                continue
            run = rows[run_start:index]
            duration = (run[-1]["_time"] - run[0]["_time"]).total_seconds() / 60 if run else 0
            if len(run) >= 4 and duration >= 15:
                stuck_count += 1
                indicator_types.add("STUCK")
                events.append({"type": "SENSOR_STUCK", "truck_id": truck, "sensor_id": sensor_id,
                               "timestamp": run[0]["timestamp"], "details":
                               f"Value stayed at {temps[run_start]:.1f}°C for {len(run)} consecutive readings over {duration:.0f} minutes. Flatline alone is suspicious, not conclusive."})
            run_start = index

        humidity_missing_count = sum(1 for row in rows if row.get("humidity_pct") is None)
        if humidity_missing_count >= 2:
            indicator_types.add("HUMIDITY_MISSING")
            events.append({"type": "SENSOR_HUMIDITY_MISSING", "truck_id": truck, "sensor_id": sensor_id,
                           "timestamp": rows[-1]["timestamp"], "details":
                           f"Humidity is absent from {humidity_missing_count} readings. The channel is SUSPECT; temperature readings continue to be evaluated and no hardware diagnosis is inferred."})

        disagreement_count = len(cross_sensor.get((truck, sensor_id), set()))
        if disagreement_count >= 2:
            indicator_types.add("DISAGREEMENT")
            events.append({"type": "SENSOR_DISAGREEMENT", "truck_id": truck, "sensor_id": sensor_id,
                           "timestamp": rows[-1]["timestamp"], "details":
                           f"Repeatedly differed by at least 5°C or 25 percentage points from another sensor on the same sample truck ({disagreement_count} shared timestamps). Verify a shared compartment and calibration."})

        diagnostic = next((str(row.get("diagnostic_status", "")).strip() for row in reversed(rows)
                           if str(row.get("diagnostic_status", "")).strip()), "")
        diagnostic_failed = any(flag in diagnostic.upper() for flag in ("FAIL", "FAULT", "ERROR"))
        if diagnostic_failed:
            events.append({"type": "SENSOR_DIAGNOSTIC", "truck_id": truck, "sensor_id": sensor_id,
                           "timestamp": rows[-1]["timestamp"], "details":
                           f"Device reported diagnostic '{diagnostic}'. Explicit hardware diagnostics confirm a sensor fault."})

        # Repeated identical values or a single recovered spike do not confirm a fault.
        recovered_temperature_jump = (jump_clusters == 1 and any(
            index + 2 < len(rows) and
            all(policy["min_temp_c"] <= temps[j] <= policy["max_temp_c"] for j in range(index, len(rows)))
            for index in transient_returns))
        recovered_humidity_jump = (humidity_jump_count == 1 and any(
            index + 2 < len(rows) and all(humidity_values[j] is not None and
                                          policy["min_humidity_pct"] <= humidity_values[j] <= policy["max_humidity_pct"]
                                          for j in range(index, len(rows)))
            for index in humidity_transient_returns))
        recovered_single_anomaly = ((recovered_temperature_jump or recovered_humidity_jump) and
                                    len(indicator_types) == 1 and not stuck_count and not humidity_stuck_count)
        if diagnostic_failed or len(indicator_types) >= 2:
            status = "FAULTY"
            reason = "Explicit device diagnostic" if diagnostic_failed else "Two independent evidence types"
            events.append({"type": "SENSOR_FAULT", "truck_id": truck, "sensor_id": sensor_id,
                           "timestamp": rows[-1]["timestamp"], "details": f"{reason} support a sensor-fault classification. Temperature incidents remain active and are evaluated independently."})
        elif indicator_types:
            status = "HEALTHY" if recovered_single_anomaly else "SUSPECT"
        elif gap_count:
            status = "STALE"
        else:
            status = "HEALTHY"
        sensors.append({"truck_id": truck, "sensor_id": sensor_id, "shipment_id": assignment["shipment_id"],
                        "product_id": assignment["product_id"], "status": status,
                        "sample_count": len(rows), "latest_timestamp": rows[-1]["timestamp"],
                        "max_gap_min": max(gaps, default=0), "jump_events": jump_clusters,
                        "stuck_runs": stuck_count, "humidity_jump_events": humidity_jump_count,
                        "humidity_stuck_runs": humidity_stuck_count, "humidity_missing_count": humidity_missing_count,
                        "disagreement_timestamps": disagreement_count,
                        "diagnostic_status": diagnostic or None,
                        "evidence_types": sorted(indicator_types),
                        "summary": ("Diagnostic failure" if diagnostic_failed else
                                    "Multiple independent indicators" if status == "FAULTY" else
                                    "Single abrupt spike recovered" if recovered_single_anomaly else
                                    "Anomaly evidence needs review" if status == "SUSPECT" else
                                    "Telemetry gap; no sensor fault inferred" if status == "STALE" else
                                    "No fault indicators detected")})
    by_truck = {}
    for sensor in sensors:
        by_truck.setdefault(sensor["truck_id"], []).append(sensor)
    for truck, truck_sensors in by_truck.items():
        events.append({"type": "SENSOR_HEALTH_SUMMARY", "truck_id": truck,
                       "timestamp": max(s["latest_timestamp"] for s in truck_sensors),
                       "details": "; ".join(f"{s['sensor_id']}: {s['status']} ({s['summary']})" for s in truck_sensors)})
    return sensors, events, by_truck, priority


def dashboard():
    policies = load_policies()
    assignments = load_assignments()
    readings, quality_events, raw_reading_count = load_readings()
    episodes, incidents, alerts = detect_episodes(readings, policies, assignments)
    humidity_incidents, humidity_alerts, humidity_episode_count = detect_humidity_episodes(readings, policies, assignments)
    incidents.extend(humidity_incidents)
    alerts.extend(humidity_alerts)
    sensors, sensor_events, sensors_by_truck, sensor_priority = evaluate_sensor_health(readings, policies, assignments)
    quality_events.extend(sensor_events)
    by_truck = {}
    for reading in readings:
        by_truck.setdefault(reading["truck_id"], []).append(reading)
    trucks = []
    reference_time = max(r["_time"] for r in readings)
    for truck, rows in sorted(by_truck.items()):
        rows.sort(key=lambda r: r["_time"])
        assignment = assignments.get(truck, {"shipment_id": f"SHIP-{truck}", "product_id": "SAMPLE-PHARMA"})
        policy = policies.get(assignment["product_id"], policies.get("SAMPLE-PHARMA", DEFAULT_POLICY))
        latest = rows[-1]
        gaps = [(b["_time"] - a["_time"]).total_seconds() / 60 for a, b in zip(rows, rows[1:])]
        stale = bool(gaps and max(gaps) > policy["max_gap_min"])
        if stale:
            quality_events.append({"type": "GAP", "truck_id": truck, "timestamp": latest["timestamp"],
                                   "details": f"Observed {max(gaps):.0f}-minute interval exceeds the {policy['max_gap_min']}-minute continuity limit."})
        temp = float(latest["temperature_c"])
        band = "HIGH" if temp > policy["max_temp_c"] else "LOW" if temp < policy["min_temp_c"] else "NORMAL"
        truck_quality = [q for q in quality_events if q.get("truck_id") == truck]
        truck_sensor_health = [s["status"] for s in sensors_by_truck.get(truck, [])]
        sensor_health = max(truck_sensor_health, key=lambda status: sensor_priority.get(status, 0)) if truck_sensor_health else "UNKNOWN"
        truck_incidents = [i for i in incidents if i["truck_id"] == truck and i["status"] in ("ACTIVE", "MONITORING", "RECOVERY_CHECK", "UNKNOWN")]
        truck_incidents.sort(key=lambda i: i["started_at"])
        current_state = band
        if truck_incidents:
            current_state = {"ACTIVE": "ALERT", "MONITORING": "MONITORING", "RECOVERY_CHECK": "RECOVERING", "UNKNOWN": "STALE/UNKNOWN"}.get(truck_incidents[-1]["status"], band)
        if stale and current_state not in ("ALERT", "MONITORING"):
            current_state = "STALE/UNKNOWN"
        received_times = [r.get("received_timestamp", r["timestamp"]) for r in rows]
        trucks.append({"truck_id": truck, "shipment_id": assignment["shipment_id"], "product_id": assignment["product_id"],
                       "policy_version": policy.get("version", 1), "range_min_c": policy["min_temp_c"], "range_max_c": policy["max_temp_c"],
                       "temperature_c": temp, "event_timestamp": latest["timestamp"], "received_timestamp": max(received_times),
                       "timestamp": latest["timestamp"], "band": band, "current_state": current_state,
                       "sensor_health": sensor_health, "freshness_state": "STALE" if stale else "FRESH (REPLAY)",
                       "data_quality": "GAP DETECTED" if stale else "HEALTHY", "reading_count": len(rows),
                       "max_gap_min": max(gaps, default=0), "humidity_pct": latest.get("humidity_pct")})
    history = []
    with db_connect() as conn:
        history = [dict(r) for r in conn.execute("SELECT product_id,version,effective_at,changed_at FROM policy_history ORDER BY id DESC LIMIT 10")]
    return {"policy": policies.get("SAMPLE-PHARMA", DEFAULT_POLICY), "policies": list(policies.values()),
            "assignments": assignments, "policy_history": history, "trucks": trucks,
            "incidents": incidents, "alerts": alerts, "quality_events": quality_events, "sensors": sensors,
            "episode_count": len(episodes) + humidity_episode_count, "reading_count": len(readings), "raw_reading_count": raw_reading_count,
            "as_of": reference_time.isoformat(),
            "notes": ["Sample policy values only; confirm product limits with the customer.",
                      "Dataset is a fixed replay. Freshness is assessed against observed gaps, not wall-clock time."]}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, content_type="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/dashboard":
            self._send(200, json.dumps(dashboard()))
        elif path == "/api/health":
            self._send(200, json.dumps({"status": "ok"}))
        elif path == "/api/incidents":
            self._send(200, json.dumps(dashboard()["incidents"]))
        elif path == "/api/alerts":
            self._send(200, json.dumps(dashboard()["alerts"]))
        elif path == "/api/policies":
            d = dashboard()
            self._send(200, json.dumps({"policies": d["policies"], "assignments": d["assignments"], "history": d["policy_history"]}))
        else:
            file_path = WEB_ROOT / ("index.html" if path == "/" else path.lstrip("/"))
            if not file_path.resolve().is_relative_to(WEB_ROOT.resolve()) or not file_path.is_file():
                self._send(404, "Not found", "text/plain; charset=utf-8")
                return
            mime = "text/html; charset=utf-8" if file_path.suffix == ".html" else "text/css; charset=utf-8" if file_path.suffix == ".css" else "application/javascript; charset=utf-8"
            self._send(200, file_path.read_bytes(), mime)

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/api/policy", "/api/policies", "/api/assignments", "/api/readings"):
            self._send(404, json.dumps({"error": "Not found"}))
            return
        try:
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            if path == "/api/policies":
                result = {"policy": save_product_policy(payload)}
            elif path == "/api/assignments":
                result = {"assignment": save_assignment(payload)}
            elif path == "/api/readings":
                result = ingest_reading(payload)
            else:
                result = {"policy": save_product_policy({"product_id": "SAMPLE-PHARMA", **payload})}
            self._send(201 if path == "/api/readings" else 200, json.dumps(result))
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self._send(400, json.dumps({"error": str(exc)}))

    def log_message(self, fmt, *args):
        print(f"{self.log_date_time_string()} {fmt % args}")


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", "8080"))
    print(f"Cold-chain dashboard listening on 0.0.0.0:{port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
