# -*- coding: utf-8 -*-
"""FoodCal Web API — 三視角上傳 + 雲端推論管線。"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("FOODCAL_DIR", str(ROOT))

WEBAPP_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEBAPP_DIR / "static"
UPLOAD_DIR = WEBAPP_DIR / "uploads"
RESULTS_DIR = WEBAPP_DIR / "results"

PIPELINE_LOCK = threading.Lock()
VIEW_ORDER = ("left_45", "right_45", "top_90")
REF_IDX = 2  # top_90 is reference view for segmentation
ANALYZE_TIMEOUT = float(os.environ.get("FOODCAL_ANALYZE_TIMEOUT", "900"))


def _remote_api_base() -> str | None:
    url = os.environ.get("FOODCAL_REMOTE_API", "").strip().rstrip("/")
    return url or None


def _require_httpx() -> None:
    if httpx is None:
        raise HTTPException(
            status_code=500,
            detail="httpx not installed. Run: pip install -r webapp/requirements.txt",
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _model_cache_status() -> dict:
    try:
        from model_cache import status
        return status()
    except Exception:
        return {"keep_models": None, "cached": []}


def _load_food_db() -> dict:
    path = ROOT / "food_nutrition_db.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _demo_report() -> dict:
    """UI 測試用示範資料（FOODCAL_DEMO=1）。"""
    return {
        "report": {
            "items": [
                {
                    "original_label": "chicken",
                    "matched_as": "鹽酥雞 (Salt & Pepper Chicken)",
                    "volume_cm3": 33.17,
                    "kcal": 130.9,
                    "protein_g": 8.8,
                    "fat_g": 7.4,
                    "carbs_g": 7.2,
                },
                {
                    "original_label": "cabbage",
                    "matched_as": "炒高麗菜 (Stir-fried Cabbage)",
                    "volume_cm3": 90.56,
                    "kcal": 14.5,
                    "protein_g": 0.8,
                    "fat_g": 0.1,
                    "carbs_g": 3.0,
                },
            ],
            "totals": {
                "calories_kcal": 145.4,
                "protein_g": 9.6,
                "fat_g": 7.5,
                "carbohydrates_g": 10.2,
            },
        },
        "volumes": {"chicken": 33.17, "cabbage": 90.56},
        "artifacts": {},
        "demo": True,
    }


def _run_pipeline(image_paths: list[str], job_dir: Path) -> dict:
    if os.environ.get("FOODCAL_DEMO", "").strip() in ("1", "true", "yes"):
        return _demo_report()

    from master_pipeline import run_master_pipeline

    report_path = job_dir / "report.json"
    with PIPELINE_LOCK:
        result = run_master_pipeline(
            image_paths,
            ref_idx=REF_IDX,
            report_path=str(report_path),
        )
    if result is None:
        raise RuntimeError("Pipeline failed (segmentation, card detection, or reconstruction).")
    return result


@asynccontextmanager
async def lifespan(_app: FastAPI):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    demo = os.environ.get("FOODCAL_DEMO", "").strip().lower() in ("1", "true", "yes")
    remote = _remote_api_base()
    preload = os.environ.get("FOODCAL_PRELOAD", "1").strip().lower() not in ("0", "false", "no")
    if preload and not demo and not remote:
        os.environ.setdefault("FOODCAL_KEEP_MODELS", "1")
        print("[startup] Preloading models into GPU (FOODCAL_KEEP_MODELS=1)...")
        try:
            from model_cache import preload_all
            preload_all()
        except Exception as exc:
            print(f"[startup] Preload skipped / failed: {exc}")
    yield


app = FastAPI(
    title="FoodCal API",
    description="基於 3D 重建與語意分割之食物熱量估算",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/config")
def config():
    remote = _remote_api_base()
    return {
        "mode": "remote_proxy" if remote else "local",
        "remote_api_configured": remote is not None,
        "demo_mode": os.environ.get("FOODCAL_DEMO", "") in ("1", "true", "yes"),
    }


@app.get("/api/health")
async def health():
    remote = _remote_api_base()
    if remote:
        _require_httpx()
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(f"{remote}/api/health")
                resp.raise_for_status()
                data = resp.json()
                data["remote_api"] = True
                data["local_proxy"] = True
                return data
        except Exception as exc:
            return JSONResponse(
                status_code=502,
                content={
                    "status": "error",
                    "remote_api": True,
                    "local_proxy": True,
                    "detail": f"無法連線雲端 GPU：{exc}",
                },
            )

    gpu = False
    try:
        import torch
        gpu = torch.cuda.is_available()
    except ImportError:
        pass
    return {
        "status": "ok",
        "time": _utc_now(),
        "gpu_available": gpu,
        "demo_mode": os.environ.get("FOODCAL_DEMO", "") in ("1", "true", "yes"),
        "remote_api": False,
        "pipeline_views": list(VIEW_ORDER),
        "ref_view": VIEW_ORDER[REF_IDX],
        "keep_models": os.environ.get("FOODCAL_KEEP_MODELS", "1"),
        "model_cache": _model_cache_status(),
    }


@app.get("/api/foods")
async def list_foods():
    remote = _remote_api_base()
    if remote:
        _require_httpx()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{remote}/api/foods")
            resp.raise_for_status()
            return resp.json()

    db = _load_food_db()
    items = []
    for key, entry in db.items():
        if key.startswith("__"):
            continue
        items.append({
            "key": key,
            "display_name": entry.get("display_name", key),
            "tfda_integration_no": entry.get("tfda_integration_no"),
            "kcal_cm3": entry.get("kcal_cm3"),
        })
    return {"count": len(items), "items": items}


@app.post("/api/analyze")
async def analyze(
    left_45: UploadFile = File(...),
    right_45: UploadFile = File(...),
    top_90: UploadFile = File(...),
):
    remote = _remote_api_base()
    if remote:
        _require_httpx()
        files = []
        for name, upload in (
            ("left_45", left_45),
            ("right_45", right_45),
            ("top_90", top_90),
        ):
            content = await upload.read()
            files.append((
                name,
                (upload.filename or f"{name}.jpg", content, upload.content_type or "image/jpeg"),
            ))
        try:
            async with httpx.AsyncClient(timeout=ANALYZE_TIMEOUT) as client:
                resp = await client.post(f"{remote}/api/analyze", files=files)
                if resp.status_code >= 400:
                    detail = resp.text
                    try:
                        detail = resp.json().get("detail", detail)
                    except Exception:
                        pass
                    raise HTTPException(status_code=resp.status_code, detail=detail)
                return resp.json()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"雲端推論失敗：{exc}") from exc

    job_id = uuid.uuid4().hex[:12]
    job_dir = UPLOAD_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    paths: list[str] = []
    uploads = (("left_45", left_45), ("right_45", right_45), ("top_90", top_90))
    try:
        for name, upload in uploads:
            ext = Path(upload.filename or "img.jpg").suffix or ".jpg"
            dest = job_dir / f"{name}{ext}"
            with open(dest, "wb") as out:
                shutil.copyfileobj(upload.file, out)
            paths.append(str(dest))

        result = _run_pipeline(paths, job_dir)
        report = result["report"]

        meta_path = job_dir / "meta.json"
        meta = {
            "job_id": job_id,
            "created_at": _utc_now(),
            "image_paths": paths,
            "volumes": result.get("volumes", {}),
            "demo": result.get("demo", False),
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        return {
            "job_id": job_id,
            "demo": result.get("demo", False),
            "items": report["items"],
            "totals": report["totals"],
            "volumes": result.get("volumes", {}),
        }
    except Exception as exc:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/jobs/{job_id}/segmented")
async def get_segmented_image(job_id: str):
    remote = _remote_api_base()
    if remote:
        _require_httpx()
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(f"{remote}/api/jobs/{job_id}/segmented")
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return Response(
                content=resp.content,
                media_type=resp.headers.get("content-type", "image/png"),
            )

    job_dir = UPLOAD_DIR / job_id
    if not job_dir.is_dir():
        raise HTTPException(status_code=404, detail="Job not found")
    labeled = None
    for p in job_dir.glob("*_segmented_labeled.png"):
        labeled = p
        break
    if labeled is None or not labeled.is_file():
        raise HTTPException(status_code=404, detail="Segmentation image not available")
    return FileResponse(labeled)


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
