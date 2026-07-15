# -*- coding: utf-8 -*-
"""Process-level model cache so VLM / SAM3 / DUSt3R load once and reuse.

Env:
  FOODCAL_KEEP_MODELS=1  (default) keep models in GPU memory after first load
  FOODCAL_KEEP_MODELS=0  unload after each phase (low-VRAM / old behaviour)
"""
from __future__ import annotations

import os
import sys
import threading
from typing import Any

_lock = threading.Lock()
_cache: dict[str, Any] = {}


def keep_models() -> bool:
    return os.environ.get("FOODCAL_KEEP_MODELS", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _sam3_root() -> str:
    return (
        os.environ.get("SAM3_ROOT")
        or os.environ.get("FOODCAL_DIR")
        or os.path.dirname(os.path.abspath(__file__))
    )


def get_vlm():
    """Return (model, processor) for Qwen2.5-VL-7B."""
    with _lock:
        if "vlm" in _cache:
            print("[VLM] Reusing cached Qwen2.5-VL-7B")
            return _cache["vlm"]

        print("[VLM] Loading Qwen/Qwen2.5-VL-7B-Instruct (first time)...")
        import torch
        from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor

        model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            "Qwen/Qwen2.5-VL-7B-Instruct",
            torch_dtype=torch.float16,
            device_map="auto",
        )
        processor = AutoProcessor.from_pretrained("Qwen/Qwen2.5-VL-7B-Instruct")
        print("[VLM] Qwen model loaded and cached.")
        pair = (model, processor)
        if keep_models():
            _cache["vlm"] = pair
        return pair


def get_sam3():
    """Return (model, Sam3Processor, device)."""
    with _lock:
        if "sam3" in _cache:
            print("[SAM3] Reusing cached model")
            return _cache["sam3"]

        print("[SAM3] Loading model (first time)...")
        import torch
        from sam3.model_builder import build_sam3_image_model
        from sam3.model.sam3_image_processor import Sam3Processor

        root = _sam3_root()
        if root not in sys.path:
            sys.path.insert(0, root)

        device = "cuda" if torch.cuda.is_available() else "cpu"
        candidates = [
            os.path.join(root, "sam3", "assets", "bpe_simple_vocab_16e6.txt.gz"),
            os.path.join(root, "assets", "bpe_simple_vocab_16e6.txt.gz"),
        ]
        bpe_path = next((p for p in candidates if os.path.exists(p)), None)

        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        if device == "cuda":
            torch.autocast("cuda", dtype=torch.bfloat16).__enter__()

        model = build_sam3_image_model(bpe_path=bpe_path)
        model.to(device)
        model.eval()
        processor = Sam3Processor(model, confidence_threshold=0.3, device=device)
        print("[SAM3] Model loaded and cached.")
        bundle = (model, processor, device)
        if keep_models():
            _cache["sam3"] = bundle
        return bundle


def get_dust3r(device: str | None = None):
    """Return DUSt3R AsymmetricCroCo3DStereo on device."""
    with _lock:
        if "dust3r" in _cache:
            print("[DUSt3R] Reusing cached model")
            return _cache["dust3r"]

        print("[DUSt3R] Loading model (first time)...")
        import torch
        from dust3r.model import AsymmetricCroCo3DStereo

        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        model_name = "naver/DUSt3R_ViTLarge_BaseDecoder_512_dpt"
        model = AsymmetricCroCo3DStereo.from_pretrained(model_name).to(device)
        print("[DUSt3R] Model loaded and cached.")
        if keep_models():
            _cache["dust3r"] = model
        return model


def release(name: str | None = None) -> None:
    """Release one cached model or all. Used when KEEP_MODELS=0."""
    from auto_pipeline import cleanup_gpu

    with _lock:
        keys = [name] if name else list(_cache.keys())
        for k in keys:
            if k in _cache:
                del _cache[k]
                print(f"[cache] Released {k}")
        cleanup_gpu()


def preload_all() -> None:
    """Warm-load all three models (for webapp startup)."""
    get_vlm()
    get_sam3()
    get_dust3r()
    print("[cache] All models preloaded.")


def status() -> dict:
    return {
        "keep_models": keep_models(),
        "cached": sorted(_cache.keys()),
    }
