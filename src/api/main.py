import datetime
import io
import logging
import os
import time
import warnings
import zipfile
from typing import Any, Optional

import pandas as pd
import requests
from sklearn.ensemble import RandomForestRegressor

from evidently import Report, Dataset, DataDefinition, Regression
from evidently.metrics import MAE, RMSE, R2Score, MAPE
from evidently.presets import DataDriftPreset

from fastapi import FastAPI, HTTPException, Response, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from prometheus_client import Counter, Histogram, generate_latest, CollectorRegistry, Gauge

# Evidently computes drift statistics on constant columns during the forced-drift
# scenario, which makes numpy emit divide-by-zero warnings on every call.
warnings.filterwarnings("ignore", category=RuntimeWarning)
requests.packages.urllib3.disable_warnings()

# --- Logging Configuration ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- FastAPI App Initialization ---
app = FastAPI(
    title="Bike Sharing Predictor API",
    description="API for predicting bike sharing demand with MLOps monitoring.",
    version="1.0.0"
)

# --- Prometheus Metrics Definitions ---
registry = CollectorRegistry()

api_requests_total = Counter(
    'api_requests_total',
    'Total number of API requests',
    ['endpoint', 'method', 'status_code'],
    registry=registry
)

# Two endpoints with very different timescales share this histogram: /predict
# answers in single-digit milliseconds while /evaluate runs a full Evidently
# report over a few hundred rows. The low buckets keep the /predict percentiles
# from collapsing into one bucket; the high ones stop /evaluate saturating the
# default 10s ceiling.
api_request_duration_seconds = Histogram(
    'api_request_duration_seconds',
    'API request duration in seconds',
    ['endpoint', 'method', 'status_code'],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0,
             10.0, 30.0, 60.0, 120.0, float('inf')),
    registry=registry
)

model_rmse_score = Gauge(
    'model_rmse_score',
    'Root Mean Squared Error of the regression model on the last evaluated batch',
    registry=registry
)

model_mae_score = Gauge(
    'model_mae_score',
    'Mean Absolute Error of the regression model on the last evaluated batch',
    registry=registry
)

model_r2_score = Gauge(
    'model_r2_score',
    'R2 score of the regression model on the last evaluated batch',
    registry=registry
)

# Custom metric 1: MAPE, reported because run_evaluation.py surfaces it and because
# it is scale-free, unlike RMSE/MAE which are expressed in bikes. Caveat worth
# knowing when reading the dashboard: hourly demand falls to single digits
# overnight, so the percentage error is inflated by those near-zero hours and the
# absolute level matters less than its movement between comparable periods.
model_mape_score = Gauge(
    'model_mape_score',
    'Mean Absolute Percentage Error of the regression model on the last evaluated batch',
    registry=registry
)

# Custom metric 2: RMSE/MAE/R2 all need the ground truth, which in production
# arrives late or not at all. Input drift is observable immediately from the
# features alone, so it is the earliest available warning that the model is being
# asked to extrapolate outside its training distribution.
evidently_data_drift_detected_status = Gauge(
    'evidently_data_drift_detected_status',
    'Dataset drift verdict from Evidently on the last evaluated batch (1 = drift detected, 0 = no drift)',
    registry=registry
)

evidently_drifted_columns_share = Gauge(
    'evidently_drifted_columns_share',
    'Share of columns flagged as drifted by Evidently on the last evaluated batch',
    registry=registry
)

# A prometheus_client Gauge starts at 0, which is indistinguishable from a real
# measurement: model_r2_score would read 0 on a freshly started stack, making
# `model_r2_score < 0.5` true and firing ModelR2Degraded before any evaluation has
# happened. NaN expresses "not measured yet" instead. PromQL comparisons against
# NaN are false, so no model alert can fire until /evaluate has set a real value.
for _gauge in (model_rmse_score, model_mae_score, model_r2_score, model_mape_score,
               evidently_data_drift_detected_status, evidently_drifted_columns_share):
    _gauge.set(float('nan'))

# --- Global Variables for Model and Data ---
TARGET = 'cnt'
PREDICTION = 'prediction'
NUM_FEATS = ['temp', 'atemp', 'hum', 'windspeed', 'mnth', 'hr', 'weekday']
CAT_FEATS = ['season', 'holiday', 'workingday', 'weathersit']

