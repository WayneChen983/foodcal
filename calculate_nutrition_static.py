# -*- coding: utf-8 -*-
"""Map pipeline food labels → nutrition DB keys and compute portion nutrients."""
from __future__ import annotations

import json
import os
import re
from difflib import get_close_matches


def load_db(db_path):
    with open(db_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_volumes(vol_path):
    with open(vol_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _normalize(label: str) -> str:
    """Normalize VLM labels for matching: spaces → _, drop filler words."""
    s = label.lower().strip()
    s = s.replace("&", " and ")
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "_", s, flags=re.UNICODE)
    s = re.sub(r"_+", "_", s).strip("_")
    # drop common filler tokens
    fillers = {
        "and", "with", "the", "a", "an", "stir", "fried", "stirfried",
        "dish", "food", "item", "side",
    }
    parts = [p for p in s.split("_") if p and p not in fillers]
    return "_".join(parts)


# Aliases → DB keys (covers English VLM output + Chinese names)
ALIAS_TO_KEY = {
    # 鹽酥雞
    "salt_pepper_chicken": "salt_pepper_chicken",
    "salt_and_pepper_chicken": "salt_pepper_chicken",
    "taiwanese_fried_chicken": "salt_pepper_chicken",
    "popcorn_chicken": "salt_pepper_chicken",
    "鹽酥雞": "salt_pepper_chicken",
    # 炸雞腿
    "chicken": "chicken_leg_fried",
    "chicken_leg": "chicken_leg_fried",
    "fried_chicken": "chicken_leg_fried",
    "fried_chicken_leg": "chicken_leg_fried",
    "chicken_drumstick": "chicken_leg_fried",
    "炸雞腿": "chicken_leg_fried",
    # 芋頭
    "taro": "taro",
    "芋頭": "taro",
    # 蒸蛋
    "egg": "egg_steamed",
    "steamed_egg": "egg_steamed",
    "egg_custard": "egg_steamed",
    "蒸蛋": "egg_steamed",
    # 紅蘿蔔炒蛋
    "carrot_egg": "carrot_egg_stir_fried",
    "carrot_and_egg": "carrot_egg_stir_fried",
    "carrot_egg_stir_fried": "carrot_egg_stir_fried",
    "egg_carrot": "carrot_egg_stir_fried",
    "scrambled_egg_carrot": "carrot_egg_stir_fried",
    "carrot_scrambled_egg": "carrot_egg_stir_fried",
    "紅蘿蔔炒蛋": "carrot_egg_stir_fried",
    # 青椒
    "green_pepper": "green_pepper_stir_fried",
    "pepper": "green_pepper_stir_fried",
    "bell_pepper": "green_pepper_stir_fried",
    "青椒": "green_pepper_stir_fried",
    # 麻婆豆腐
    "mapo_tofu": "mapo_tofu",
    "mapo": "mapo_tofu",
    "tofu": "mapo_tofu",
    "麻婆豆腐": "mapo_tofu",
    # 韓式泡菜
    "kimchi": "kimchi_korean",
    "korean_kimchi": "kimchi_korean",
    "韓式泡菜": "kimchi_korean",
    "泡菜": "kimchi_korean",
    # 炒高麗菜
    "cabbage": "cabbage_stir_fried",
    "stir_fried_cabbage": "cabbage_stir_fried",
    "cabbage_stir_fried": "cabbage_stir_fried",
    "napa_cabbage": "cabbage_stir_fried",
    "炒高麗菜": "cabbage_stir_fried",
    "高麗菜": "cabbage_stir_fried",
    # 炒苦瓜
    "bitter_gourd": "bitter_gourd_stir_fried",
    "bitter_melon": "bitter_gourd_stir_fried",
    "炒苦瓜": "bitter_gourd_stir_fried",
    "苦瓜": "bitter_gourd_stir_fried",
}


def _build_lookup(db: dict) -> dict[str, str]:
    """Map normalized aliases / display names → DB keys."""
    lookup: dict[str, str] = {}
    for key, entry in db.items():
        if key.startswith("__"):
            continue
        lookup[_normalize(key)] = key
        lookup[key.lower()] = key
        display = entry.get("display_name", "")
        # Chinese part before " ("
        zh = display.split(" (")[0].strip()
        if zh:
            lookup[_normalize(zh)] = key
            lookup[zh] = key
        # English in parentheses
        m = re.search(r"\(([^)]+)\)", display)
        if m:
            lookup[_normalize(m.group(1))] = key
    for alias, key in ALIAS_TO_KEY.items():
        if key in db:
            lookup[_normalize(alias)] = key
            lookup[alias.lower()] = key
    return lookup


def resolve_food_key(food_label: str, db: dict, lookup: dict[str, str] | None = None) -> str | None:
    """Resolve a VLM / contour label to a nutrition DB key, or None if unknown."""
    if lookup is None:
        lookup = _build_lookup(db)
    clean = food_label.lower().strip()
    norm = _normalize(food_label)

    if clean in lookup:
        return lookup[clean]
    if norm in lookup:
        return lookup[norm]
    if clean in db:
        return clean
    if norm in db:
        return norm

    # Token containment: e.g. "carrot_and_egg_stir_fry" contains carrot+egg
    candidates = [k for k in db.keys() if not k.startswith("__")]
    for key in candidates:
        key_tokens = set(_normalize(key).split("_"))
        label_tokens = set(norm.split("_"))
        if key_tokens and key_tokens.issubset(label_tokens):
            return key
        # also try display Chinese tokens
        zh = db[key].get("display_name", "").split(" (")[0]
        if zh and zh in food_label:
            return key

    # Fuzzy on normalized keys + aliases
    pool = list(lookup.keys())
    hits = get_close_matches(norm, pool, n=1, cutoff=0.72)
    if hits:
        return lookup[hits[0]]
    return None


def calculate_nutrition(volumes, db):
    results = []
    total_kcal = 0.0
    total_protein = 0.0
    total_fat = 0.0
    total_carbs = 0.0

    lookup = _build_lookup(db)

    for food_label, volume in volumes.items():
        if food_label.lower() in ("card", "credit_card", "reference_card"):
            continue

        match_key = resolve_food_key(food_label, db, lookup)

        if match_key:
            info = db[match_key]
            d_name = info["display_name"]

            kcal = volume * info["kcal_cm3"]
            protein = volume * info["protein_g_cm3"]
            fat = volume * info["fat_g_cm3"]
            carbs = volume * info["carbs_g_cm3"]

            results.append({
                "original_label": food_label,
                "matched_as": d_name,
                "db_key": match_key,
                "volume_cm3": round(volume, 2),
                "kcal": round(kcal, 1),
                "protein_g": round(protein, 1),
                "fat_g": round(fat, 1),
                "carbs_g": round(carbs, 1),
            })

            total_kcal += kcal
            total_protein += protein
            total_fat += fat
            total_carbs += carbs
        else:
            results.append({
                "original_label": food_label,
                "matched_as": "UNKNOWN",
                "volume_cm3": round(volume, 2),
                "notes": (
                    "Could not match label to the 10 calibrated buffet items. "
                    "Volume is still valid; nutrition requires a DB entry."
                ),
            })

    return {
        "items": results,
        "totals": {
            "calories_kcal": round(total_kcal, 1),
            "protein_g": round(total_protein, 1),
            "fat_g": round(total_fat, 1),
            "carbohydrates_g": round(total_carbs, 1),
        },
    }


def main():
    base_dir = os.environ.get("FOODCAL_DIR") or os.path.dirname(os.path.abspath(__file__))
    vol_path = os.path.join(base_dir, "food_volumes_universal.json")
    db_path = os.path.join(base_dir, "food_nutrition_db.json")
    report_path = os.path.join(base_dir, "final_nutrition_report.json")

    if not os.path.exists(vol_path) or not os.path.exists(db_path):
        print("Required data files missing.")
        return

    volumes = load_volumes(vol_path)
    db = load_db(db_path)
    report = calculate_nutrition(volumes, db)

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 50)
    print("      TAIWAN BUFFET NUTRITION SUMMARY")
    print("=" * 50)
    print(f"{'Food Item':<15} | {'Vol(cm3)':>8} | {'Kcal':>6} | {'P(g)':>4} | {'F(g)':>4} | {'C(g)':>4}")
    print("-" * 50)
    for item in report["items"]:
        if item["matched_as"] == "UNKNOWN":
            print(f"{item['original_label']:<15} | {item['volume_cm3']:>8} | {'N/A':>6} | {'-':>4} | {'-':>4} | {'-':>4}")
        else:
            name = item["matched_as"].split(" ")[0]
            print(f"{name:<15} | {item['volume_cm3']:>8} | {item['kcal']:>6} | {item['protein_g']:>4} | {item['fat_g']:>4} | {item['carbs_g']:>4}")

    print("-" * 50)
    t = report["totals"]
    print(f"{'TOTAL':<15} | {'-':>8} | {t['calories_kcal']:>6} | {t['protein_g']:>4} | {t['fat_g']:>4} | {t['carbohydrates_g']:>4}")
    print("=" * 50)
    print(f"Report saved to: {report_path}\n")


if __name__ == "__main__":
    main()
