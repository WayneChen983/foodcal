#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""量測 pipeline 各階段耗時與 RunPod 單次推論成本估算。

用法：
  # 僅估算（不跑 GPU，$0）
  py scripts/benchmark_pipeline.py --estimate-only

  # 實際量測（需 GPU + 模型）
  py scripts/benchmark_pipeline.py

  # 指定三視角影像（論文順序：左45、右45、俯視90，ref_idx=2）
  py scripts/benchmark_pipeline.py --images left.jpg right.jpg top.jpg
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# RunPod On-Demand 參考價 (US$/hr)，2026
GPU_HOURLY_USD = {
    "NVIDIA L4": 0.59,
    "RTX 4090": 0.60,
    "RTX PRO 4500": 0.74,
    "RTX 5090": 0.99,
    "GTX 1080 (本機)": 0.0,
}

# 文獻／同類系統參考值（待 RunPod 實測替換）
DEFAULT_ESTIMATE_SEC = {
    "vlm": 18.0,
    "sam3": 12.0,
    "dust3r": 45.0,
    "volume": 3.0,
    "nutrition": 0.1,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _total_sec(timings: dict) -> float:
    if "total" in timings:
        return float(timings["total"])
    keys = ("vlm", "sam3", "dust3r", "volume", "nutrition")
    return round(sum(float(timings.get(k, 0)) for k in keys), 2)


def cost_usd(total_sec: float, hourly_usd: float) -> float:
    return round(total_sec / 3600.0 * hourly_usd, 4)


def print_cost_table(timings: dict, mem_peaks: dict | None, source: str) -> None:
    total = _total_sec(timings)
    mem = mem_peaks or {}

    print("\n" + "=" * 72)
    print(f"  FoodCal 推論時間與成本  [{source}]")
    print("=" * 72)

    print(f"\n{'階段':<12} {'耗時(秒)':>10} {'GPU峰值(GB)':>14}")
    print("-" * 40)
    for key, label in [
        ("vlm", "VLM 辨識"),
        ("sam3", "SAM3 分割"),
        ("dust3r", "DUSt3R 重建"),
        ("volume", "體積計算"),
        ("nutrition", "營養換算"),
    ]:
        if key in timings:
            peak = mem.get(key, mem.get("total", "-"))
            print(f"{label:<12} {timings[key]:>10.2f} {str(peak):>14}")

    print("-" * 40)
    print(f"{'總計':<12} {total:>10.2f} {str(mem.get('total', '-')):>14}")

    print(f"\n{'GPU 型號':<20} {'$/hr':>8} {'單次成本(US$)':>14} {'單次(NT$~32)':>14}")
    print("-" * 60)
    for gpu, rate in GPU_HOURLY_USD.items():
        if rate <= 0:
            print(f"{gpu:<20} {'本機':>8} {'$0.0000':>14} {'$0':>14}")
            continue
        c = cost_usd(total, rate)
        print(f"{gpu:<20} {rate:>8.2f} {c:>14.4f} {c * 32:>14.2f}")

    print("\n公式：單次成本 = 總秒數 ÷ 3600 × 每小時費用")
    print("=" * 72)


def default_images() -> list[str]:
    cal = ROOT / "drive-download-20260712T092115Z-2-001" / "鹽酥雞"
    names = ["left_45.jpeg", "right_45.jpeg", "top_90.jpeg"]
    paths = [str(cal / n) for n in names]
    if all(os.path.isfile(p) for p in paths):
        return paths
    fallback = ["1001.jpg", "1002.jpg", "1003.jpg"]
    return [str(ROOT / n) for n in fallback]


def run_benchmark(images: list[str], ref_idx: int) -> dict | None:
    import torch
    from master_pipeline import run_master_pipeline

    gpu_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU"
    print(f"Device: {gpu_name}")
    print(f"Images: {images}")
    print(f"ref_idx: {ref_idx} (reference = {images[ref_idx]})")

    result = run_master_pipeline(images, ref_idx=ref_idx)
    if result is None:
        return None

    payload = {
        "measured_at": _utc_now(),
        "source": "measured",
        "device": gpu_name,
        "images": images,
        "ref_idx": ref_idx,
        "timings_sec": result.get("timings_sec", {}),
        "gpu_memory_peak_gb": result.get("gpu_memory_peak_gb", {}),
        "volumes": result.get("volumes", {}),
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="FoodCal pipeline 計時與成本估算")
    parser.add_argument(
        "--estimate-only",
        action="store_true",
        help="不跑 GPU，用參考耗時估算成本（$0）",
    )
    parser.add_argument("--images", nargs=3, metavar="IMG", help="三視角影像路徑")
    parser.add_argument("--ref-idx", type=int, default=2, help="參考視角索引（預設 2=俯視）")
    parser.add_argument(
        "--out",
        default=str(ROOT / "benchmark_report.json"),
        help="輸出 JSON 路徑",
    )
    args = parser.parse_args()

    if args.estimate_only:
        timings = dict(DEFAULT_ESTIMATE_SEC)
        timings["total"] = _total_sec(timings)
        payload = {
            "measured_at": _utc_now(),
            "source": "estimate",
            "note": "參考值，非實測；請於 RunPod RTX PRO 4500 跑本腳本更新",
            "timings_sec": timings,
            "gpu_memory_peak_gb": {
                "vlm": "TODO",
                "sam3": "TODO",
                "dust3r": "TODO",
                "total": "TODO",
            },
        }
        print_cost_table(timings, None, "參考估算（未實測）")
    else:
        images = args.images or default_images()
        missing = [p for p in images if not os.path.isfile(p)]
        if missing:
            print("[ERROR] 找不到影像：", missing)
            return 1
        try:
            payload = run_benchmark(images, args.ref_idx)
        except Exception as exc:
            print(f"\n[ERROR] 量測失敗：{exc}")
            print("\n本機 GTX 1080 (8GB) 可能 VRAM 不足。")
            print("請改用：py scripts/benchmark_pipeline.py --estimate-only")
            print("或 RunPod 上：bash scripts/runpod_benchmark.sh")
            return 1
        if payload is None:
            print("[ERROR] Pipeline 回傳 None")
            return 1
        print_cost_table(
            payload["timings_sec"],
            payload["gpu_memory_peak_gb"],
            f"實測 · {payload['device']}",
        )

    out_path = Path(args.out)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n已寫入 {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
