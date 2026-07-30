# Solution notes — Prometheus & Grafana exam (Bike Sharing drift monitoring)

## Running the project

```bash
make          # builds and starts bike-api, prometheus, grafana, node-exporter
make traffic  # sends traffic to /predict so the API dashboard fills up
make evaluation   # runs the provided run_evaluation.py against /evaluate
make fire-alert   # deliberately triggers the drift / RMSE alerts
make stop     # stops everything
```

| Service | URL |
|---|---|
| API (Swagger) | http://localhost:8080/docs |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 — `admin` / `admin` |

On the first `make`, the API container downloads the UCI Bike Sharing dataset and
trains the `RandomForestRegressor` on January 2011 before it starts answering. It
is normal for the `bike-api` target to show as DOWN in Prometheus for the first
minute or two; the container healthcheck covers this and `make evaluation` waits
for the API to be healthy before sending anything.

## Where each requirement is implemented

| Requirement | Location |
|---|---|
| `_fetch_data`, `_process_data`, `_train_and_predict_reference_model` | `src/api/main.py` |
| Model trained once, at container startup | `_load_model_and_reference()`, called at import |
| `/predict` with `BikeSharingInput` | `src/api/main.py` |
| `/evaluate` with Evidently report | `src/api/main.py` |
| `/metrics` | `src/api/main.py` |
| Prometheus scrape config | `deployment/prometheus/prometheus.yml` |
| Prometheus alert rules | `deployment/prometheus/rules/alert_rules.yml` |
| Grafana datasource provisioning | `deployment/grafana/provisioning/datasources/datasources.yaml` |
| Grafana dashboard provisioning | `deployment/grafana/provisioning/dashboards/dashboards.yaml` |
| Grafana alert provisioning | `deployment/grafana/provisioning/alerting/alerting.yaml` |
| Dashboards as code | `deployment/grafana/dashboards/*.json` |
| Traffic generation | `scripts/generate_traffic.sh` (`make traffic`) |

## Metrics exposed

| Metric | Type | Labels | Updated by |
|---|---|---|---|
| `api_requests_total` | Counter | `endpoint`, `method`, `status_code` | every request |
| `api_request_duration_seconds` | Histogram | `endpoint`, `method`, `status_code` | every request |
| `model_rmse_score` | Gauge | — | `/evaluate` |
| `model_mae_score` | Gauge | — | `/evaluate` |
| `model_r2_score` | Gauge | — | `/evaluate` |
| `model_mape_score` | Gauge | — | `/evaluate` |
| `evidently_data_drift_detected_status` | Gauge | — | `/evaluate` |
| `evidently_drifted_columns_share` | Gauge | — | `/evaluate` |

### Justification of the additional metrics

**`evidently_data_drift_detected_status`** is the metric that matters most here.
RMSE, MAE, R² and MAPE all require the ground truth `cnt`, which in a real bike
sharing deployment is only known after the fact. Input drift is computable from
the features alone, at prediction time, so it is the earliest signal available
that the model is being asked to extrapolate outside its January training
distribution. It is deliberately a 0/1 verdict so that it can be alerted on
without choosing a threshold.

**`evidently_drifted_columns_share`** is the continuous version of the same
signal. The 0/1 status tells you drift crossed the 0.5 threshold; the share lets
you watch it approach that threshold beforehand.

**`model_mape_score`** is scale-free, unlike RMSE and MAE which are expressed in
bikes. One caveat worth knowing when reading the dashboard: hourly demand falls
to single digits overnight, so those near-zero hours inflate the percentage
error. Its movement between comparable periods is informative; its absolute
level is not.

## Alerting

Prometheus rules (`deployment/prometheus/rules/alert_rules.yml`):

