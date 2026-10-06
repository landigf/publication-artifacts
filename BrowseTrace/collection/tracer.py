"""HTTP trace capture for BrowserUse-backed and synthetic benchmark sessions."""
from __future__ import annotations

import os
import random
import sys
import time
import uuid
from urllib.parse import urlsplit

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "schema"))

from trace_schema import AccessMode, AgentType, TraceRequest, TraceSession
from privacy import safe_headers

try:
    from browser_use.browser.events import AgentFocusChangedEvent, TabCreatedEvent
except ImportError:
    AgentFocusChangedEvent = None
    TabCreatedEvent = None


class HTTPTracer:
    """Captures HTTP-level traces from agent execution."""

    def __init__(
        self,
        mode: str = "scraping",
        task_id: str = "",
        task_name: str = "",
        session_id: str | None = None,
    ):
        self.mode = AccessMode(mode)
        self.task_id = task_id
        self.task_name = task_name
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.requests: list[TraceRequest] = []
        self._start_time = int(time.time() * 1_000_000)

    def on_request(self, url: str, method: str = "GET", headers: dict | None = None):
        """Record an outgoing request. Call before on_response."""
        self._pending = {
            "url": url,
            "method": method,
            "headers": headers or {},
            "start_us": int(time.time() * 1_000_000),
        }

    def on_response(
        self,
        status: int = 200,
        content_type: str = "text/html",
        body_size: int = 0,
        headers: dict | None = None,
    ):
        """Record an incoming response."""
        pending = getattr(self, "_pending", None)
        if not pending:
            return

        now_us = int(time.time() * 1_000_000)
        latency_ms = (now_us - pending["start_us"]) / 1000

        req = TraceRequest(
            timestamp_us=pending["start_us"],
            url=pending["url"],
            method=pending["method"],
            status=status,
            request_headers=safe_headers(pending["headers"]),
            response_size_bytes=body_size,
            content_type=content_type,
            latency_ms=latency_ms,
            session_id=self.session_id,
            task_id=self.task_id,
            access_mode=self.mode,
            agent_type=AgentType.MULTI_STEP,
            cache_key=pending["url"].split("?")[0],
            object_size_bytes=body_size,
        )
        self.requests.append(req)
        self._pending = None

    def record(
        self,
        url: str,
        method: str = "GET",
        status: int = 200,
        content_type: str = "text/html",
        body_size: int = 0,
        latency_ms: float = 0,
        headers: dict | None = None,
    ):
        """Record a complete request-response pair in one call."""
        now_us = int(time.time() * 1_000_000)
        req = TraceRequest(
            timestamp_us=now_us,
            url=url,
            method=method,
            status=status,
            request_headers=safe_headers(headers),
            response_size_bytes=body_size,
            content_type=content_type,
            latency_ms=latency_ms,
            session_id=self.session_id,
            task_id=self.task_id,
            access_mode=self.mode,
            agent_type=AgentType.MULTI_STEP,
            cache_key=url.split("?")[0],
            object_size_bytes=body_size,
        )
        self.requests.append(req)

    def export(self) -> TraceSession:
        """Export captured trace as a TraceSession."""
        end_time = int(time.time() * 1_000_000)
        return TraceSession(
            session_id=self.session_id,
            task_id=self.task_id,
            task_name=self.task_name,
            agent_type=AgentType.MULTI_STEP,
            access_mode=self.mode,
            start_time_us=self._start_time,
            end_time_us=end_time,
            requests=self.requests,
        )


