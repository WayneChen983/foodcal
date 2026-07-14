import json
import os
from difflib import get_close_matches

def load_db(db_path):
    with open(db_path, 'r') as f:
        return json.load(f)

def load_volumes(vol_path):
    with open(vol_path, 'r') as f:
        return json.load(f)

def calculate_nutrition(volumes, db):
    results = []
    total_kcal = 0
    total_protein = 0
    total_fat = 0
    total_carbs = 0
    
    db_keys = [k for k in db.keys() if not k.startswith("__")]
    
    # Manual Mapping Overrides for the experiment
    manual_map = {
        "salt_pepper_chicken": "salt_pepper_chicken",
        "chicken": "chicken_leg_fried",
        "chicken_leg": "chicken_leg_fried",
        "taro": "taro",
        "egg": "egg_steamed",
        "steamed_egg": "egg_steamed",
        "carrot_egg": "carrot_egg_stir_fried",
        "green_pepper": "green_pepper_stir_fried",
        "pepper": "green_pepper_stir_fried",
        "mapo_tofu": "mapo_tofu",
        "kimchi": "kimchi_korean",
        "cabbage": "cabbage_stir_fried",
        "bitter_gourd": "bitter_gourd_stir_fried",
    }
    
    for food_label, volume in volumes.items():
        # Clean label
        clean_label = food_label.lower().strip()
        
        # Mapping logic
        match_key = None
        if clean_label in manual_map:
            match_key = manual_map[clean_label]
        elif clean_label in db:
            match_key = clean_label
        
        if match_key:
            info = db[match_key]
            d_name = info["display_name"]
            
            # Calculations
            kcal = volume * info["kcal_cm3"]
            protein = volume * info["protein_g_cm3"]
            fat = volume * info["fat_g_cm3"]
            carbs = volume * info["carbs_g_cm3"]
            
            results.append({
                "original_label": food_label,
                "matched_as": d_name,
                "volume_cm3": round(volume, 2),
                "kcal": round(kcal, 1),
                "protein_g": round(protein, 1),
                "fat_g": round(fat, 1),
                "carbs_g": round(carbs, 1)
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
                "notes": "Could not find a suitable match in the static database."
            })

    summary = {
        "items": results,
        "totals": {
            "calories_kcal": round(total_kcal, 1),
            "protein_g": round(total_protein, 1),
            "fat_g": round(total_fat, 1),
            "carbohydrates_g": round(total_carbs, 1)
        }
    }
    return summary

def main():
    base_dir = "/home/bl515-01/sam3"
    vol_path = os.path.join(base_dir, "food_volumes_universal.json")
    db_path = os.path.join(base_dir, "food_nutrition_db.json")
    report_path = os.path.join(base_dir, "final_nutrition_report.json")
    
    if not os.path.exists(vol_path) or not os.path.exists(db_path):
        print("Required data files missing.")
        return

    volumes = load_volumes(vol_path)
    db = load_db(db_path)
    
    report = calculate_nutrition(volumes, db)
    
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)
    
    # Print formatted summary
    print("\n" + "="*50)
    print("      TAIWAN BUFFET NUTRITION SUMMARY")
    print("="*50)
    print(f"{'Food Item':<15} | {'Vol(cm3)':>8} | {'Kcal':>6} | {'P(g)':>4} | {'F(g)':>4} | {'C(g)':>4}")
    print("-" * 50)
    for item in report["items"]:
        if item["matched_as"] == "UNKNOWN":
            print(f"{item['original_label']:<15} | {item['volume_cm3']:>8} | {'N/A':>6} | {'-':>4} | {'-':>4} | {'-':>4}")
        else:
            name = item["matched_as"].split(" ")[0] # Show Chinese part
            print(f"{name:<15} | {item['volume_cm3']:>8} | {item['kcal']:>6} | {item['protein_g']:>4} | {item['fat_g']:>4} | {item['carbs_g']:>4}")
    
    print("-" * 50)
    t = report["totals"]
    print(f"{'TOTAL':<15} | {'-':>8} | {t['calories_kcal']:>6} | {t['protein_g']:>4} | {t['fat_g']:>4} | {t['carbohydrates_g']:>4}")
    print("="*50)
    print(f"Report saved to: {report_path}\n")

if __name__ == "__main__":
    main()