ALL_FEATS = NUM_FEATS + CAT_FEATS
DTEDAY_COL_NAME = 'dteday'
DATASET_URL = "https://archive.ics.uci.edu/static/public/275/bike+sharing+dataset.zip"

REFERENCE_START = '2011-01-01 00:00:00'
REFERENCE_END = '2011-01-31 23:00:00'

MODEL: Optional[RandomForestRegressor] = None
REFERENCE_DATA: Optional[pd.DataFrame] = None
REFERENCE_DATASET: Optional[Dataset] = None

DATA_DEFINITION = DataDefinition(
    numerical_columns=NUM_FEATS + [TARGET, PREDICTION],
    categorical_columns=CAT_FEATS,
    regression=[Regression(target=TARGET, prediction=PREDICTION)],
)


# --- Data Ingestion and Preparation Functions ---
def _fetch_data() -> pd.DataFrame:
    """Fetches the bike sharing dataset and returns a DataFrame."""
    # Offline override: lets the stack start from a local copy of hour.csv when the
    # UCI archive is unreachable, without changing the normal code path.
    local_csv = os.getenv("BIKE_DATA_CSV")
    if local_csv and os.path.exists(local_csv):
        logger.info(f"Loading bike sharing data from local file {local_csv}")
        return pd.read_csv(local_csv, header=0, sep=',', parse_dates=[DTEDAY_COL_NAME])

    logger.info("Fetching data from UCI archive...")
    content = requests.get(DATASET_URL, verify=False, timeout=120).content
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        df = pd.read_csv(z.open("hour.csv"), header=0, sep=',', parse_dates=[DTEDAY_COL_NAME])
    logger.info(f"Data fetched successfully: {df.shape[0]} rows.")
    return df


def _process_data(raw_data: pd.DataFrame) -> pd.DataFrame:
    """Processes raw data, setting a DatetimeIndex built from dteday and hr."""
    raw_data['hr'] = raw_data['hr'].astype(int)
    raw_data.index = raw_data.apply(
        lambda row: datetime.datetime.combine(row[DTEDAY_COL_NAME].date(), datetime.time(row.hr)),
        axis=1
    )
    raw_data = raw_data.sort_index()
    logger.info("Data processed successfully.")
    return raw_data


def _train_and_predict_reference_model(processed_data: pd.DataFrame):
    """Trains the RandomForestRegressor on January 2011 and scores that reference period."""
    reference = processed_data.loc[REFERENCE_START:REFERENCE_END].copy()
    if reference.empty:
        raise RuntimeError("No January 2011 data available to train the reference model.")

    model = RandomForestRegressor(random_state=42, n_estimators=100, n_jobs=-1)
    model.fit(reference[ALL_FEATS], reference[TARGET])
    reference[PREDICTION] = model.predict(reference[ALL_FEATS])

    logger.info(f"Reference model trained on {reference.shape[0]} rows (January 2011).")
    return model, reference


def _load_model_and_reference() -> None:
    """Trains the model once, at API startup, and caches the reference dataset."""
    global MODEL, REFERENCE_DATA, REFERENCE_DATASET
    processed = _process_data(_fetch_data())
    MODEL, REFERENCE_DATA = _train_and_predict_reference_model(processed)
    REFERENCE_DATASET = Dataset.from_pandas(REFERENCE_DATA, data_definition=DATA_DEFINITION)


try:
    _load_model_and_reference()
except Exception as e:
    logger.error(f"Error preparing model and reference data: {e}")
    raise RuntimeError("Failed to prepare the model, application cannot start.") from e


# --- Metric and Report Helpers ---
def _record_request(endpoint: str, method: str, status_code: str, start_time: float) -> None:
    """Single place where the API counters/histogram are updated."""
    api_request_duration_seconds.labels(
        endpoint=endpoint, method=method, status_code=status_code
    ).observe(time.time() - start_time)
    api_requests_total.labels(
        endpoint=endpoint, method=method, status_code=status_code
    ).inc()


def _scalar(value: Any) -> Optional[float]:
    """Evidently returns a float for RMSE/R2Score but a {'mean','std'} dict for MAE/MAPE."""
    if isinstance(value, dict):
        value = value.get('mean')
    return float(value) if value is not None else None


