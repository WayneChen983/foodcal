import json
from calculate_nutrition_static import load_db, calculate_nutrition

vols = {
    "broccoli": 46.48,
    "corn": 23.00,
    "cabbage": 37.40,
    "rice": 65.71,
    "pork": 35.11
}
db = load_db("food_nutrition_db.json")
report = calculate_nutrition(vols, db)
with open("2003_report.json", "w") as f:
    json.dump(report, f, indent=2)

print(f"{'Food':<15} | {'Vol(cm3)':>8} | {'Kcal':>6} | {'P(g)':>5} | {'F(g)':>5} | {'C(g)':>5}")
print("-" * 60)
for it in report["items"]:
    name = it["matched_as"].split(" ")[0]
    print(f"{name:<15} | {it.get('volume_cm3', 0):>8} | {it.get('kcal', 0):>6} | {it.get('protein_g', 0):>5} | {it.get('fat_g', 0):>5} | {it.get('carbs_g', 0):>5}")
print("-" * 60)
t = report["totals"]
print(f"{'TOTAL':<15} | {'-':>8} | {t['calories_kcal']:>6} | {t['protein_g']:>5} | {t['fat_g']:>5} | {t['carbohydrates_g']:>5}")
