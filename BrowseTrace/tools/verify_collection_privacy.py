#!/usr/bin/env python3
"""Optional credential-filter regression on real collection constructors.

Requires pydantic from collection/requirements.txt. No browser, API or network
is instantiated. The offline artifact verifier tests the shared filter without
requiring collection dependencies.
"""
from __future__ import annotations

import importlib.metadata
import json
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "collection"))
from privacy import CREDENTIAL_HEADERS
from tracer import BrowserUseNetworkTracer, HTTPTracer


def main() -> None:
    headers = {name.upper(): "synthetic-not-a-secret" for name in CREDENTIAL_HEADERS}
    headers["Content-Type"] = "text/plain"
    expected = {"Content-Type": "text/plain"}
    tracer = HTTPTracer()
    tracer.record("https://example.com/", headers=headers)
    assert tracer.export().requests[0].request_headers == expected
    tracer = HTTPTracer()
    tracer.on_request("https://example.com/", "GET", headers)
    tracer.on_response(200, "text/plain", 1, headers)
    assert tracer.export().requests[0].request_headers == expected
    browser_tracer = BrowserUseNetworkTracer()
    pending = {"url": "https://example.com/", "method": "GET", "headers": headers, "response_headers": headers, "timestamp_us": 1}
    request = browser_tracer._build_trace_request(pending=pending, latency_ms=0, response_size_bytes=1, status=200, content_type="text/plain")
    assert request.request_headers == request.response_headers == expected
    result = {"status": "passed", "environment": {"python": platform.python_version(), "pydantic": importlib.metadata.version("pydantic")}, "credential_field_names": sorted(CREDENTIAL_HEADERS), "checks": ["HTTPTracer.record request headers", "HTTPTracer request/response callback export", "BrowserUseNetworkTracer request and response constructor", "case insensitive header removal", "Content-Type retained"], "no_browser_or_model_execution": True}
    (ROOT / "reports/collection-privacy-verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print("Real collection constructors rejected all credential fixture fields.")


if __name__ == "__main__":
    main()