class BrowserUseNetworkTracer(HTTPTracer):
    """Capture real browser traffic from BrowserUse's CDP layer.

    The output matches the shared BrowseTrace trace schema so the same files can be
    replayed into cache simulation, dashboard analysis, and paper figures.
    """

    def __init__(
        self,
        mode: str = "scraping",
        task_id: str = "",
        task_name: str = "",
        session_id: str | None = None,
        agent_type: AgentType = AgentType.MULTI_STEP,
    ):
        super().__init__(mode=mode, task_id=task_id, task_name=task_name, session_id=session_id)
        self.agent_type = agent_type
        self._browser = None
        self._registered_targets: set[str] = set()
        self._pending_by_request: dict[str, dict] = {}

    async def attach(self, browser) -> None:
        """Attach the tracer to a BrowserUse BrowserSession."""
        self._browser = browser
        if getattr(browser, "event_bus", None) is not None and AgentFocusChangedEvent and TabCreatedEvent:
            browser.event_bus.on(AgentFocusChangedEvent, self._on_focus_changed)
            browser.event_bus.on(TabCreatedEvent, self._on_tab_created)
        if getattr(browser, "agent_focus_target_id", None):
            await self._register_target(browser.agent_focus_target_id)

    async def _on_focus_changed(self, event) -> None:
        await self._register_target(event.target_id)

    async def _on_tab_created(self, event) -> None:
        await self._register_target(event.target_id)

    async def _register_target(self, target_id: str) -> None:
        if self._browser is None or target_id in self._registered_targets:
            return

        cdp_session = await self._browser.get_or_create_cdp_session(target_id, focus=False)
        await cdp_session.cdp_client.send.Network.enable(session_id=cdp_session.session_id)

        cdp_session.cdp_client.register.Network.requestWillBeSent(self._on_request_will_be_sent)
        cdp_session.cdp_client.register.Network.responseReceived(self._on_response_received)
        cdp_session.cdp_client.register.Network.loadingFinished(self._on_loading_finished)
        cdp_session.cdp_client.register.Network.loadingFailed(self._on_loading_failed)

        self._registered_targets.add(target_id)

    def _on_request_will_be_sent(self, event, session_id=None) -> None:
        request = event.get("request", {})
        url = request.get("url", "")
        if not self._should_capture(url):
            return

        request_id = event.get("requestId")
        if not request_id:
            return

        headers = self._normalize_headers(request.get("headers", {}))
        wall_time = float(event.get("wallTime", time.time()))
        monotonic_start = float(event.get("timestamp", 0.0))

        # Capture initiator info (script, parser, fetch, etc.)
        initiator = event.get("initiator", {})

        # Track redirect count
        redirect_response = event.get("redirectResponse")
        existing = self._pending_by_request.get(request_id)
        redirect_count = (existing.get("redirect_count", 0) + 1) if existing and redirect_response else 0

        self._pending_by_request[request_id] = {
            "timestamp_us": int(wall_time * 1_000_000),
            "start_monotonic": monotonic_start,
            "url": url,
            "method": request.get("method", "GET"),
            "headers": headers,
            "status": 0,
            "content_type": "",
            "response_size_bytes": 0,
            "response_headers": {},
            "initiator_type": initiator.get("type", ""),
            "resource_type": event.get("type", ""),
            "redirect_count": redirect_count,
            # Timing and connection fields filled by _on_response_received
            "timing": {},
            "remote_ip": "",
            "protocol": "",
            "connection_reused": False,
        }

    def _on_response_received(self, event, session_id=None) -> None:
        request_id = event.get("requestId")
        pending = self._pending_by_request.get(request_id)
        if not pending:
            return

        response = event.get("response", {})
        pending["status"] = int(response.get("status", 0))
        pending["content_type"] = response.get("mimeType", "")
        header_size = int(response.get("encodedDataLength", 0) or 0)
        if header_size > pending["response_size_bytes"]:
            pending["response_size_bytes"] = header_size

        # Capture response headers (critical for cache analysis)
        resp_headers = self._normalize_headers(response.get("headers", {}))
        pending["response_headers"] = resp_headers

        # Capture CDP timing breakdown
        timing = response.get("timing", {})
        pending["timing"] = timing

        # Capture connection details
        pending["remote_ip"] = response.get("remoteIPAddress", "")
        pending["protocol"] = response.get("protocol", "")
        pending["connection_reused"] = bool(response.get("connectionReused", False))

        # Capture resource type from response
        if event.get("type"):
            pending["resource_type"] = event.get("type", "")

    def _on_loading_finished(self, event, session_id=None) -> None:
        request_id = event.get("requestId")
        pending = self._pending_by_request.pop(request_id, None)
        if not pending:
            return

        end_monotonic = float(event.get("timestamp", pending["start_monotonic"]))
        size_bytes = int(event.get("encodedDataLength", pending["response_size_bytes"]) or 0)
        self.requests.append(
            self._build_trace_request(
                pending=pending,
                latency_ms=max(0.0, (end_monotonic - pending["start_monotonic"]) * 1000),
                response_size_bytes=size_bytes,
                status=pending["status"] or 200,
                content_type=pending["content_type"],
            )
        )

    def _on_loading_failed(self, event, session_id=None) -> None:
        request_id = event.get("requestId")
        pending = self._pending_by_request.pop(request_id, None)
        if not pending:
            return

        end_monotonic = float(event.get("timestamp", pending["start_monotonic"]))
        self.requests.append(
            self._build_trace_request(
                pending=pending,
                latency_ms=max(0.0, (end_monotonic - pending["start_monotonic"]) * 1000),
                response_size_bytes=pending["response_size_bytes"],
                status=0,
                content_type=pending["content_type"] or "network/error",
            )
        )

    @staticmethod
    def _extract_timing(timing: dict) -> dict[str, float]:
        """Extract timing breakdown from CDP Network.Response.timing object."""
        if not timing:
            return {}
        def ms_delta(start_key: str, end_key: str) -> float:
            s, e = timing.get(start_key, -1), timing.get(end_key, -1)
            return max(0.0, e - s) if s >= 0 and e >= 0 else 0.0
        return {
            "dns_ms": ms_delta("dnsStart", "dnsEnd"),
            "tls_ms": ms_delta("sslStart", "sslEnd"),
            "tcp_ms": ms_delta("connectStart", "connectEnd"),
            "ttfb_ms": ms_delta("sendStart", "receiveHeadersEnd") if timing.get("sendStart", -1) >= 0 else 0.0,
            "transfer_ms": 0.0,  # filled in _on_loading_finished from total - ttfb
        }

    def _build_trace_request(
        self,
        pending: dict,
        latency_ms: float,
        response_size_bytes: int,
        status: int,
        content_type: str,
    ) -> TraceRequest:
        headers = pending["headers"]
        resp_headers = pending.get("response_headers", {})
        timing_breakdown = self._extract_timing(pending.get("timing", {}))

        # Compute transfer time = total latency - TTFB
        ttfb = timing_breakdown.get("ttfb_ms", 0.0)
        timing_breakdown["transfer_ms"] = max(0.0, latency_ms - ttfb) if ttfb > 0 else 0.0

        return TraceRequest(
            timestamp_us=pending["timestamp_us"],
            url=pending["url"],
            method=pending["method"],
            status=status,
            request_headers=safe_headers(headers),
            response_headers=safe_headers(resp_headers),
            response_size_bytes=response_size_bytes,
            content_type=content_type,
            latency_ms=latency_ms,
            session_id=self.session_id,
            task_id=self.task_id,
            access_mode=self.mode,
            agent_type=self.agent_type,
            agent_domain=self._parse_agent_domain(headers.get("agent-identity", "")),
            purpose=headers.get("agent-purpose", ""),
            cache_key=self._cache_key(pending["url"]),
            object_size_bytes=response_size_bytes,
            # Timing breakdown
            latency_dns_ms=timing_breakdown.get("dns_ms", 0.0),
            latency_tls_ms=timing_breakdown.get("tls_ms", 0.0),
            latency_tcp_ms=timing_breakdown.get("tcp_ms", 0.0),
            latency_ttfb_ms=timing_breakdown.get("ttfb_ms", 0.0),
            latency_transfer_ms=timing_breakdown.get("transfer_ms", 0.0),
            # Cache-relevant headers
            cache_control=resp_headers.get("cache-control", ""),
            cache_status=resp_headers.get("cf-cache-status", resp_headers.get("x-cache", resp_headers.get("cache-status", ""))),
            etag=resp_headers.get("etag", ""),
            age_seconds=int(resp_headers.get("age", "0") or "0"),
            # Request context
            initiator_type=pending.get("initiator_type", ""),
            resource_type=pending.get("resource_type", ""),
            redirect_count=pending.get("redirect_count", 0),
            # Connection details
            remote_ip=pending.get("remote_ip", ""),
            protocol=pending.get("protocol", ""),
            connection_reused=pending.get("connection_reused", False),
        )

    @staticmethod
    def _normalize_headers(headers: dict | None) -> dict[str, str]:
        return {str(k).lower(): str(v) for k, v in (headers or {}).items()}

    @staticmethod
    def _parse_agent_domain(agent_identity: str) -> str:
        if not agent_identity:
            return ""
        for part in agent_identity.split(";"):
            key, _, value = part.partition("=")
            if key.strip().lower() == "domain":
                return value.strip()
        return ""

    @staticmethod
    def _cache_key(url: str) -> str:
        parts = urlsplit(url)
        path = parts.path or "/"
        return f"{parts.scheme}://{parts.netloc}{path}" if parts.scheme and parts.netloc else path

    @staticmethod
    def _should_capture(url: str) -> bool:
        parts = urlsplit(url)
        return parts.scheme in {"http", "https"} and bool(parts.netloc)

    def export(self) -> TraceSession:
        """Export trace data, flushing any incomplete requests conservatively."""
        for request_id, pending in list(self._pending_by_request.items()):
            self.requests.append(
                self._build_trace_request(
                    pending=pending,
                    latency_ms=0.0,
                    response_size_bytes=pending["response_size_bytes"],
                    status=pending["status"],
                    content_type=pending["content_type"] or "incomplete",
                )
            )
            del self._pending_by_request[request_id]

        end_time = int(time.time() * 1_000_000)
        return TraceSession(
            session_id=self.session_id,
            task_id=self.task_id,
            task_name=self.task_name,
            agent_type=self.agent_type,
            access_mode=self.mode,
            start_time_us=self._start_time,
            end_time_us=end_time,
            requests=self.requests,
        )


