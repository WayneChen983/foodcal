#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""同一進程冷啟動 + 常駐推論計時（給表 6-9）。

用法：
  export FOODCAL_KEEP_MODELS=1
  python -u scripts/warm_bench.py
  python -u scripts/warm_bench.py --images a.jpg b.jpg c.jpg
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--images", nargs=3, metavar="IMG")
    parser.add_argument("--ref-idx", type=int, default=2)
    parser.add_argument("--out", default=str(ROOT / "benchmark_warm.json"))
    args = parser.parse_args()

    os.environ.setdefault("FOODCAL_KEEP_MODELS", "1")
    print("start", flush=True)

    import torch
    from master_pipeline import run_master_pipeline
    from model_cache import status

    gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU"
    print(f"GPU: {gpu}", flush=True)
    print(f"KEEP_MODELS={os.environ.get('FOODCAL_KEEP_MODELS')}", flush=True)

    if args.images:
        imgs = args.images
    else:
        imgs = [str(ROOT / n) for n in ("1001.jpg", "1002.jpg", "1003.jpg")]
    missing = [p for p in imgs if not os.path.isfile(p)]
    if missing:
        print("missing images:", missing, flush=True)
        return 1

    print("=== RUN1 cold ===", status(), flush=True)
    t0 = time.perf_counter()
    r1 = run_master_pipeline(imgs, ref_idx=args.ref_idx, report_path="/tmp/r1.json")
    run1_sec = round(time.perf_counter() - t0, 2)
    print("RUN1", run1_sec, r1.get("timings_sec") if r1 else None, flush=True)

    print("=== RUN2 warm ===", status(), flush=True)
    t1 = time.perf_counter()
    r2 = run_master_pipeline(imgs, ref_idx=args.ref_idx, report_path="/tmp/r2.json")
    run2_sec = round(time.perf_counter() - t1, 2)
    print("RUN2", run2_sec, r2.get("timings_sec") if r2 else None, flush=True)
    print("done", status(), flush=True)

    hourly = {
        "RTX 4090": 0.60,
        "RTX PRO 4500": 0.74,
        "L4": 0.59,
        "RTX 5090": 0.99,
    }
    rate = 0.60
    for k, v in hourly.items():
        if k.replace(" ", "").lower() in gpu.replace(" ", "").lower() or k.split()[-1] in gpu:
            rate = v
            break
    if "4090" in gpu:
        rate = 0.60
    elif "4500" in gpu:
        rate = 0.74
    elif "5090" in gpu:
        rate = 0.99
    elif "L4" in gpu:
        rate = 0.59

    payload = {
        "device": gpu,
        "hourly_usd": rate,
        "cold": {
            "wall_sec": run1_sec,
            "timings_sec": (r1 or {}).get("timings_sec"),
            "cost_usd": round(run1_sec / 3600 * rate, 4),
        },
        "warm": {
            "wall_sec": run2_sec,
            "timings_sec": (r2 or {}).get("timings_sec"),
            "cost_usd": round(run2_sec / 3600 * rate, 4),
        },
    }
    out = Path(args.out)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"wrote {out}", flush=True)
    print(
        f"SUMMARY warm={run2_sec}s cost≈${payload['warm']['cost_usd']} @ ${rate}/hr",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
