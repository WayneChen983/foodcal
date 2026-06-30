#!/usr/bin/env python3
"""
Multi-Object 3D Volume Calculation System (Contour Polygon Method)
==================================================================
流程：
  1. 從每個 SAM Mask 提取輪廓多邊形（findContours）
  2. 將多邊形從像素座標映射到 GLB 模型的 XY 空間
  3. 用 shapely 判斷每個 3D 頂點是否在多邊形範圍內
  4. 建立子網格 → 解決底部不封閉問題 → 計算真實體積（cm³）

Dependencies:
    pip install trimesh numpy opencv-python shapely
"""

import numpy as np
import trimesh
import cv2
from shapely.geometry import Polygon, Point
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from pathlib import Path


# ---------------------------------------------------------------------------
# Data Structure
# ---------------------------------------------------------------------------

@dataclass
class ObjectVolume:
    object_id: str
    raw_volume: float        # 模型原始單位³
    real_volume_cm3: float   # 真實體積（cm³）
    is_watertight: bool
    auto_fixed: bool         # True = 使用 convex_hull 修正
    contour_area_px2: float  # SAM mask 的像素面積（除錯用）
    vertex_count: int = 0
    bbox_min: np.ndarray = field(default_factory=lambda: np.zeros(3))
    bbox_max: np.ndarray = field(default_factory=lambda: np.zeros(3))


# ---------------------------------------------------------------------------
# 主函式
# ---------------------------------------------------------------------------