def _extract_report_metrics(snapshot) -> dict:
    """Reads the regression and drift results out of an Evidently snapshot.

    Metrics are matched on config['type'] rather than on the display name, because
    the display name embeds the metric parameters and changes with them.
    """
    results = {'rmse': None, 'mae': None, 'r2score': None, 'mape': None,
               'drift_detected': 0, 'drift_share': None}

    for metric in snapshot.dict().get('metrics', []):
        kind = metric.get('config', {}).get('type', '').split(':')[-1]
        value = metric.get('value')

        if kind == 'RMSE':
            results['rmse'] = _scalar(value)
        elif kind == 'MAE':
            results['mae'] = _scalar(value)
        elif kind == 'R2Score':
            results['r2score'] = _scalar(value)
        elif kind == 'MAPE':
            results['mape'] = _scalar(value)
        elif kind == 'DriftedColumnsCount':
            # Dataset drift is declared when the share of drifted columns reaches
            # the preset threshold, not as soon as a single column drifts.
            threshold = metric.get('config', {}).get('drift_share', 0.5)
            share = (value or {}).get('share')
            results['drift_share'] = share
            results['drift_detected'] = int(share is not None and share >= threshold)

    return results


def _evaluate_batch(current_data: pd.DataFrame) -> dict:
    """Scores a batch, runs the Evidently report against January, updates the Gauges."""
    current = current_data.copy()

    for column in ALL_FEATS + [TARGET]:
        if column not in current.columns:
            raise HTTPException(status_code=400, detail=f"Missing required column: {column}")
        current[column] = pd.to_numeric(current[column], errors='coerce')

    current = current.dropna(subset=ALL_FEATS + [TARGET])
    if current.empty:
        raise HTTPException(status_code=400, detail="No usable rows after numeric coercion.")

    current[PREDICTION] = MODEL.predict(current[ALL_FEATS])

    report = Report(metrics=[RMSE(), MAE(), R2Score(), MAPE(), DataDriftPreset()])
    snapshot = report.run(
        current_data=Dataset.from_pandas(current, data_definition=DATA_DEFINITION),
        reference_data=REFERENCE_DATASET,
    )

    results = _extract_report_metrics(snapshot)
    results['evaluated_items'] = int(current.shape[0])

    if results['rmse'] is not None:
        model_rmse_score.set(results['rmse'])
    if results['mae'] is not None:
        model_mae_score.set(results['mae'])
    if results['r2score'] is not None:
        model_r2_score.set(results['r2score'])
    if results['mape'] is not None:
        model_mape_score.set(results['mape'])
    if results['drift_share'] is not None:
        evidently_drifted_columns_share.set(results['drift_share'])
    evidently_data_drift_detected_status.set(results['drift_detected'])

    return results


# --- Pydantic Models for API Input/Output ---
class BikeSharingInput(BaseModel):
    temp: float = Field(..., example=0.24)
    atemp: float = Field(..., example=0.2879)
    hum: float = Field(..., example=0.81)
    windspeed: float = Field(..., example=0.0)
    mnth: int = Field(..., example=1)
    hr: int = Field(..., example=0)
    weekday: int = Field(..., example=6)
    season: int = Field(..., example=1)
    holiday: int = Field(..., example=0)
    workingday: int = Field(..., example=0)
    weathersit: int = Field(..., example=1)
    dteday: datetime.date = Field(..., example="2011-01-01", description="Date of the record in YYYY-MM-DD format.")

class PredictionOutput(BaseModel):
    predicted_count: float = Field(..., example=16.0)

class EvaluationData(BaseModel):
    data: list[dict[str, Any]] = Field(..., description="List of data points, each containing features and the true target ('cnt').")
    evaluation_period_name: str = Field("unknown_period", description="Name of the period being evaluated (e.g., 'week1_february').")
    model_config = {'arbitrary_types_allowed': True}

class EvaluationReportOutput(BaseModel):
    message: str
    rmse: Optional[float]
    mape: Optional[float]
    mae: Optional[float]
    r2score: Optional[float]
    drift_detected: int
    evaluated_items: int

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """FastAPI rejects malformed bodies before the endpoint runs, so without this
    handler a 422 would never appear in api_requests_total and the API error-rate
    panel would be blind to invalid client payloads."""
    _record_request(request.url.path, request.method, "422", time.time())
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})


# --- API Endpoints ---
@app.get("/")
async def read_root():
    return {"message": "Welcome to the Bike Sharing Predictor API. Use /predict to get bike counts or /evaluate to run drift reports."}


