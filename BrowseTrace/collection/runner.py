#!/usr/bin/env python3
"""Benchmark runner for controlled and live BrowserUse sessions.

Modes:
- `controlled`: real browser sessions against the local test publisher
- `live`: real BrowserUse agent runs against public target URLs
- `mock`: synthetic fallback for development without BrowserUse/LLM credentials
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import random
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "schema"))
from trace_schema import TraceFile, TraceSession

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tracer import BrowserUseNetworkTracer, MockBrowserTracer
from trace_schema import AgentType

try:
    from browser_use import Agent, Browser, ChatAnthropic, ChatBrowserUse, ChatOpenAI
except ImportError:
    Agent = None
    Browser = None
    ChatAnthropic = None
    ChatBrowserUse = None
    ChatOpenAI = None


DEFAULT_TEST_PUBLISHER_URL = os.getenv("BROWSETRACE_TEST_PUBLISHER_URL", "http://localhost:9001")

# Linux/cloud headless Chromium flags (required for GCP VMs, Docker, CI)
_LINUX_BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--disable-extensions",
    "--no-first-run",
] if platform.system() == "Linux" else []
_LINUX_SANDBOX = False if platform.system() == "Linux" else None


def _safe_getattr(obj, attr, default="unknown"):
    """getattr that catches property errors (e.g. ChatGoogleGenerativeAI.provider)."""
    try:
        return getattr(obj, attr, default)
    except Exception:
        return default
DEFAULT_BROWSERUSE_MODEL = os.getenv("BROWSETRACE_BROWSERUSE_MODEL", "gpt-4.1-mini")


class AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        for key, value in attrs:
            if key.lower() == "href" and value:
                self.hrefs.append(value)


def load_tasks(tasks_file: str = "tasks.yaml") -> dict:
    with open(tasks_file) as f:
        data = yaml.safe_load(f)
    return {t["id"]: t for t in data["tasks"]}


def estimate_pages(task: dict) -> int:
    est = task.get("estimated_requests", "10-20")
    if isinstance(est, str) and "-" in est:
        low, _high = est.split("-", 1)
        return max(3, int(low) // 5)
    if isinstance(est, int):
        return max(3, est // 5)
    return 5


def controlled_article_ids(task_id: str, n_pages: int, population: int = 50) -> list[int]:
    seed = int(hashlib.sha256(task_id.encode("utf-8")).hexdigest(), 16)
    rng = random.Random(seed)
    return rng.sample(range(1, population + 1), k=min(n_pages, population))


def build_live_task_prompt(task: dict) -> str:
    targets = "\n".join(f"- {url}" for url in task.get("target_urls", []))
    return f"""You are executing a benchmark workload for agent-to-web measurement.

Task: {task['name']}
Description: {task['description']}
Category: {task.get('category', 'unknown')}
Access pattern: {task.get('access_pattern', 'unknown')}

Start from these URLs when relevant:
{targets}

