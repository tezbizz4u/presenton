#!/usr/bin/env python3
"""ASF bulk deck generator: runs Presenton's own pipeline through its public API.

Same outline, layout, content, image and template code as the UI, but several decks
are generated in parallel and the script keeps running without a browser.

Usage (on the Presenton VM):
  python3 asf_bulk_generate.py --key-file /path/to/keyfile --jobs jobs.json --parallel 3
  python3 asf_bulk_generate.py --key-file /path/to/keyfile --brief "..." --slides 10 --name test10

jobs.json = [{"name": "pcos-m01", "brief": "...", "slides": 20, "template": "momentum"}, ...]

The API key is read from the file (or env PRESENTON_API_KEY) and is never printed.
Results go to ./bulk_out/<timestamp>/summary.json.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("PRESENTON_BASE", "https://presenton.arogyashikshafoundation.com")


def _req(method: str, path: str, key: str, body: dict | None = None, timeout: int = 120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode() or "{}")


def read_key(path: str | None) -> str:
    key = os.environ.get("PRESENTON_API_KEY", "").strip()
    if not key and path:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read().strip()
        # accept a bare key, KEY=value, or a JSON object holding it
        if raw.startswith("{"):
            obj = json.loads(raw)
            raw = next((str(v) for k, v in obj.items() if "key" in k.lower()), "")
        elif "=" in raw and "\n" not in raw:
            raw = raw.split("=", 1)[1]
        key = raw.strip().strip('"').strip("'")
    if not key:
        sys.exit("No API key: pass --key-file or set PRESENTON_API_KEY")
    return key


def quality_check(key: str, presentation_id: str) -> dict:
    """Cheap post-checks on the finished deck (notes length, duplicate images)."""
    out: dict = {}
    try:
        pres = _req("GET", f"/api/v1/ppt/presentation/{presentation_id}", key)
        slides = pres.get("slides") or []
        notes = [len(str(s.get("speaker_note") or "")) for s in slides]
        urls: list[str] = []

        def walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k in ("__image_url__", "image_url", "url") and isinstance(v, str):
                        urls.append(v)
                    else:
                        walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)

        for s in slides:
            walk(s.get("content"))
        real = [u for u in urls if "transparent" not in u and "/static/" not in u]
        out = {
            "slides": len(slides),
            "notes_min": min(notes) if notes else 0,
            "notes_lt_300": sum(1 for n in notes if n < 300),
            "images": len(real),
            "duplicate_images": len(real) - len(set(real)),
        }
    except Exception as exc:  # noqa: BLE001
        out = {"check_error": type(exc).__name__}
    return out


def run_job(job: dict, key: str, poll: int) -> dict:
    name = job["name"]
    started = time.time()
    body = {
        "content": job["brief"],
        "n_slides": int(job.get("slides", 10)),
        "template": job.get("template", "momentum"),
        "verbosity": job.get("verbosity", "standard"),
        "export_as": job.get("export_as", "pptx"),
        "include_title_slide": job.get("title_slide", True),
        "web_search": False,
    }
    if job.get("instructions"):
        body["instructions"] = job["instructions"]
    try:
        task = _req("POST", "/api/v1/ppt/presentation/generate/async", key, body)
    except urllib.error.HTTPError as exc:
        return {"name": name, "status": "submit_failed", "http": exc.code}
    task_id = task["id"]
    pid = (task.get("data") or {}).get("presentation_id")
    print(f"[{time.strftime('%H:%M:%S')}] {name}: submitted ({body['n_slides']} slides)", flush=True)
    last = ""
    while True:
        time.sleep(poll)
        try:
            st = _req("GET", f"/api/v1/ppt/presentation/status/{task_id}", key)
        except Exception:  # noqa: BLE001 - transient network error, keep polling
            continue
        data = st.get("data") or {}
        msg = f"{st.get('status')} {data.get('created_slides', '?')}/{body['n_slides']}"
        if msg != last:
            print(f"[{time.strftime('%H:%M:%S')}] {name}: {msg}", flush=True)
            last = msg
        if st.get("status") in ("completed", "error"):
            elapsed = round(time.time() - started)
            result = {
                "name": name,
                "status": st["status"],
                "seconds": elapsed,
                "slides_requested": body["n_slides"],
                "presentation_id": data.get("presentation_id") or pid,
                "path": data.get("path"),
                "message": st.get("message") if st["status"] == "error" else None,
            }
            if st["status"] == "completed" and result["presentation_id"]:
                result["check"] = quality_check(key, result["presentation_id"])
            return result
        if time.time() - started > job.get("timeout", 3600):
            return {"name": name, "status": "timeout", "seconds": round(time.time() - started)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key-file")
    ap.add_argument("--jobs", help="JSON file with a list of jobs")
    ap.add_argument("--brief")
    ap.add_argument("--slides", type=int, default=10)
    ap.add_argument("--name", default="test")
    ap.add_argument("--template", default="momentum")
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--poll", type=int, default=10)
    args = ap.parse_args()

    key = read_key(args.key_file)
    if args.jobs:
        with open(args.jobs, "r", encoding="utf-8") as fh:
            jobs = json.load(fh)
    elif args.brief:
        jobs = [{"name": args.name, "brief": args.brief, "slides": args.slides, "template": args.template}]
    else:
        sys.exit("Give --jobs or --brief")

    outdir = os.path.join("bulk_out", time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(outdir, exist_ok=True)
    t0 = time.time()
    results = []
    with cf.ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
        futures = [pool.submit(run_job, j, key, args.poll) for j in jobs]
        for fut in cf.as_completed(futures):
            res = fut.result()
            results.append(res)
            print(json.dumps(res), flush=True)
            with open(os.path.join(outdir, "summary.json"), "w", encoding="utf-8") as fh:
                json.dump(results, fh, indent=2)
    print(f"ALL DONE in {round(time.time() - t0)} s -> {outdir}/summary.json", flush=True)


if __name__ == "__main__":
    main()