@app.post("/predict", response_model=PredictionOutput)
async def predict(features: BikeSharingInput):
    """Predicts the hourly bike count (cnt) for a single record."""
    start_time = time.time()
    status_code = "200"

    try:
        row = pd.DataFrame([{name: getattr(features, name) for name in ALL_FEATS}])
        predicted_count = float(MODEL.predict(row)[0])
        return PredictionOutput(predicted_count=predicted_count)

    except HTTPException as e:
        status_code = str(e.status_code)
        raise
    except Exception as e:
        logger.error(f"Error during prediction: {e}")
        status_code = "500"
        raise HTTPException(status_code=500, detail=f"Prediction failed due to an internal error: {e}")
    finally:
        _record_request("/predict", "POST", status_code, start_time)


@app.post("/evaluate", response_model=EvaluationReportOutput)
async def evaluate(payload: EvaluationData):
    """Scores a batch of current data and runs an Evidently regression + drift report."""
    start_time = time.time()
    status_code = "200"

    try:
        if not payload.data:
            status_code = "400"
            raise HTTPException(status_code=400, detail="No items provided for evaluation.")

        results = _evaluate_batch(pd.DataFrame(payload.data))

        logger.info(
            f"Evaluated period '{payload.evaluation_period_name}' on {results['evaluated_items']} items: "
            f"RMSE={results['rmse']:.4f} MAE={results['mae']:.4f} R2={results['r2score']:.4f} "
            f"MAPE={results['mape']:.4f} drift_detected={results['drift_detected']}"
        )

        return EvaluationReportOutput(
            message=f"Evaluation completed for period '{payload.evaluation_period_name}'",
            rmse=results['rmse'],
            mape=results['mape'],
            mae=results['mae'],
            r2score=results['r2score'],
            drift_detected=results['drift_detected'],
            evaluated_items=results['evaluated_items'],
        )

    except HTTPException as e:
        status_code = str(e.status_code)
        raise
    except Exception as e:
        logger.error(f"Error during evaluation: {e}")
        status_code = "500"
        raise HTTPException(status_code=500, detail=f"Evaluation failed due to an internal error: {e}")
    finally:
        _record_request("/evaluate", "POST", status_code, start_time)


@app.post("/trigger-drift", response_model=EvaluationReportOutput)
async def trigger_drift():
    """Deliberately evaluates a corrupted batch so the drift and RMSE alerts fire.

    Used by `make fire-alert`. The batch is built from the reference period with the
    feature distributions pushed to their extremes and the target inflated, which
    drives the drifted-column share above the 0.5 preset threshold and the error
    metrics far above their alerting thresholds.
    """
    start_time = time.time()
    status_code = "200"

    try:
        drifted = REFERENCE_DATA.tail(168).copy()
        drifted['temp'] = 0.99
        drifted['atemp'] = 0.99
        drifted['hum'] = 0.02
        drifted['windspeed'] = 0.85
        drifted['weathersit'] = 3
        drifted['season'] = 4
        drifted['holiday'] = 1
        drifted['workingday'] = 0
        drifted[TARGET] = drifted[TARGET] * 10 + 1000

        results = _evaluate_batch(drifted)

        logger.warning(
            f"Drift deliberately triggered: RMSE={results['rmse']:.4f} "
            f"drift_detected={results['drift_detected']} share={results['drift_share']}"
        )

        return EvaluationReportOutput(
            message="Drift deliberately triggered on a corrupted batch",
            rmse=results['rmse'],
            mape=results['mape'],
            mae=results['mae'],
            r2score=results['r2score'],
            drift_detected=results['drift_detected'],
            evaluated_items=results['evaluated_items'],
        )

    except HTTPException as e:
        status_code = str(e.status_code)
        raise
    except Exception as e:
        logger.error(f"Error during drift trigger: {e}")
        status_code = "500"
        raise HTTPException(status_code=500, detail=f"Drift trigger failed: {e}")
    finally:
        _record_request("/trigger-drift", "POST", status_code, start_time)


@app.get("/health")
async def health():
    """Readiness probe used by the container healthcheck; not instrumented on purpose."""
    return {"status": "ok", "model_loaded": MODEL is not None}


@app.get("/metrics")
async def metrics(request: Request):
    """Exposes the Prometheus metrics of our own registry.

    Deliberately not instrumented: Prometheus scrapes it every 15s and those
    scrapes would otherwise dominate the API request-rate panels.
    """
    return Response(content=generate_latest(registry), media_type="text/plain")
