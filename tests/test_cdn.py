#!/usr/bin/env python3
"""Bounded HTTP/2 diagnostic probe for a CDN endpoint owned by the operator.

This is NOT a VLESS/XHTTP implementation. It imitates the *HTTP shape* only:
parallel long-lived GET downlinks and GET uplinks with opaque header data.
A 400 response is expected from Xray when the session data is not valid.
"""
import argparse
import asyncio
import base64
import csv
import os
import random
import secrets
import statistics
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

try:
    import httpx
except ImportError:
    raise SystemExit("Install dependency: python3 -m pip install 'httpx[http2]'")

FIELDS = ["time_utc", "phase", "kind", "index", "status", "http_version", "elapsed_ms", "first_byte_ms", "bytes_read", "edge", "request_id", "error_type", "error"]


def token(n=12):
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    return "".join(secrets.choice(alphabet) for _ in range(n))


def random_opaque(n):
    # Real XHTTP encodes protocol frames; this only approximates data entropy/size.
    return base64.urlsafe_b64encode(os.urandom(n)).rstrip(b"=").decode("ascii")


def build_headers(shaped, session, data_bytes=512):
    headers = {"Accept": "*/*", "Cache-Control": "no-store", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"}
    if shaped:
        headers.update({
            "X-Auth-Token": session,
            "X-Cache": random_opaque(random.randint(80, 180)),
            "X-Range": random_opaque(data_bytes),
        })
    return headers


async def one_request(client, sem, args, results, phase, kind, index, session):
    async with sem:
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        start = time.monotonic()
        row = {k: "" for k in FIELDS}
        row.update(time_utc=timestamp, phase=phase, kind=kind, index=index)
        # Avoid caching by giving each request its own query; analogous to a sequence number.
        params = {"page": str(index), "_probe": token(8)}
        if phase == "control":
            params = {"_probe": token(8)}
        headers = build_headers(phase == "shaped", session, args.payload if kind == "upload" else 0)
        if kind == "downlink":
            headers.pop("X-Range", None)
        try:
            async with client.stream("GET", args.url, params=params, headers=headers) as response:
                first = time.monotonic()
                row.update(
                    status=response.status_code,
                    http_version=response.http_version,
                    first_byte_ms=round((first-start)*1000),
                    edge=response.headers.get("cache-host", ""),
                    request_id=response.headers.get("x-request-id", ""),
                )
                read = 0
                # Limit received bytes and total time, including streamed responses.
                deadline = start + args.timeout
                async for chunk in response.aiter_bytes():
                    read += len(chunk)
                    if read >= args.max_read or time.monotonic() >= deadline:
                        break
                row["bytes_read"] = read
        except Exception as exc:
            row["error_type"] = type(exc).__name__
            row["error"] = str(exc).replace("\n", " ")[:180]
        finally:
            row["elapsed_ms"] = round((time.monotonic()-start)*1000)
            results.append(row)
            outcome = str(row["status"]) if row["status"] != "" else row["error_type"]
            print(f"{phase:7} {kind:8} #{index:03} {outcome:20} {row['elapsed_ms']:>5}ms {row['http_version']:6} {row['edge']}", flush=True)


async def run_phase(client, args, results, phase):
    print(f"\n=== {phase.upper()} ===", flush=True)
    sem = asyncio.Semaphore(args.concurrency)
    tasks = []
    session = token(12)
    for i in range(args.count):
        # A small number of downlink-like streams amid the uplink-like requests.
        kind = "downlink" if phase == "shaped" and i % args.downlink_every == 0 else "upload"
        tasks.append(asyncio.create_task(one_request(client, sem, args, results, phase, kind, i+1, session)))
        await asyncio.sleep(1 / args.rate)
    await asyncio.gather(*tasks)


def summary(rows, phase):
    data = [x for x in rows if x["phase"] == phase]
    ok = [x for x in data if x["status"] != ""]
    err = [x for x in data if x["error_type"]]
    statuses = {}
    for row in ok:
        statuses[str(row["status"])] = statuses.get(str(row["status"]), 0) + 1
    times = [int(x["first_byte_ms"]) for x in ok if x["first_byte_ms"] != ""]
    p95 = sorted(times)[min(len(times)-1, int(len(times)*0.95))] if times else "-"
    print(f"{phase}: completed={len(ok)}/{len(data)}, errors={len(err)}, statuses={statuses}, median_ttfb_ms={int(statistics.median(times)) if times else '-'}, p95_ttfb_ms={p95}")
    if err:
        errors = {}
        for row in err:
            errors[row["error_type"]] = errors.get(row["error_type"], 0) + 1
        print("  error_types:", errors)


async def main():
    p = argparse.ArgumentParser(description="HTTP/2 CDN XHTTP-shape diagnostic (not real VLESS)")
    p.add_argument("--url", default="https://cdn.service-ya.fun/api-best")
    p.add_argument("--count", type=int, default=24, help="requests per phase (max 100)")
    p.add_argument("--rate", type=float, default=2.0, help="requests per second (max 5)")
    p.add_argument("--concurrency", type=int, default=4, help="max in-flight requests (max 8)")
    p.add_argument("--payload", type=int, default=768, help="random bytes in X-Range before base64 (max 2048)")
    p.add_argument("--downlink-every", type=int, default=5)
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--max-read", type=int, default=2048)
    p.add_argument("--output", default="xhttp_realistic_probe.csv")
    args = p.parse_args()
    parsed = urlparse(args.url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        p.error("--url must be an HTTPS URL")
    if not (1 <= args.count <= 100 and .1 <= args.rate <= 5 and 1 <= args.concurrency <= 8 and 1 <= args.payload <= 2048 and args.downlink_every >= 2 and 3 <= args.timeout <= 30 and 256 <= args.max_read <= 16384):
        p.error("out-of-range settings; use --help and keep the test bounded")
    timeout = httpx.Timeout(connect=min(5,args.timeout), read=args.timeout, write=5.0, pool=args.timeout)
    limits = httpx.Limits(max_connections=args.concurrency, max_keepalive_connections=args.concurrency)
    rows = []
    print(f"Target: {args.url} | HTTP/2 enabled | per phase: {args.count} | rate: {args.rate}/s | max concurrent: {args.concurrency}")
    print("Important: status 400 is EXPECTED for malformed (non-VLESS) session data.")
    async with httpx.AsyncClient(http2=True, timeout=timeout, limits=limits, follow_redirects=False, trust_env=False) as client:
        await run_phase(client, args, rows, "control")
        await run_phase(client, args, rows, "shaped")
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print("\n=== RESULTS ===")
    summary(rows, "control")
    summary(rows, "shaped")
    print("CSV:", args.output)
    print("Note: no valid VLESS handshake is generated. Compare with real Happ and origin/CDN logs.")


if __name__ == "__main__":
    asyncio.run(main())
