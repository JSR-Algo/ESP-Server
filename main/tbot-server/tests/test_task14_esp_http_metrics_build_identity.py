from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_esp_runtime_metrics_bind_only_redacted_approval_status():
    source = (ROOT / "core/http_server.py").read_text(encoding="utf-8")
    assert "approved_identities_from_config(self.config)" in source
    assert "esp_build_identity_metrics_fields(headers, approved_identities)" in source
    assert 'device["buildIdentity"]' not in source


def test_esp_connection_preserves_raw_header_cardinality():
    source = (ROOT / "core/connection.py").read_text(encoding="utf-8")
    assert "preserve_request_headers(ws.request.headers)" in source
    assert 'single_header(self.headers, "client-id")' in source
    assert 'single_header(self.headers, "device-id")' in source
    assert "self.headers = dict(ws.request.headers)" not in source