| Alert | Condition | `for` |
|---|---|---|
| `BikeApiDown` | `up{job="bike-api"} == 0` | 1m |
| `NodeExporterDown` | `up{job="node-exporter"} == 0` | 1m |
| `HighApiErrorRate` | non-2xx share > 20% | 2m |
| `DataDriftDetected` | `evidently_data_drift_detected_status == 1` | 30s |
| `SevereDataDrift` | `evidently_drifted_columns_share > 0.7` | 30s |
| `ModelRMSEHigh` | `model_rmse_score > 400` | 30s |
| `ModelR2Degraded` | `model_r2_score < 0.5` | 5m |

Grafana rule (`provisioning/alerting/alerting.yaml`): `ModelRMSEHigh`, on the ML
metric `model_rmse_score` above 400 for 1 minute. It is provisioned as code
rather than only created through the UI so that it exists on a first `make` on
any machine instead of living in this machine's `grafana_data` volume. It is
visible and editable in the Grafana UI under Alerting → Alert rules.

### Which alert `make fire-alert` tests

`make fire-alert` POSTs to `/trigger-drift`, which evaluates a deliberately
corrupted batch: the features are pinned to extreme values (hot, dry, windy, bad
weather, wrong season) and the target is inflated to ten times its value plus
1000.

The alert being tested is **`ModelRMSEHigh`**, together with its Grafana
counterpart of the same name, because it is the one that demonstrates a clean
inactive → pending → firing transition. Measured on the real dataset:

| | routine `make evaluation` (week 1 February) | after `make fire-alert` |
|---|---|---|
| `model_rmse_score` | 17.52 | 1583.92 |
| `model_mae_score` | 10.46 | 1511.57 |
| `model_r2_score` | 0.885 | −9.60 |
| `model_mape_score` | 29.37 | 97.18 |
| `evidently_drifted_columns_share` | 0.538 | 0.846 |
| `evidently_data_drift_detected_status` | 1 | 1 |

A threshold of 400 sits two orders of magnitude away from normal operation, so
the transition is unambiguous rather than borderline.

**`SevereDataDrift` is the second alert tested**, and it exists because of the
last row of that table. Scoring February against a January reference legitimately
drifts about 54% of the monitored columns — seasonal change is the premise of the
exercise, not a fault — so `evidently_data_drift_detected_status` is already 1
after a routine `make evaluation` and `DataDriftDetected` is the normal state of
this dataset at warning level. Rather than retune what Evidently calls drift, the
verdict is left faithful to the tool and a second tier at 70% separates expected
seasonal drift from a genuinely broken batch. That threshold sits between the two
measured values, so `SevereDataDrift` is inactive during normal operation and
fires only on the corrupted batch.

This follows the chapter 5 principle on alert fatigue: an alert that is always on
carries no information. The warning tier records that drift exists; the critical
tier is the one worth waking someone for.

`ModelR2Degraded` also enters `pending` on the corrupted batch and reaches
`firing` if the drifted state is left in place for 5 minutes.

Timings observed when testing: `ModelRMSEHigh` and `SevereDataDrift` reach
`firing` about 45–60 seconds after `make fire-alert`, being `for: 30s` on a 15s
evaluation interval. `BikeApiDown` needs about 90 seconds after the container is
stopped: up to 15s for the failed scrape, then `for: 1m`, then the next rule
evaluation. Stopping the API also returns the model alerts to `inactive`, since
their series stop being reported.

## Design decisions worth flagging

