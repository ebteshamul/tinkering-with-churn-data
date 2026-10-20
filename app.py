"""Flask API and web UI for the customer churn model.

Run:  python app.py     (after `python train.py --data <your csv>`)
"""
import io
import math
import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from flask import Flask, Response, jsonify, render_template, request

from churn_core import (CATEGORICAL, FEATURES, FIELDS, INT_FIELDS, LABELS,
                        NUMERIC, clean_frame)

BASE = Path(__file__).parent
MODEL_PATH = Path(os.environ.get("MODEL_PATH", BASE / "artifacts" / "churn_model.joblib"))
MAX_BATCH_ROWS = 10_000

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB upload limit


class ApiError(Exception):
    def __init__(self, message, status=400, details=None):
        super().__init__(message)
        self.message, self.status, self.details = message, status, details


class Predictor:
    """Wraps the saved pipeline: predictions plus per-field explanations."""

    def __init__(self, path):
        artifact = joblib.load(path)
        self.pipe = artifact["pipeline"]
        self.meta = artifact["meta"]
        self.prep = self.pipe.named_steps["prep"]
        self.coef = self.pipe.named_steps["model"].coef_[0]
        self.means = np.array(self.meta["feature_means"])
        self.fills = self.meta["fills"]

        # Matrix that sums encoded columns back into one value per field.
        self.field_names = list(dict.fromkeys(self.meta["feature_fields"]))
        index = {f: i for i, f in enumerate(self.field_names)}
        self.group = np.zeros((len(self.means), len(self.field_names)))
        for col, field in enumerate(self.meta["feature_fields"]):
            self.group[col, index[field]] = 1.0

    # -- scoring ---------------------------------------------------------
    def score(self, raw_df):
        """Return probabilities, per-field contributions and imputed mask."""
        cleaned = clean_frame(raw_df)
        imputed = cleaned.isna()
        filled = cleaned.fillna(value=self.fills)
        encoded = self.prep.transform(filled)
        probs = self.pipe.named_steps["model"].predict_proba(encoded)[:, 1]
        # How far each field pushes this customer's log-odds away from
        # the average training customer.
        contrib = ((encoded - self.means) * self.coef) @ self.group
        return probs, contrib, filled, imputed

    # -- validation ------------------------------------------------------
    def clean_payload(self, payload):
        """Validate one customer record; return (record, warnings)."""
        errors, warnings, record = [], [], {}
        for key in FEATURES:
            value = payload.get(key)
            if value is None or (isinstance(value, str) and not value.strip()):
                continue  # missing: the model fills it in
            label = LABELS[key]
            if key in NUMERIC:
                try:
                    number = float(str(value).strip())
                except ValueError:
                    errors.append(f"{label} must be a number.")
                    continue
                if not math.isfinite(number) or number < 0:
                    errors.append(f"{label} must be zero or greater.")
                    continue
                lo, hi = self.meta["ranges"][key]
                if number < lo - 1e-9 or number > hi + 1e-9:
                    warnings.append(
                        f"{label} of {number:g} is outside the range seen in "
                        f"training ({lo:g} to {hi:g}), so the estimate is less reliable."
                    )
                record[key] = number
            else:
                options = {o.lower(): o for o in self.meta["categories"][key]}
                match = options.get(str(value).strip().lower())
                if match is None:
                    errors.append(
                        f"{label} must be one of: {', '.join(self.meta['categories'][key])}."
                    )
                    continue
                record[key] = match
        if errors:
            raise ApiError("Some values are not valid.", 422, errors)
        return record, warnings

    # -- schema for the UI -----------------------------------------------
    def schema(self):
        fields = []
        for key, label, group, unit, hint in FIELDS:
            field = {"key": key, "label": label, "group": group, "unit": unit,
                     "default": self.fills[key]}
            if key in CATEGORICAL:
                field.update(type="category", options=self.meta["categories"][key])
            else:
                lo, hi = self.meta["ranges"][key]
                is_int = key in INT_FIELDS
                field.update(
                    type="number",
                    integer=is_int,
                    min=math.floor(lo) if is_int else round(lo, 1),
                    max=math.ceil(hi) if is_int else round(hi, 1),
                    default=round(self.fills[key]) if is_int else round(self.fills[key], 1),
                )
            fields.append(field)
        m = self.meta
        return {
            "fields": fields,
            "labels": LABELS,
            "metrics": m["metrics"],
            "threshold_table": m["threshold_table"],
            "influence": m["influence"],
            "n_train": m["n_train"],
            "churn_rate": m["churn_rate"],
            "default_threshold": 0.5,
        }


_predictor = None


def get_predictor():
    global _predictor
    if _predictor is None:
        if not MODEL_PATH.exists():
            raise ApiError(
                "The model file was not found. Run `python train.py --data <your csv>` "
                "and restart the app.", 503)
        _predictor = Predictor(MODEL_PATH)
    return _predictor


