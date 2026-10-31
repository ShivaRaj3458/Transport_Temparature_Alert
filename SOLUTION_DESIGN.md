# Cold-chain alerting: design process

## Problem framing and assumptions

I translated Nimbus Freight's request into two goals: avoid escalating every brief temperature spike, and emit only one alert for each sustained excursion. A fixed threshold cannot do that alone; the evaluator needs elapsed time, episode state, and evidence an operator can inspect. I treated missing or questionable telemetry as uncertainty: a gap cannot prove cargo was safe, and an unusual reading alone cannot prove a sensor failed.

The original 54 readings cover five trucks and include a sustained high, a brief spike, normal operation, a telemetry gap, and out-of-order events. They lack product, shipment, compartment, and sensor mappings, as well as received-time and device-health fields. For demonstration, I added labeled sample assignments and edge cases, then consolidated all 155 records and case notes into one `sensor_readings.json` file while preserving the seed readings.

Sample policy values are 2 to 8 deg C, 15 minutes of high-side temperature tolerance, 10 minutes of low-side temperature tolerance, and a seven-minute continuity limit. Humidity has separate illustrative bounds of 30% to 80% RH, 10-minute high/low tolerances, and optional immediate extremes at 95% and 15% RH. Temperature extremes are 12 deg C and -5 deg C; input outside -40 to 60 deg C or humidity outside 0% to 100% is quarantined. These are demo assumptions, not approved cargo guidance. Humidity alerts are an independent signal and do not change temperature incident rules.

## Design decisions

I chose a deterministic event-time flow so each result can be replayed and explained:

1. Validate readings and quarantine malformed or implausible values.
2. Deduplicate, group by truck and sensor where available, and sort by event time.
3. Resolve the sample product policy assigned to the truck.
4. Evaluate temperature and humidity episodes independently, then evaluate sensor confidence separately from both.
5. Show incident state, policy version, and supporting readings in the dashboard.

Python's standard library serves the API and static UI. SQLite persists product policies, versions, effective times, and sample assignments. The dashboard separates fleet status, incidents, alerts, and policy management so an operator can see both the decision and the rule behind it.

## Temperature and incident behavior

A valid sample above the maximum or below the minimum starts a potential episode. The evaluator measures elapsed time between observed event timestamps, not readings multiplied by an assumed interval. It escalates at the first observed sample at or beyond that side's tolerance, unless an optional extreme limit triggers immediately. Later readings update the same episode. A gap longer than the continuity limit ends the observed segment as uncertain; missing time is not counted as exposure or recovery.

In-range samples move an episode into `RECOVERY_CHECK`. Two consecutive readings inside the configured recovery band, plus any configured minimum stable duration, resolve it. An out-of-range sample resets recovery checking. A new episode after confirmed recovery receives a new ID. Since readings are samples, the physical crossing time is unknown; the dashboard reports the first qualifying observation.

When humidity is present, a separate evaluator applies the product's humidity limits, duration tolerances, extreme thresholds, and recovery confirmation. It creates its own explainable incident. This lets the customer choose whether high or low humidity is alert-worthy without treating humidity as a proxy for temperature or sensor failure.

| Example | Decision demonstrated |
|---|---|
| T-01 sustained high | One alert at the first observed sample reaching tolerance; five-minute sampling means the trigger is observed 20 minutes after the first high reading. |
| T-02 brief spike / T-03 normal | Avoid a sustained alert for a short spike and show normal operation without noise. |
| T-04 gap / T-05 unordered | Split uncertain evidence at a long gap; sort delayed-in-file readings before evaluating. |
| T-06 through T-08 | Show low-side tolerance, recovery, and immediate high/low extremes. |
| T-11 unrecovered high | Keep the incident ACTIVE at the end of replay. |
| T-20 / T-21 | Show sustained high- and low-humidity alerts and recovery. |
| T-22 | Show simultaneous independent temperature and humidity incidents. |
| T-23 / T-24 | Show a recovered humidity jump and a humidity flatline contributing sensor-health evidence. |
| T-25 | Show a missing humidity channel as SUSPECT while continuing to evaluate available temperature readings. |

## Sensor-health behavior

Sensor confidence must not suppress temperature or humidity monitoring, so it runs as a separate evaluation. Abrupt changes and prolonged identical values in either signal can raise sensor suspicion. A single abrupt jump that returns near baseline and is followed by two in-range readings clears to **HEALTHY**. One anomaly category or flatline alone remains **SUSPECT**. Two independent evidence types, or an explicit failed device diagnostic, can classify a sensor **FAULTY**. A continuity gap marks telemetry **STALE**; it does not by itself diagnose hardware failure.

Synthetic cases show each stage: T-12 is a stuck signal that remains SUSPECT; T-13 is a recovered temperature jump; T-14 has repeated jumps but stays SUSPECT; T-15 combines jump and flatline evidence; T-17 is stale without a fault diagnosis; T-18 has an explicit calibration failure; T-19 shows repeated disagreement between paired sensors; T-16 keeps a cargo alert active while the sensor is SUSPECT. T-23 and T-24 add humidity anomaly examples, and T-25 shows missing humidity samples. Rates and evidence thresholds are demo heuristics and need validation for real sensor models and verified compartment mappings.

## Operator view and trade-offs

The fleet view shows the latest valid temperature and humidity readings, event and receive times, shipment/product, applicable ranges, exposure state, freshness, and sensor confidence. Incident history exposes metric, state, limits, and evidence; alert activity explains the escalation; product policies expose editable temperature and humidity bounds/tolerances, recovery, extremes, and version history. Notifications are simulated, with no acknowledgement workflow.

The fixed replay and small standard-library server keep setup simple, but this is not a streaming production system. The API sorts and recalculates the replay, reports late ingestion, and stores policy history; it does not implement a lateness buffer, durable incident lifecycle, or idempotent notification delivery. Freshness is based on gaps inside the file, not wall-clock age. Seed data cannot validate product mapping, sensor placement, connectivity, battery, or calibration.

## Next steps

Validate policy and sensor rules with cargo owners and labeled history. Then add authenticated streaming ingestion with event/receive timestamps, explicit late-event correction, compartment-level policy resolution, durable incident transitions and notifications, wall-clock freshness, operator acknowledgement, and production security and monitoring.