class MockBrowserTracer(HTTPTracer):
    """Generates synthetic traces without BrowserUse.

    Simulates a multi-step agent browsing a website:
    - Scraping mode: fetches full HTML pages + CSS/JS/images
    - Machine lane: fetches structured JSON from API endpoints
    """

    def simulate_scraping_session(self, base_url: str, n_pages: int = 5):
        """Simulate scraping mode: full HTML + resources per page."""
        rng = random.Random(hash(self.session_id))

        for i in range(n_pages):
            page_id = rng.randint(1, 500)
            page_url = f"{base_url}/articles/{page_id}"

            # Main HTML page
            self.record(
                url=page_url,
                content_type="text/html",
                body_size=rng.randint(30000, 80000),
                latency_ms=rng.uniform(200, 800),
            )

            # CSS
            self.record(
                url=f"{base_url}/css/main.css",
                content_type="text/css",
                body_size=rng.randint(15000, 30000),
                latency_ms=rng.uniform(20, 100),
            )

            # JavaScript
            self.record(
                url=f"{base_url}/js/app.js",
                content_type="application/javascript",
                body_size=rng.randint(50000, 150000),
                latency_ms=rng.uniform(30, 150),
            )

            # Images (2-4 per page)
            for j in range(rng.randint(2, 4)):
                img_id = rng.randint(1, 200)
                self.record(
                    url=f"{base_url}/images/{img_id}.jpg",
                    content_type="image/jpeg",
                    body_size=rng.randint(50000, 500000),
                    latency_ms=rng.uniform(50, 300),
                )

            # Tracking/analytics
            self.record(
                url=f"{base_url}/analytics/track",
                content_type="application/json",
                body_size=rng.randint(100, 500),
                latency_ms=rng.uniform(50, 200),
            )

            time.sleep(0.01)  # Small delay between pages

    def simulate_authenticated_session(self, base_url: str, n_pages: int = 5):
        """Simulate authenticated (structured JSON) lane."""
        rng = random.Random(hash(self.session_id))

        # Discovery: fetch machine-access.json once
        self.record(
            url=f"{base_url}/.well-known/machine-access.json",
            content_type="application/json",
            body_size=rng.randint(500, 2000),
            latency_ms=rng.uniform(10, 50),
        )

        for i in range(n_pages):
            page_id = rng.randint(1, 500)

            # Structured JSON endpoint only
            self.record(
                url=f"{base_url}/api/v1/articles/{page_id}",
                content_type="application/json",
                body_size=rng.randint(2000, 8000),
                latency_ms=rng.uniform(10, 80),
            )

            time.sleep(0.01)
