# -*- coding: utf-8 -*-
"""Generate fig6_2: warm inference time & cost for measured GPUs."""
from pathlib import Path

import matplotlib.pyplot as plt

OUT = Path(__file__).resolve().parent / "figures" / "fig6_2.png"

gpus = ["RTX 4090", "RTX PRO 4500"]
times = [15.69, 20.61]
costs = [0.0026, 0.0042]
colors = ["#2d8a6a", "#1a5f4a"]

fig, axes = plt.subplots(1, 2, figsize=(9, 3.8))
axes[0].bar(gpus, times, color=colors, width=0.55)
axes[0].set_ylabel("Warm inference (s)")
axes[0].set_title("Latency (models cached)")
for i, v in enumerate(times):
    axes[0].text(i, v + 0.4, f"{v:.2f} s", ha="center", fontsize=10)
axes[0].set_ylim(0, 26)

axes[1].bar(gpus, costs, color=colors, width=0.55)
axes[1].set_ylabel("Cost per run (USD)")
axes[1].set_title("Cost @ RunPod On-Demand")
for i, v in enumerate(costs):
    axes[1].text(i, v + 0.00015, f"${v:.4f}", ha="center", fontsize=10)
axes[1].set_ylim(0, 0.0055)

fig.suptitle("Measured GPUs — warm inference time & cost", fontsize=11, y=1.02)
fig.tight_layout()
OUT.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(OUT, dpi=160, bbox_inches="tight")
print("wrote", OUT)