**Evidently 0.7.21.** The skeleton's imports (`from evidently import Report,
Dataset, DataDefinition, Regression`) are the 0.7 API; the older
`evidently.report` / `metric_preset` API found in most tutorials does not provide
them. The version is pinned in `src/api/requirements.txt`.

**Report composition.** The report combines `DataDriftPreset` with the
individual `RMSE`, `MAE`, `R2Score` and `MAPE` metrics rather than
`RegressionPreset`. That is what the provided skeleton imports
(`from evidently.metrics import MAE, RMSE, R2Score`), and it keeps the report
to exactly the four regression figures the exam asks to be exported as Gauges
instead of computing the preset's full set.

**Reading the report.** Metrics are matched on `config['type']`
(`evidently:metric_v2:RMSE`, …) rather than on the display name, because the
display name embeds the metric parameters. The values do not share one shape:
RMSE and R2Score come back as plain floats while MAE and MAPE come back as
`{'mean', 'std'}` dicts, so `_scalar()` normalises them. `DriftedColumnsCount`
returns `{'count', 'share'}`, and dataset drift is declared from the *share*
against the preset's `drift_share` threshold — not from `count > 0`, which would
report drift for a single column. Evaluating a normal February week does drift
exactly one column (`mnth`, January vs February) and must still read as no drift.

**`/metrics` is not instrumented.** Prometheus scrapes it every 15 seconds; if
those scrapes were counted, they would dominate the request-rate panels and make
the API dashboard meaningless.

**Validation errors are instrumented.** FastAPI rejects a malformed body before
the endpoint function runs, so a `RequestValidationError` handler records the
422 explicitly. Without it, `api_requests_total` would never see invalid client
payloads and the error-rate panel would be blind to them. `scripts/generate_traffic.sh`
sends one malformed request in twelve on purpose, so the error-rate panel and
`HighApiErrorRate` have something real to show.

**Histogram buckets are explicit.** `/predict` answers in single-digit
milliseconds and `/evaluate` takes seconds. The default buckets stop at 10s and
would saturate on `/evaluate`; buckets starting only at 0.05 collapse every
`/predict` observation into one bucket and make its P95 meaningless. The range
used covers both.

**Explicit Compose project name** (`name: bike-monitoring`) so volume and network
names are identical on every machine and cannot collide with volumes left by
other projects.

**Grafana datasource has a fixed `uid: prometheus`**, referenced directly by the
dashboard JSON. This is what makes the dashboards work on a first `make` with an
empty `grafana_data` volume, with no manual datasource selection.

**Dashboards are bind-mounted**, from `deployment/grafana/dashboards` to
`/etc/grafana/dashboards`, and loaded by the file provider. Nothing about the
dashboards depends on volume contents.

**`evaluation` uses a Compose profile** so it stays out of `docker compose up`.
It is a one-shot script, not a long-running service, and `make evaluation` starts
it explicitly.

**The Makefile detects Compose v1 vs v2.** The starter Makefile called
`docker-compose`, which does not exist on installations that only ship the v2
`docker compose` plugin.

**Model gauges start at NaN, not 0.** A `prometheus_client` Gauge initialises to
0, which is indistinguishable from a real measurement: on a freshly started stack
`model_r2_score` would read 0, making `model_r2_score < 0.5` true and sending
`ModelR2Degraded` to `pending` before any evaluation had run. NaN expresses "not
measured yet" — PromQL comparisons against NaN are false, so no model alert can
fire until `/evaluate` has set a real value. The consequence to expect is that the
model panels show `No data` until the first `make evaluation`, which is accurate.

**Memory panel uses `MemAvailable`** rather than `MemFree`. `MemFree` excludes
reclaimable page cache and therefore overstates memory pressure on Linux.

## Things that are expected, not faults

- With no recent traffic, the API error-rate and P95 latency panels show `NaN`:
  `rate()` of a flat counter is 0, so the ratio is 0/0. Run `make traffic` and
  they populate: the script sends one malformed request in twelve, so the error
  rate settles around 8%.
- The model gauges are flat lines that step when `/evaluate` runs. They are
  point-in-time evaluations, not continuous measurements.
- `predictions`-style counters do not exist for a regression model; the
  distribution question is answered here by the drift metrics instead.
- January 2011 contains **688 hourly rows**, not 744. The UCI dataset omits hours
  with no rentals (17,379 rows where two complete years would be 17,544), so the
  reference model trains on 688 rows and a February week sends roughly 235 records
  to `/evaluate` — comfortably below the 1000-sample cap in `run_evaluation.py`.
