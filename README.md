# Coldwatch Cold-Chain Monitor

A small full-stack prototype for reviewing cold-chain telemetry and explainable temperature incidents. It includes a Python HTTP API, a responsive browser dashboard, configurable sample policy values, SQLite policy persistence, and a Docker Compose deployment.

## Run with Docker Compose

From the repository root:

```sh
docker compose up --build
```

Open [http://localhost:8080](http://localhost:8080). Stop the service with `Ctrl+C`; run `docker compose down` to remove the container. The named `coldchain-data` volume keeps saved policy values across restarts. To remove the saved policy too, run `docker compose down -v`.

## Run locally without Docker

Requires Python 3.9 or newer; there are no third-party Python dependencies.

```sh
python app.py
```

Then open [http://localhost:8080](http://localhost:8080). Set `PORT` or `COLDCHAIN_DB` to change the listening port or SQLite database path.

## What the prototype does

- Provides separate fleet overview, incident history, alert activity, and product-policy views. Fleet rows show the truck's shipment/product, assigned range, latest valid event and received times, temperature state, telemetry freshness, and sensor-health confidence.
- Shows incident evidence readings, recovery confirmations, policy version, alert reason, and simulated alert activity. Product policies can be created or edited with independent temperature and humidity bounds/tolerances, recovery rules, extreme limits, continuity gap, lateness allowance, and effective time; each save records a new version in policy history.
- Sorts readings per truck by event timestamp before evaluating them, so T-05 is handled in chronological order.
- Evaluates both high and low excursions using elapsed time between observed readings, and supports immediate escalation at configured extreme limits.
- Evaluates humidity independently with product-specific high/low limits, tolerances, recovery, and optional extreme thresholds. Humidity alerts do not suppress or reclassify temperature alerts.
- Splits an episode when the gap between consecutive readings exceeds the configured continuity limit. It does not count missing time as confirmed exposure.
- Deduplicates repeated truck/timestamp/value events and quarantines malformed or implausible sensor records with a visible reason.
- Generates one critical incident per qualifying excursion, including its evidence readings, start, sampled trigger time, end, observed duration, peak, and status.
- Marks an incident ACTIVE when it has crossed tolerance and the replay ends before recovery; its notification action is labeled as simulated, not sent to an external channel.
- Stores policy edits in SQLite. The dashboard applies the selected policy to the full sample replay whenever it loads.
- Shows each truck's latest sample, observed maximum gap, temperature band, incident state, and fleet summary.

The product-policy editor and alert views operate on the included replay. The sample assignments illustrate the truck → shipment → product policy relationship; changing an assignment is available through the API, while the dashboard displays the current assignments.

## Sample policy defaults

The defaults are **illustrative test settings only**: 2 to 8 deg C, 15-minute high and 10-minute low temperature tolerances; 30% to 80% RH, 10-minute high and low humidity tolerances; a 7-minute continuity gap; temperature extremes at 12 / -5 deg C; and humidity extremes at 95% / 15% RH. The editor stores product-specific versions and the demo assigns sample products to trucks. Production needs customer-approved temperature and humidity rules, shipment-to-product mapping, and compartment-level policies where needed.

## Included scenario coverage

`starter_kit/data/sensor_readings.json` is the single replay file: it keeps the original 54 readings and includes 101 additional labeled cases for temperature and humidity excursions, validation and duplicate handling, gaps, ordering, recovery, and staged sensor health. Cases cover a recovered single jump, repeated jumps and flatlines that remain SUSPECT, independent evidence or device diagnostics that confirm FAULTY, paired-sensor disagreement, and stale telemetry. T-16 demonstrates that sensor suspicion does not suppress a cargo-temperature alert. T-20 and T-21 demonstrate high/low humidity alerts; T-22 demonstrates simultaneous temperature and humidity incidents; T-23 through T-25 demonstrate humidity anomalies and missing humidity data. Case notes are stored with the readings. Cross-sensor comparison assumes paired demo sensors share a truck and event time; real deployments need verified compartment mappings. These are synthetic fixtures, not customer telemetry.

Sensor-health rules are intentionally illustrative: rapid changes or flatlines in temperature or humidity are flagged for review; one jump that returns to baseline and is followed by two in-range readings clears to HEALTHY; and two independent evidence categories or an explicit failed device diagnostic classify a sensor as FAULTY. A stuck value alone remains SUSPECT. A telemetry gap sets freshness to STALE without declaring a hardware fault. Temperature and humidity excursion evaluations run independently from sensor health.

## API

- `GET /api/health` — service health.
- `GET /api/dashboard` - product policies, truck and sensor summaries, temperature/humidity incidents and alerts, and replay metadata.
- `GET /api/incidents` — evaluated incident list.
- `POST /api/policies` - create or update a versioned product policy. `/api/policy` remains a sample-pharma alias.
- `POST /api/readings` - append a validated reading to the replay store.

## Known limits

This is a static batch replay, not a production streaming service. The API uses Python's standard-library HTTP server and is intended for a local demo. It does not authenticate users or deliver external notifications. Humidity bounds and tolerances are illustrative rules, not universal cargo guidance. The original seed data has no product, shipment, compartment, sensor identity, battery, or calibration fields; supplemental fixtures add sample mappings and diagnostic examples only. Sensor-health rules are evidence-based demo heuristics and do not prove hardware failure without corroboration or explicit diagnostics. Incident results are derived from the current dataset and policy on each request; policies persist in SQLite, but incident lifecycle and delivery state do not. Freshness is based on gaps within the replay rather than wall-clock age.

The dashboard assesses gap quality against gaps observed inside this fixed file, not against current wall-clock time. Sample duration is observation-based: a threshold tolerance is crossed at the first violating sample whose elapsed time from the first violating sample is greater than the tolerance.

## Next production steps

Add authenticated ingestion with event IDs and both event/received timestamps; durable incident state with idempotency; product/shipment policy lookup and versioning; configurable recovery hysteresis; sensor freshness based on wall-clock or stream watermarks; notification routing and acknowledgement; and deployment behind HTTPS with secrets, backups, and monitoring.