def calculate_volumes(
    glb_path: str,
    sam_masks: List[np.ndarray],
    ratio_cm_per_px: float,
    contour_approx_epsilon: float = 2.0,
    min_contour_area_px2: float = 500.0,
    min_vertex_threshold: int = 10,
    coord_transform: Optional[callable] = None,
) -> List[ObjectVolume]:
    """
    根據 SAM Mask 輪廓多邊形切割 GLB 網格並計算各物體體積。

    Parameters
    ----------
    glb_path : str
        GLB 3D 模型路徑。
    sam_masks : List[np.ndarray]
        SAM 輸出的二值遮罩列表，shape (H, W)。
    ratio_cm_per_px : float
        像素→公分的比例（1px = ratio cm）。體積換算用 ratio³。
    contour_approx_epsilon : float
        cv2.approxPolyDP 的精度，越大輪廓越粗略（節省計算）。
    min_contour_area_px2 : float
        面積低於此值的輪廓視為噪點，直接忽略。
    min_vertex_threshold : int
        子網格頂點數太少則跳過（碎片處理）。
    coord_transform : callable, optional
        自訂座標映射函式：(pixels_2d, img_shape, vertices_3d) → polygon_3d_xy
        若為 None，使用預設線性映射。

    Returns
    -------
    List[ObjectVolume]
    """

    # ── Step 1：載入 GLB 合併成單一網格 ────────────────────────────────
    print(f"[1/5] Loading GLB: {glb_path}")
    scene = trimesh.load(glb_path, force="scene")

    if isinstance(scene, trimesh.Scene):
        meshes = [g for g in scene.geometry.values() if isinstance(g, trimesh.Trimesh)]
        if not meshes:
            raise ValueError("GLB 中找不到任何 Trimesh 物件")
        mesh = trimesh.util.concatenate(meshes)
    elif isinstance(scene, trimesh.Trimesh):
        mesh = scene
    else:
        raise ValueError(f"不支援的類型: {type(scene)}")

    vertices = mesh.vertices   # (V, 3)
    faces    = mesh.faces      # (F, 3)
    print(f"    頂點數: {len(vertices)}, 面數: {len(faces)}")

    H, W = sam_masks[0].shape[:2]
    ratio3 = ratio_cm_per_px ** 3
    results: List[ObjectVolume] = []

    # ── Step 2-4：對每個 Mask 處理 ──────────────────────────────────────
    for mask_id, mask in enumerate(sam_masks):

        # ── Step 2：提取輪廓多邊形 ────────────────────────────────────
        contours, _ = cv2.findContours(
            mask.astype(np.uint8),
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        if not contours:
            print(f"    [Object_{mask_id}] 無輪廓，跳過")
            continue

        # 取面積最大的輪廓（避免 mask 內有碎片）
        contour = max(contours, key=cv2.contourArea)
        contour_area = cv2.contourArea(contour)

        if contour_area < min_contour_area_px2:
            print(f"    [Object_{mask_id}] 輪廓面積過小 ({contour_area:.1f}px²)，跳過")
            continue

        # 簡化輪廓（減少多邊形頂點數，提升效能）
        approx = cv2.approxPolyDP(contour, epsilon=contour_approx_epsilon, closed=True)
        contour_pts = approx.squeeze()  # (N, 2) → 每行 [col, row]

        if contour_pts.ndim == 1:
            contour_pts = contour_pts.reshape(1, 2)

        print(f"    [Object_{mask_id}] 輪廓頂點: {len(contour_pts)}, 面積: {contour_area:.1f}px²")

        # ── Step 3：像素座標 → GLB XY 空間座標 ───────────────────────
        if coord_transform is not None:
            poly_pts_3d = coord_transform(contour_pts, (H, W), vertices)
        else:
            poly_pts_3d = _default_pixel_to_model_xy(contour_pts, H, W, vertices)

        # 建立 shapely 2D 多邊形（投影到 XY 平面）
        if len(poly_pts_3d) < 3:
            print(f"    [Object_{mask_id}] 多邊形頂點不足，跳過")
            continue

        try:
            polygon = Polygon(poly_pts_3d)
            if not polygon.is_valid:
                polygon = polygon.buffer(0)  # 修正自交多邊形
        except Exception as e:
            print(f"    [Object_{mask_id}] 多邊形建立失敗: {e}，跳過")
            continue

        # ── Step 4a：找出在多邊形內的頂點（XY 平面判斷）──────────────
        # 批次轉換為 shapely Points—比 loop 快很多
        in_polygon = _batch_point_in_polygon(vertices[:, :2], polygon)
        mask_vertex_indices = np.where(in_polygon)[0]

        if len(mask_vertex_indices) < min_vertex_threshold:
            print(f"    [Object_{mask_id}] 多邊形內頂點不足 ({len(mask_vertex_indices)})，跳過")
            continue

        # ── Step 4b：篩選完全在多邊形內的面 ──────────────────────────
        vertex_set = set(mask_vertex_indices.tolist())
        face_mask = np.array([
            faces[i, 0] in vertex_set and
            faces[i, 1] in vertex_set and
            faces[i, 2] in vertex_set
            for i in range(len(faces))
        ])
        sub_faces = faces[face_mask]

        if len(sub_faces) == 0:
            print(f"    [Object_{mask_id}] 無有效三角面，跳過")
            continue

        # 重新編號頂點索引（建立緊湊子網格）
        local_idx = {gv: lv for lv, gv in enumerate(mask_vertex_indices)}
        sub_vertices = vertices[mask_vertex_indices]
        sub_faces_local = np.array(
            [[local_idx[v] for v in f] for f in sub_faces]
        )

        # 建立子網格
        sub_mesh = trimesh.Trimesh(vertices=sub_vertices, faces=sub_faces_local)
        sub_mesh.remove_degenerate_faces()
        sub_mesh.remove_duplicate_faces()

        # ── Step 5：體積計算（處理底部不封閉問題）────────────────────
        is_watertight = sub_mesh.is_watertight
        auto_fixed = False

        if is_watertight:
            raw_volume = abs(sub_mesh.volume)
        else:
            # 底部不封閉 → convex hull 估算體積
            try:
                raw_volume = abs(sub_mesh.convex_hull.volume)
                auto_fixed = True
            except Exception as e:
                print(f"    [Object_{mask_id}] convex_hull 失敗 ({e})，跳過")
                continue

        real_volume_cm3 = raw_volume * ratio3

        results.append(ObjectVolume(
            object_id=f"Object_{mask_id}",
            raw_volume=raw_volume,
            real_volume_cm3=real_volume_cm3,
            is_watertight=is_watertight,
            auto_fixed=auto_fixed,
            contour_area_px2=contour_area,
            vertex_count=len(sub_vertices),
            bbox_min=sub_vertices.min(axis=0),
            bbox_max=sub_vertices.max(axis=0),
        ))

        status = "convex_hull" if auto_fixed else "direct"
        print(f"    [Object_{mask_id}] ✓ raw={raw_volume:.4f}, real={real_volume_cm3:.4f}cm³ ({status})")

    print(f"\n完成！共處理 {len(results)} 個物體。")
    return results


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def _default_pixel_to_model_xy(
    contour_pts: np.ndarray,
    H: int,
    W: int,
    vertices: np.ndarray,
) -> List[Tuple[float, float]]:
    """
    預設的像素→模型 XY 座標映射（線性縮放）。

    假設相機為正射俯視，圖片 (0,0)~(W,H) 對應模型 XY 的 bounding box。
    座標對應關係：
        col (0~W) → X (x_min ~ x_max)
        row (0~H) → Y (y_min ~ y_max)  [注意 row 軸方向]
    """
    x_min, x_max = vertices[:, 0].min(), vertices[:, 0].max()
    y_min, y_max = vertices[:, 1].min(), vertices[:, 1].max()

    poly_pts = []
    for pt in contour_pts:
        col, row = float(pt[0]), float(pt[1])
        glb_x = col / W * (x_max - x_min) + x_min
        glb_y = row / H * (y_max - y_min) + y_min
        poly_pts.append((glb_x, glb_y))

    return poly_pts


def _batch_point_in_polygon(
    xy_points: np.ndarray,   # (V, 2)
    polygon: Polygon,
) -> np.ndarray:             # (V,) bool
    """
    批次判斷一組 XY 點是否在 shapely Polygon 內。
    採用 numpy 先做 bounding box 預篩選以加速。
    """
    minx, miny, maxx, maxy = polygon.bounds

    # 快速預篩：在 bounding box 外的點不用進一步判斷
    in_bbox = (
        (xy_points[:, 0] >= minx) & (xy_points[:, 0] <= maxx) &
        (xy_points[:, 1] >= miny) & (xy_points[:, 1] <= maxy)
    )

    result = np.zeros(len(xy_points), dtype=bool)

    # 只對 bbox 內的點做精確 contains 判斷
    candidate_indices = np.where(in_bbox)[0]
    for i in candidate_indices:
        if polygon.contains(Point(xy_points[i, 0], xy_points[i, 1])):
            result[i] = True

    return result


# ---------------------------------------------------------------------------
# 報告輸出
# ---------------------------------------------------------------------------

def print_report(results: List[ObjectVolume]) -> None:
    sep = "─" * 80
    print(f"\n{sep}")
    print(f"  {'物體 ID':<12} {'原始體積 (u³)':>16} {'真實體積 (cm³)':>18} {'封閉':>8} {'備註'}")
    print(sep)
    for r in results:
        note = "Auto-fixed (convex_hull)" if r.auto_fixed else "OK"
        sealed = "Yes" if r.is_watertight else "No"
        print(f"  {r.object_id:<12} {r.raw_volume:>16.4f} {r.real_volume_cm3:>18.4f} {sealed:>8}   {note}")
    print(f"{sep}\n")


# ---------------------------------------------------------------------------
# 使用說明 / Demo Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    # ── 從 auto_pipeline.py 整合範例 ────────────────────────────────────
    # 假設 auto_pipeline.py 已輸出：
    #   - scene.glb  (3D 重構結果)
    #   - masks      (SAM 分割結果，list of np.ndarray)
    #   - ratio      (px→cm，由餐卡校準得出)

    GLB_PATH        = "scene.glb"
    RATIO_CM_PER_PX = 0.05          # 1px = 0.05cm（依實際校準值修改）

    if not Path(GLB_PATH).exists():
        print(f"[Demo] 找不到 '{GLB_PATH}'，請提供真實的 GLB 檔案。")
        print("[Demo] 用法: python calculate_volumes.py <glb_file> <ratio_cm_per_px>")
        sys.exit(0)

    if len(sys.argv) >= 3:
        GLB_PATH        = sys.argv[1]
        RATIO_CM_PER_PX = float(sys.argv[2])

    # 假 masks（示範）— 實際替換為 SAM 輸出
    H, W = 480, 640
    masks = [
        np.pad(np.ones((300, 200), dtype=np.uint8), ((90, 90), (50, 390))),  # 左側物體
        np.pad(np.ones((200, 250), dtype=np.uint8), ((140, 140), (330, 60))),  # 右側物體
    ]

    results = calculate_volumes(
        glb_path=GLB_PATH,
        sam_masks=masks,
        ratio_cm_per_px=RATIO_CM_PER_PX,
    )

    print_report(results)
