"""Measure basic ClueStack API latency without external dependencies."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from urllib.request import urlopen


def percentile(values: list[float], percent: float) -> float:
    """Return a simple nearest-rank percentile."""

    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((percent / 100) * (len(ordered) - 1))))
    return ordered[index]


def main() -> None:
    """Run repeated GET requests and print latency stats as JSON."""

    parser = argparse.ArgumentParser(description="Measure ClueStack API latency.")
    parser.add_argument("--url", default="http://localhost:8000/healthz", help="URL to measure.")
    parser.add_argument("--requests", type=int, default=50, help="Number of requests to send.")
    args = parser.parse_args()

    durations: list[float] = []
    failures = 0
    for _ in range(args.requests):
        start = time.perf_counter()
        try:
            with urlopen(args.url, timeout=10) as response:
                response.read()
                if response.status >= 400:
                    failures += 1
        except Exception:
            failures += 1
        durations.append((time.perf_counter() - start) * 1000)

    result = {
        "url": args.url,
        "requests": args.requests,
        "failures": failures,
        "avg_ms": round(statistics.mean(durations), 2) if durations else 0,
        "p50_ms": round(percentile(durations, 50), 2),
        "p95_ms": round(percentile(durations, 95), 2),
        "max_ms": round(max(durations), 2) if durations else 0,
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
