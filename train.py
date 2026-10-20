"""Train the churn model and save it for the Flask app.

Usage:
    python train.py --data customer_churn_exam_dataset_3500.csv

Reproduces the notebook: same cleaning, same encoders, LogisticRegression,
70/30 split with random_state=30. It also saves what the app needs to show
(categories, value ranges, test metrics) alongside the fitted pipeline.
"""
import argparse
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (accuracy_score, classification_report,
                             confusion_matrix, precision_score, recall_score,
                             roc_auc_score)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder

from churn_core import (CATEGORICAL, FEATURES, NOMINAL, NUMERIC, ORDINAL,
                        PLAN_ORDER, TARGET, clean_frame, compute_fills,
                        field_for_feature)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="customer_churn_exam_dataset_3500.csv")
    ap.add_argument("--out", default="artifacts/churn_model.joblib")
    args = ap.parse_args()

    raw = pd.read_csv(args.data)
    y = raw[TARGET].astype(int)
    X = clean_frame(raw.drop(columns=[TARGET]))

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.3, random_state=30
    )

    # Learn fill values from the training split only.
    fills = compute_fills(X_train)
    X_train = X_train.fillna(value=fills)
    X_test = X_test.fillna(value=fills)

    preprocessor = ColumnTransformer(
        transformers=[
            ("nominal",
             OneHotEncoder(drop="first", sparse_output=False,
                           handle_unknown="ignore"),
             NOMINAL),
            ("ordinal",
             OrdinalEncoder(categories=[PLAN_ORDER],
                            handle_unknown="use_encoded_value",
                            unknown_value=-1),
             ORDINAL),
        ],
        remainder="passthrough",
    )
    pipe = Pipeline([
        ("prep", preprocessor),
        ("model", LogisticRegression(max_iter=1000)),
    ])
    pipe.fit(X_train, y_train)

    y_pred = pipe.predict(X_test)
    y_prob = pipe.predict_proba(X_test)[:, 1]
    report = classification_report(y_test, y_pred, output_dict=True,
                                   zero_division=0)
    tn, fp, fn, tp = confusion_matrix(y_test, y_pred).ravel()

    metrics = {
        "accuracy": accuracy_score(y_test, y_pred),
        "precision": report["1"]["precision"],
        "recall": report["1"]["recall"],
        "f1": report["1"]["f1-score"],
        "roc_auc": roc_auc_score(y_test, y_prob),
        "confusion": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        "n_test": int(len(y_test)),
    }

    # What the model would catch / get right at each flag threshold.
    threshold_table = []
    for t in np.round(np.arange(0.05, 0.951, 0.05), 2):
        flagged = y_prob >= t
        threshold_table.append({
            "threshold": float(t),
            "precision": float(precision_score(y_test, flagged, zero_division=0)),
            "recall": float(recall_score(y_test, flagged, zero_division=0)),
            "flagged_share": float(flagged.mean()),
        })

    # Options for the form: categories seen often enough to be real.
    min_count = max(5, int(0.005 * len(X_train)))
    categories = {}
    for col in NOMINAL:
        counts = X_train[col].value_counts()
        categories[col] = sorted(counts[counts >= min_count].index.tolist())
    plan_counts = X_train["PlanType"].value_counts()
    categories["PlanType"] = [p for p in PLAN_ORDER if plan_counts.get(p, 0) > 0]

    ranges = {
        col: [float(X_train[col].quantile(0.005)), float(X_train[col].quantile(0.995))]
        for col in NUMERIC
    }

    # Per-column facts used to explain individual predictions.
    X_enc = pipe.named_steps["prep"].transform(X_train)
    names = pipe.named_steps["prep"].get_feature_names_out().tolist()
    fields = [field_for_feature(n) for n in names]
    coef = pipe.named_steps["model"].coef_[0]
    influence = {}
    for f, c, s in zip(fields, coef, X_enc.std(axis=0)):
        influence[f] = influence.get(f, 0.0) + abs(float(c) * float(s))
    total = sum(influence.values()) or 1.0
    influence = {k: v / total for k, v in influence.items()}

    meta = {
        "fills": fills,
        "categories": categories,
        "ranges": ranges,
        "metrics": metrics,
        "threshold_table": threshold_table,
        "influence": influence,
        "feature_means": X_enc.mean(axis=0).tolist(),
        "feature_fields": fields,
        "n_train": int(len(y_train)),
        "churn_rate": float(y.mean()),
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"pipeline": pipe, "meta": meta}, out)

    print(f"Rows: {len(raw)} (train {len(y_train)}, test {len(y_test)})")
    print(f"Accuracy:  {metrics['accuracy']:.4f}")
    print(f"Churn precision / recall: {metrics['precision']:.2f} / {metrics['recall']:.2f}")
    print(f"ROC AUC:   {metrics['roc_auc']:.3f}")
    print(f"Saved model to {out}")


if __name__ == "__main__":
    main()
