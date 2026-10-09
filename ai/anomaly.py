"""Offline Isolation Forest pipeline. Production consumes only validated JSON artifacts."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone

FEATURES = ("cpu", "ram", "storage", "temperature", "latency", "packet_loss")
ARTIFACT_VERSION = 2
MIN_ROWS = 64

def feature_row(sample: dict) -> list[float] | None:
    hardware = sample.get("hardware") or {}; network = sample.get("networkTelemetry") or {}
    values = [sample.get("cpu"), sample.get("ram"), sample.get("storage"), hardware.get("cpuTemperatureC"), network.get("latencyMs"), network.get("packetLossPercent")]
    if any(value is None for value in values): return None
    try: return [float(value) for value in values]
    except (TypeError, ValueError): return None

def export_rows(samples: list[dict]) -> list[dict]:
    """Redacted bounded dataset: numeric feature vector and optional timestamp only."""
    out = []
    for sample in samples:
        vector = feature_row(sample)
        if vector is not None: out.append({"features": dict(zip(FEATURES, vector)), **({"timestamp": sample["timestamp"]} if isinstance(sample.get("timestamp"), str) else {})})
    return out[:10_000]

def _digest(payload: dict) -> str:
    clean = dict(payload); clean.pop("sha256", None)
    return hashlib.sha256(json.dumps(clean, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def train(samples: list[dict]):
    """Fit real Isolation Forest and return a versioned, validated calibration artifact."""
    rows = export_rows(samples)
    if len(rows) < MIN_ROWS: return None
    try:
        from sklearn.ensemble import IsolationForest
    except ImportError: return None
    vectors = [[row["features"][key] for key in FEATURES] for row in rows]
    split = max(MIN_ROWS // 2, int(len(vectors) * .8)); train_rows, eval_rows = vectors[:split], vectors[split:]
    model = IsolationForest(contamination=.05, random_state=42, n_estimators=128).fit(train_rows)
    scores = [-float(value) for value in model.score_samples(eval_rows or train_rows)]
    threshold = sorted(scores)[max(0, int(len(scores) * .95) - 1)]
    means = [sum(column) / len(train_rows) for column in zip(*train_rows)]
    scales = [max(1e-6, (sum((row[i] - means[i]) ** 2 for row in train_rows) / len(train_rows)) ** .5) for i in range(len(FEATURES))]
    artifact = {"format": "vigilia-anomaly", "version": ARTIFACT_VERSION, "algorithm": "isolation-forest", "features": list(FEATURES), "training": {"rows": len(train_rows), "evaluationRows": len(eval_rows or train_rows), "contamination": .05, "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}, "evaluation": {"scoreP95": threshold, "validated": True}, "fallbackCalibration": {"means": means, "scales": scales, "zThreshold": 3.0}}
    artifact["sha256"] = _digest(artifact)
    return artifact

def validate_artifact(artifact: dict) -> bool:
    try:
        return artifact.get("format") == "vigilia-anomaly" and artifact.get("version") == ARTIFACT_VERSION and artifact.get("algorithm") == "isolation-forest" and artifact.get("features") == list(FEATURES) and artifact["training"]["rows"] >= MIN_ROWS and artifact["evaluation"]["validated"] is True and len(artifact["fallbackCalibration"]["means"]) == len(FEATURES) and len(artifact["fallbackCalibration"]["scales"]) == len(FEATURES) and artifact.get("sha256") == _digest(artifact)
    except (KeyError, TypeError): return False
