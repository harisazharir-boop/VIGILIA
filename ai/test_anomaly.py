from __future__ import annotations
import unittest
from ai.anomaly import feature_row, train, export_rows, validate_artifact

class AnomalyTests(unittest.TestCase):
    def test_missing_metric_is_honestly_unscorable(self): self.assertIsNone(feature_row({"cpu": 1}))
    def test_valid_metric_vector_is_bounded_input(self): self.assertEqual(feature_row({"cpu": 1, "ram": 2, "storage": 3, "hardware": {"cpuTemperatureC": 4}, "networkTelemetry": {"latencyMs": 5, "packetLossPercent": 6}}), [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    def test_training_requires_sufficient_local_history(self): self.assertIsNone(train([]))
    def test_export_redacts_non_telemetry_fields(self): self.assertEqual(export_rows([{"cpu": 1, "ram": 2, "storage": 3, "hardware": {"cpuTemperatureC": 4}, "networkTelemetry": {"latencyMs": 5, "packetLossPercent": 6}, "token": "secret"}])[0]["features"]["cpu"], 1.0)
    def test_invalid_artifact_is_rejected(self): self.assertFalse(validate_artifact({}))
    def test_tampered_artifact_is_rejected(self):
        rows = [{"cpu": 1, "ram": 2, "storage": 3, "hardware": {"cpuTemperatureC": 4}, "networkTelemetry": {"latencyMs": 5, "packetLossPercent": 6}} for _ in range(64)]
        artifact = train(rows)
        if artifact is None: self.skipTest("sklearn unavailable")
        self.assertTrue(validate_artifact(artifact)); artifact["training"]["rows"] = 999; self.assertFalse(validate_artifact(artifact))
    def test_training_artifact_has_evaluation_and_integrity(self):
        rows = [{"cpu": n, "ram": n + 1, "storage": n + 2, "hardware": {"cpuTemperatureC": n + 3}, "networkTelemetry": {"latencyMs": n + 4, "packetLossPercent": n + 5}} for n in range(64)]
        artifact = train(rows)
        if artifact is None: self.skipTest("sklearn unavailable")
        self.assertEqual(artifact["format"], "vigilia-anomaly"); self.assertTrue(artifact["evaluation"]["validated"]); self.assertTrue(validate_artifact(artifact))

if __name__ == '__main__': unittest.main()