Rules:
- Stay within the target publishers unless a redirect is required.
- Do not log in, create accounts, or submit payments.
- Prefer navigation, reading, and comparison over broad wandering.
- Stop once you can produce a short answer to the task.
- End with a concise summary of what you found.
    """


def has_browseruse_llm_credentials() -> bool:
    return any(os.getenv(key) for key in ("BROWSER_USE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"))


def resolve_browseruse_llm():
    if ChatBrowserUse is not None and os.getenv("BROWSER_USE_API_KEY"):
        return ChatBrowserUse()
    if ChatOpenAI is not None and os.getenv("OPENAI_API_KEY"):
        return ChatOpenAI(model=DEFAULT_BROWSERUSE_MODEL)
    if ChatAnthropic is not None and os.getenv("ANTHROPIC_API_KEY"):
        return ChatAnthropic(model=os.getenv("BROWSETRACE_BROWSERUSE_ANTHROPIC_MODEL", "claude-3-5-sonnet-latest"))
    if os.getenv("GOOGLE_API_KEY"):
        # Use Gemini via OpenAI-compatible endpoint (BrowserUse-compatible)
        # Monkey-patch to strip frequency_penalty/presence_penalty which Gemini rejects
        if ChatOpenAI is not None:
            import openai as _openai

            # Patch BOTH sync and async create methods
            _orig_create = _openai.resources.chat.completions.Completions.create
            def _patched_create(self, **kwargs):
                kwargs.pop("frequency_penalty", None)
                kwargs.pop("presence_penalty", None)
                return _orig_create(self, **kwargs)
            _openai.resources.chat.completions.Completions.create = _patched_create

            _orig_async = _openai.resources.chat.completions.AsyncCompletions.create
            async def _patched_async(self, **kwargs):
                kwargs.pop("frequency_penalty", None)
                kwargs.pop("presence_penalty", None)
                return await _orig_async(self, **kwargs)
            _openai.resources.chat.completions.AsyncCompletions.create = _patched_async

            return ChatOpenAI(
                model=os.getenv("BROWSETRACE_BROWSERUSE_GEMINI_MODEL", "gemini-2.5-flash"),
                api_key=os.getenv("GOOGLE_API_KEY"),
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            )
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=os.getenv("BROWSETRACE_BROWSERUSE_GEMINI_MODEL", "gemini-2.5-flash"))
    raise RuntimeError(
        "No BrowserUse-compatible LLM credentials found. Set BROWSER_USE_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, or GOOGLE_API_KEY."
    )


def normalize_navigation_url(raw_url: str, base_url: str) -> str | None:
    absolute = urljoin(base_url, raw_url)
    parts = urlsplit(absolute)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    if any(parts.path.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".svg", ".pdf", ".zip", ".mp4")):
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))


def discover_same_site_links(seed_url: str, limit: int = 4) -> list[str]:
    request = Request(
        seed_url,
        headers={
            "User-Agent": "BrowseTrace/1.0 (benchmark)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urlopen(request, timeout=12) as response:
            content_type = response.headers.get("Content-Type", "")
            if "html" not in content_type.lower():
                return []
            body = response.read(512_000).decode("utf-8", errors="ignore")
    except Exception:
        return []

    parser = AnchorParser()
    parser.feed(body)
    same_host = urlsplit(seed_url).netloc
    links: list[str] = []
    seen: set[str] = set()

    for href in parser.hrefs:
        normalized = normalize_navigation_url(href, seed_url)
        if not normalized:
            continue
        if urlsplit(normalized).netloc != same_host:
            continue
        if normalized in seen or normalized == seed_url:
            continue
        seen.add(normalized)
        links.append(normalized)
        if len(links) >= limit:
            break

    return links


def build_depth_chain(
    seed_url: str,
    budget: int,
    *,
    rng: random.Random | None = None,
    randomize: bool = False,
) -> list[str]:
    chain: list[str] = []
    current = seed_url
    visited: set[str] = set()

    while current and len(chain) < budget:
        if current in visited:
            break
        chain.append(current)
        visited.add(current)
        next_links = [link for link in discover_same_site_links(current, limit=6) if link not in visited]
        if randomize and rng is not None:
            rng.shuffle(next_links)
        current = next_links[0] if next_links else None

    return chain


def build_scripted_navigation_plan(
    task: dict,
    max_steps: int,
    *,
    randomize: bool = False,
    seed: int | None = None,
) -> list[str]:
    roots = [url for url in task.get("target_urls", []) if urlsplit(url).scheme in {"http", "https"}]
    pattern = task.get("access_pattern", "")
    plan: list[str] = []
    seen: set[str] = set()
    rng = random.Random(seed if seed is not None else int(hashlib.sha256(task["id"].encode("utf-8")).hexdigest(), 16))

    def add(url: str):
        if url and url not in seen and len(plan) < max_steps:
            seen.add(url)
            plan.append(url)

    if randomize and len(roots) > 1:
        rng.shuffle(roots)

    if pattern.startswith("depth-first") and roots and "multi-site" not in pattern:
        for url in build_depth_chain(roots[0], max_steps, rng=rng, randomize=randomize):
            add(url)
        for root in roots[1:]:
            add(root)
    else:
        child_limit = 1 if any(token in pattern for token in ("multi-site", "parallel", "verification")) else 2
        for root in roots:
            add(root)
            children = discover_same_site_links(root, limit=6 if randomize else child_limit)
            if randomize:
                rng.shuffle(children)
            for child in children[:child_limit]:
                add(child)
            if len(plan) >= max_steps:
                break

    return plan[:max_steps]


def run_mock_task(task: dict, mode: str, run_id: int) -> TraceSession:
    """Run a single task in synthetic mode."""
    base_url = DEFAULT_TEST_PUBLISHER_URL
    n_pages = estimate_pages(task)
    session_id = f"{task['id']}_{mode}_{run_id}"

    tracer = MockBrowserTracer(
        mode=mode,
        task_id=task["id"],
        task_name=task["name"],
        session_id=session_id,
    )

    if mode == "scraping":
        tracer.simulate_scraping_session(base_url, n_pages=n_pages)
    else:
        tracer.simulate_authenticated_session(base_url, n_pages=n_pages)

    session = tracer.export()
    session.metadata.update(
        {
            "backend": "mock",
            "surface": "controlled",
            "benchmark_note": "Synthetic fallback path",
        }
    )
    return session


async def run_browseruse_controlled_task(task: dict, mode: str, run_id: int, publisher_base_url: str) -> TraceSession:
    """Run a real browser session against the controlled test publisher."""
    if Browser is None:
        raise RuntimeError("browser-use is not installed. Use --backend mock or install browser-use.")

    session_id = f"{task['id']}_{mode}_{run_id}"
    tracer = BrowserUseNetworkTracer(
        mode=mode,
        task_id=task["id"],
        task_name=task["name"],
        session_id=session_id,
    )
    browser = Browser(
        headless=True,
        is_local=True,
        allowed_domains=[urlsplit(publisher_base_url).netloc],
        user_agent="BrowseTrace/1.0 (benchmark)",
        args=_LINUX_BROWSER_ARGS or None,
        chromium_sandbox=_LINUX_SANDBOX,
    )

    await browser.start()
    try:
        await tracer.attach(browser)
        n_pages = estimate_pages(task)
        article_ids = controlled_article_ids(task["id"], n_pages)

        if mode == "authenticated":
            raise NotImplementedError(
                "The authenticated lane requires a credential client that is not "
                "shipped in the public benchmark mirror. The released BrowseTrace "
                "corpus is collected exclusively in scraping mode."
            )
        await browser.set_extra_headers({})
        await browser.navigate_to(f"{publisher_base_url}/")
        await asyncio.sleep(0.5)
        for article_id in article_ids:
            await browser.navigate_to(f"{publisher_base_url}/articles/{article_id}")
            await asyncio.sleep(0.75)

        session = tracer.export()
        session.metadata.update(
            {
                "backend": "browseruse",
                "surface": "controlled",
                "publisher_base_url": publisher_base_url,
                "article_ids": article_ids,
                "task_access_pattern": task.get("access_pattern", ""),
                "llm_mode": "scripted-browser-session",
            }
        )
        return session
    finally:
        await browser.stop()


async def run_browseruse_live_task(task: dict, run_id: int, max_steps: int) -> TraceSession:
    """Run a real BrowserUse agent against public target URLs."""
    if Browser is None or Agent is None:
        raise RuntimeError("browser-use is not installed. Use --backend mock or install browser-use.")

    domains = sorted({urlsplit(url).netloc for url in task.get("target_urls", []) if urlsplit(url).netloc})
    browser = Browser(
        headless=True,
        is_local=True,
        allowed_domains=domains or None,
        user_agent="BrowseTrace/1.0 (benchmark)",
        args=_LINUX_BROWSER_ARGS or None,
        chromium_sandbox=_LINUX_SANDBOX,
    )
    session_id = f"{task['id']}_scraping_{run_id}"
    tracer = BrowserUseNetworkTracer(
        mode="scraping",
        task_id=task["id"],
        task_name=task["name"],
        session_id=session_id,
    )

    await browser.start()
    try:
        await tracer.attach(browser)
        llm = resolve_browseruse_llm()
        agent = Agent(
            task=build_live_task_prompt(task),
            llm=llm,
            browser=browser,
            use_vision=False,
            max_actions_per_step=4,
            max_failures=10,
            step_timeout=60,
            directly_open_url=True,
            source="browsetrace-benchmark",
        )
        history = await agent.run(max_steps=max_steps)
        session = tracer.export()
        session.metadata.update(
            {
                "backend": "browseruse",
                "surface": "live",
                "target_urls": task.get("target_urls", []),
                "target_domains": domains,
                "max_steps": max_steps,
                "agent_completed": history.is_done() if hasattr(history, "is_done") else None,
                "agent_success": history.is_successful() if hasattr(history, "is_successful") else None,
                "llm_provider": _safe_getattr(llm, "provider", "unknown"),
                "llm_model": _safe_getattr(llm, "name", _safe_getattr(llm, "model", "unknown")),
            }
        )
        return session
    finally:
        await browser.stop()


async def run_browseruse_live_scripted_task(
    task: dict,
    run_id: int,
    max_steps: int,
    *,
    randomize: bool = False,
) -> TraceSession:
    """Run a credential-free real BrowserUse browser session against public target URLs."""
    if Browser is None:
        raise RuntimeError("browser-use is not installed. Use --backend mock or install browser-use.")

    navigation_plan = build_scripted_navigation_plan(
        task,
        max_steps=max_steps,
        randomize=randomize,
        seed=run_id,
    )
    if not navigation_plan:
        raise RuntimeError(f"No live navigation plan could be built for task {task['id']}.")

    domains = sorted({urlsplit(url).netloc for url in navigation_plan if urlsplit(url).netloc})
    browser = Browser(
        headless=True,
        is_local=True,
        allowed_domains=domains or None,
        user_agent="BrowseTrace/1.0 (benchmark)",
        args=_LINUX_BROWSER_ARGS or None,
        chromium_sandbox=_LINUX_SANDBOX,
    )
    session_id = f"{task['id']}_scraping_{run_id}"
    tracer = BrowserUseNetworkTracer(
        mode="scraping",
        task_id=task["id"],
        task_name=task["name"],
        session_id=session_id,
        agent_type=AgentType.CRAWLER,
    )

    await browser.start()
    try:
        await tracer.attach(browser)
        await browser.set_extra_headers({})
        navigation_errors: list[dict[str, str]] = []
        for url in navigation_plan:
            last_error: Exception | None = None
            for _attempt in range(2):
                try:
                    await browser.navigate_to(url)
                    last_error = None
                    break
                except Exception as exc:  # BrowserUse can raise on transient navigation failures.
                    last_error = exc
                    await asyncio.sleep(1.0)
            if last_error is not None:
                navigation_errors.append({"url": url, "error": str(last_error)})
            await asyncio.sleep(1.0)

        session = tracer.export()
        session.metadata.update(
            {
                "backend": "browseruse",
                "surface": "live",
                "live_driver": "scripted-random" if randomize else "scripted",
                "target_urls": task.get("target_urls", []),
                "target_domains": domains,
                "max_steps": max_steps,
                "navigation_plan": navigation_plan,
                "navigation_errors": navigation_errors,
                "collection_note": (
                    "Credential-free BrowserUse browser baseline with randomized same-site exploration"
                    if randomize
                    else "Credential-free BrowserUse browser baseline over live public targets"
                ),
            }
        )
        return session
    finally:
        await browser.stop()


def run_benchmark(
    task_id: str,
    mode: str,
    repeats: int,
    output_dir: str,
    backend: str,
    surface: str,
    publisher_base_url: str,
    max_steps: int,
    live_driver: str,
):
    """Run benchmark tasks and save traces."""
    tasks = load_tasks()

    if task_id == "all":
        task_list = list(tasks.values())
    else:
        requested_ids = [part.strip() for part in task_id.split(",") if part.strip()]
        missing = [task_name for task_name in requested_ids if task_name not in tasks]
        if missing:
            print(f"  Unknown task(s): {', '.join(missing)}")
            print(f"  Available: {', '.join(tasks.keys())}")
            return
        task_list = [tasks[name] for name in requested_ids]

    if surface == "live" and mode != "scraping":
        raise RuntimeError("Live BrowserUse runs currently support scraping mode only.")

    modes = ["scraping", "authenticated"] if mode == "both" else [mode]
    out = Path(output_dir)

    for m in modes:
        for task in task_list:
            sessions = []
            failed_runs = []
            print(f"  Running: {task['id']} ({m}, {backend}, {surface}) x{repeats}...", end=" ", flush=True)
            t0 = time.time()

            for r in range(repeats):
                try:
                    if backend == "mock":
                        session = run_mock_task(task, m, r)
                    elif surface == "controlled":
                        session = asyncio.run(run_browseruse_controlled_task(task, m, r, publisher_base_url))
                    elif live_driver == "agent" or (live_driver == "auto" and has_browseruse_llm_credentials()):
                        session = asyncio.run(run_browseruse_live_task(task, r, max_steps))
                    elif live_driver == "scripted-random":
                        session = asyncio.run(run_browseruse_live_scripted_task(task, r, max_steps, randomize=True))
                    else:
                        session = asyncio.run(run_browseruse_live_scripted_task(task, r, max_steps))
                    sessions.append(session)
                except Exception as exc:
                    failed_runs.append({"run_id": r, "error": str(exc)})

            if not sessions:
                print(f"\n⚠️  WARNING: No successful sessions for task {task['id']} ({m}). "
                      f"Skipping to next task. ({len(failed_runs)} failures)")
                continue

            # Inject collection region metadata from environment
            region_meta = {
                "collection_region": os.getenv("COLLECTION_REGION", "local"),
                "collection_zone": os.getenv("COLLECTION_ZONE", "unknown"),
                "collection_provider": os.getenv("COLLECTION_PROVIDER", "local"),
                "collection_vm_type": os.getenv("COLLECTION_VM_TYPE", "unknown"),
            }
            for session in sessions:
                session.metadata.update(region_meta)

            trace = TraceFile(
                generator=f"benchmark-runner-{backend}-{surface}-{m}-{live_driver}",
                sessions=sessions,
            )
            task_dir = out / m / task["id"]
            task_dir.mkdir(parents=True, exist_ok=True)
            trace.save(task_dir / "traces.json")
            trace.to_cache_sim_csv(task_dir / "cache_trace.csv")
            trace.to_access_log_jsonl(task_dir / "access_log.jsonl")

            total_reqs = sum(s.total_requests for s in sessions)
            total_bytes = sum(s.total_bytes for s in sessions)
            avg_reqs = total_reqs / len(sessions)
            avg_bytes = total_bytes / len(sessions)

            summary = {
                "task_id": task["id"],
                "task_name": task["name"],
                "mode": m,
                "backend": backend,
                "surface": surface,
                "live_driver": live_driver if surface == "live" else None,
                "repeats": len(sessions),
                "failed_runs": failed_runs,
                "total_requests": trace.total_requests,
                "avg_requests_per_run": total_reqs / len(sessions) if sessions else 0,
                "avg_bytes_per_run": total_bytes / len(sessions) if sessions else 0,
                "sessions": [
                    {
                        "session_id": s.session_id,
                        "total_requests": s.total_requests,
                        "total_bytes": s.total_bytes,
                        "unique_urls": s.unique_urls,
                        "duration_ms": s.duration_ms,
                        "metadata": s.metadata,
                    }
                    for s in sessions
                ],
            }
            (task_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
            elapsed = time.time() - t0
            print(f"{avg_reqs:.0f} req/run, {avg_bytes/1024:.0f} KB/run ({elapsed:.1f}s)")

    print(f"\n  Traces saved to {output_dir}")


def main():
    parser = argparse.ArgumentParser(description="BrowseTrace Benchmark Runner")
    parser.add_argument("--task", default="all", help="Task ID or 'all'")
    parser.add_argument("--mode", default="both", choices=["scraping", "authenticated", "both"])
    parser.add_argument("--repeats", type=int, default=3, help="Runs per config")
    parser.add_argument("--output", default="../data/", help="Output directory")
    parser.add_argument("--backend", default="browseruse", choices=["browseruse", "mock"])
    parser.add_argument(
        "--surface",
        default="controlled",
        choices=["controlled", "live"],
        help="Controlled publisher comparison or live web characterization",
    )
    parser.add_argument("--publisher-base-url", default=DEFAULT_TEST_PUBLISHER_URL)
    parser.add_argument("--max-steps", type=int, default=15, help="Max BrowserUse agent steps for live runs")
    parser.add_argument(
        "--live-driver",
        default="auto",
        choices=["auto", "agent", "scripted", "scripted-random"],
        help=(
            "For live runs: use LLM agent path when credentials exist, force agent mode, "
            "force deterministic scripted BrowserUse browsing, or force randomized scripted browsing"
        ),
    )
    args = parser.parse_args()

    print("\n  BrowseTrace Benchmark Runner\n")
    run_benchmark(
        task_id=args.task,
        mode=args.mode,
        repeats=args.repeats,
        output_dir=args.output,
        backend=args.backend,
        surface=args.surface,
        publisher_base_url=args.publisher_base_url,
        max_steps=args.max_steps,
        live_driver=args.live_driver,
    )


if __name__ == "__main__":
    main()
