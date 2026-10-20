"""Shared data cleaning and field definitions for the churn model.

Both train.py and app.py import this module, so a customer record is cleaned
in exactly the same way at training time and at prediction time. This mirrors
the cleaning steps in the original notebook.
"""
import numpy as np
import pandas as pd

TARGET = "Churn"

NUMERIC = [
    "Age", "TenureMonths", "MonthlyCharges", "SupportTickets",
    "AvgMonthlyUsageHours", "SatisfactionScore", "LastLoginDays",
    "ReferralCount", "DeviceCount",
]
INT_FIELDS = {
    "Age", "TenureMonths", "SupportTickets", "SatisfactionScore",
    "LastLoginDays", "ReferralCount", "DeviceCount",
}
NOMINAL = ["Gender", "Region", "ContractType", "PaymentMethod", "AutoPay"]
ORDINAL = ["PlanType"]
PLAN_ORDER = ["Basic", "Standard", "Premium"]
CATEGORICAL = NOMINAL + ORDINAL

# Same imputation choices as the notebook: mean for most numerics,
# median for skewed ones, mode for categoricals.
MEDIAN_FILL = ["MonthlyCharges", "LastLoginDays"]
MEAN_FILL = [c for c in NUMERIC if c not in MEDIAN_FILL]

# Values in categorical columns that are junk, not real categories.
GARBAGE = {"1", "2", "3", "value", "unknown", "n/a", ""}

# Fields in the order the form shows them, with display metadata.
FIELDS = [
    # key, label, group, unit, hint
    ("Age", "Age", "Customer", "years", ""),
    ("Gender", "Gender", "Customer", "", ""),
    ("Region", "Region", "Customer", "", ""),
    ("PlanType", "Plan", "Plan and billing", "", ""),
    ("ContractType", "Contract", "Plan and billing", "", ""),
    ("MonthlyCharges", "Monthly charges", "Plan and billing", "", ""),
    ("PaymentMethod", "Payment method", "Plan and billing", "", ""),
    ("AutoPay", "Auto-pay", "Plan and billing", "", ""),
    ("TenureMonths", "Tenure", "Plan and billing", "months", ""),
    ("AvgMonthlyUsageHours", "Monthly usage", "Engagement", "hours", ""),
    ("LastLoginDays", "Last login", "Engagement", "days ago", ""),
    ("DeviceCount", "Devices", "Engagement", "", ""),
    ("SupportTickets", "Support tickets", "Engagement", "", ""),
    ("SatisfactionScore", "Satisfaction score", "Engagement", "", ""),
    ("ReferralCount", "Referrals", "Engagement", "", ""),
]
FEATURES = [f[0] for f in FIELDS]
LABELS = {f[0]: f[1] for f in FIELDS}


def _strip(value):
    """The notebook's `x.strip('__')`, plus stray whitespace."""
    return value.strip("_").strip() if isinstance(value, str) else value


def clean_frame(df, fills=None):
    """Return a cleaned copy of `df` containing only the model's features.

    - strips the '__' wrappers around values (e.g. '__35__' -> '35')
    - converts numeric columns to numbers (bad values become NaN)
    - turns junk category values into NaN
    - if `fills` is given, fills remaining NaNs with those values
    """
    out = df.copy()
    for col in FEATURES:
        if col not in out.columns:
            out[col] = np.nan
    out = out[FEATURES].copy().astype(object).map(_strip)

    for col in NUMERIC:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    for col in CATEGORICAL:
        out[col] = out[col].map(
            lambda v: np.nan
            if isinstance(v, str) and v.strip().lower() in GARBAGE
            else v
        )

    if fills:
        out = out.fillna(value=fills)
    return out


def compute_fills(clean_train):
    """Imputation values learned from the (cleaned) training data."""
    fills = {}
    for col in MEAN_FILL:
        fills[col] = float(clean_train[col].mean())
    for col in MEDIAN_FILL:
        fills[col] = float(clean_train[col].median())
    for col in CATEGORICAL:
        fills[col] = clean_train[col].mode().iloc[0]
    return fills


def field_for_feature(encoded_name):
    """Map an encoded column name (e.g. 'nominal__Region_East') to its field."""
    name = encoded_name.split("__", 1)[-1]
    for col in NOMINAL:
        if name.startswith(col + "_"):
            return col
    return name