def parse_threshold(value):
    try:
        t = float(value)
    except (TypeError, ValueError):
        raise ApiError("threshold must be a number between 0 and 1.", 422)
    if not 0 < t < 1:
        raise ApiError("threshold must be between 0 and 1.", 422)
    return t


def python_value(v):
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    return v


# --------------------------------------------------------------------------
# Pages and API
# --------------------------------------------------------------------------
@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify(status="ok", model_loaded=MODEL_PATH.exists())


@app.get("/api/schema")
def schema():
    return jsonify(get_predictor().schema())


@app.post("/api/predict")
def predict():
    """Score one customer.

    Body (JSON): any of the 15 feature fields, plus optional `threshold`
    (default 0.5). Missing fields are filled with training-data defaults.
    """
    predictor = get_predictor()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError("Send a JSON object with the customer's fields.")
    threshold = parse_threshold(payload.get("threshold", 0.5))
    record, warnings = predictor.clean_payload(payload)

    probs, contrib, filled, imputed = predictor.score(pd.DataFrame([record]))
    p = float(probs[0])
    drivers = []
    for i, field in enumerate(predictor.field_names):
        drivers.append({
            "field": field,
            "label": LABELS[field],
            "value": python_value(filled.iloc[0][field]),
            "contribution": float(contrib[0, i]),
        })
    drivers.sort(key=lambda d: abs(d["contribution"]), reverse=True)

    return jsonify(
        probability=round(p, 4),
        threshold=threshold,
        prediction=int(p >= threshold),
        drivers=drivers,
        filled_in=[LABELS[c] for c in FEATURES if imputed.iloc[0][c]],
        warnings=warnings,
    )


@app.post("/api/predict/batch")
def predict_batch():
    """Score a CSV upload (form field `file`). Optional form field `threshold`."""
    predictor = get_predictor()
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        raise ApiError("Choose a CSV file to upload.")
    threshold = parse_threshold(request.form.get("threshold", 0.5))

    try:
        df = pd.read_csv(io.BytesIO(upload.read()))
    except Exception:
        raise ApiError("That file could not be read as a CSV.")
    if df.empty:
        raise ApiError("The CSV has no rows.")
    if len(df) > MAX_BATCH_ROWS:
        raise ApiError(f"The CSV has {len(df):,} rows. The limit is {MAX_BATCH_ROWS:,}.", 413)
    missing = [c for c in FEATURES if c not in df.columns]
    if missing:
        raise ApiError("The CSV is missing required columns.", 422,
                       [f"Missing: {', '.join(missing)}"])

    probs, contrib, filled, imputed = predictor.score(df)
    ids = df["CustomerID"].astype(str).tolist() if "CustomerID" in df.columns \
        else [str(i + 1) for i in range(len(df))]
    top = contrib.argmax(axis=1)  # field that pushes risk up the most

    rows = []
    for i in range(len(df)):
        rows.append({
            "id": ids[i],
            "probability": round(float(probs[i]), 4),
            "main_risk_factor": LABELS[predictor.field_names[top[i]]]
            if contrib[i, top[i]] > 0 else "",
            "plan": python_value(filled.iloc[i]["PlanType"]),
            "contract": python_value(filled.iloc[i]["ContractType"]),
            "tenure": python_value(round(filled.iloc[i]["TenureMonths"])),
            "monthly_charges": python_value(round(filled.iloc[i]["MonthlyCharges"], 2)),
        })

    return jsonify(
        threshold=threshold,
        n_rows=len(rows),
        rows_with_filled_values=int(imputed.any(axis=1).sum()),
        average_probability=round(float(probs.mean()), 4),
        rows=rows,
    )


@app.get("/api/template.csv")
def template_csv():
    """A one-row CSV showing the columns the batch upload expects."""
    predictor = get_predictor()
    row = {"CustomerID": "C0001"}
    for field in predictor.schema()["fields"]:
        row[field["key"]] = field["default"]
    buf = io.StringIO()
    pd.DataFrame([row]).to_csv(buf, index=False)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=churn_template.csv"})


# --------------------------------------------------------------------------
# Errors: JSON for the API, always
# --------------------------------------------------------------------------
@app.errorhandler(ApiError)
def handle_api_error(err):
    body = {"error": err.message}
    if err.details:
        body["details"] = err.details
    return jsonify(body), err.status


@app.errorhandler(404)
def not_found(_):
    if request.path.startswith("/api/"):
        return jsonify(error="No such endpoint."), 404
    return render_template("index.html"), 404


@app.errorhandler(413)
def too_large(_):
    return jsonify(error="That file is larger than the 5 MB limit."), 413


@app.errorhandler(500)
def server_error(_):
    return jsonify(error="Something went wrong on the server."), 500


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", port=int(os.environ.get("PORT", 5000)))
