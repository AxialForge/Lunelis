"""Render a chart spec (JSON) to PNG in the kit's look.

    python3 chart.py spec.json out.png ACCENT_HEX

Spec: {"type": "bar|hbar|stacked|line", "labels": [...], "series": [{"name": "...", "values": [...], "color": "#hex"}],
       "title": "optional", "unit": "optional", "ylabel": "optional"}
"""
import json, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

PALETTE = ["#1F5FA8", "#B5472A", "#3B6E3F", "#8C5B00", "#6B3FA0", "#0A7691"]
spec = json.load(open(sys.argv[1], encoding="utf-8"))
out = sys.argv[2]
accent = "#" + (sys.argv[3] if len(sys.argv) > 3 else "1F5FA8")
plt.rcParams.update({"font.family": ["Carlito", "DejaVu Sans"], "font.size": 10, "axes.edgecolor": "#B8BEC4",
                     "axes.labelcolor": "#33393F", "xtick.color": "#33393F", "ytick.color": "#33393F"})
labels, series, kind = spec["labels"], spec["series"], spec.get("type", "bar")
colors = [s.get("color") or ([accent] + PALETTE)[i % 7] for i, s in enumerate(series)]
fig, ax = plt.subplots(figsize=(6.8, spec.get("height", 3.2 if kind != "hbar" else max(2.2, 0.38 * len(labels) + 0.8))), dpi=200)
n = len(series)
if kind in ("bar", "stacked"):
    x = range(len(labels)); w = 0.8 / (1 if kind == "stacked" else n); bottom = [0] * len(labels)
    for i, s in enumerate(series):
        if kind == "stacked":
            bars = ax.bar(x, s["values"], 0.6, bottom=bottom, label=s["name"], color=colors[i])
            bottom = [b + v for b, v in zip(bottom, s["values"])]
        else:
            bars = ax.bar([p - 0.4 + w * (i + 0.5) for p in x], s["values"], w * 0.92, label=s["name"], color=colors[i])
            if n == 1: ax.bar_label(bars, fontsize=9, color="#33393F", padding=2)
    ax.set_xticks(list(x)); ax.set_xticklabels(labels)
elif kind == "hbar":
    ys = list(range(len(labels)))[::-1]; h = 0.7 / n
    for i, s in enumerate(series):
        bars = ax.barh([y + 0.35 - h * (i + 0.5) for y in ys], s["values"], h * 0.9, label=s["name"], color=colors[i])
        ax.bar_label(bars, fontsize=9, color="#33393F", padding=3)
    ax.set_yticks(ys); ax.set_yticklabels(labels)
elif kind == "line":
    for i, s in enumerate(series):
        ax.plot(labels, s["values"], marker="o", ms=4, lw=2, label=s["name"], color=colors[i])
for sp in ("top", "right"): ax.spines[sp].set_visible(False)
ax.grid(axis="x" if kind == "hbar" else "y", color="#E3E6E9", lw=0.8); ax.set_axisbelow(True)
if spec.get("ylabel"): ax.set_ylabel(spec["ylabel"])
if spec.get("title"): ax.set_title(spec["title"], loc="left", fontsize=11, fontweight="bold", color="#1E2327")
if n > 1: ax.legend(frameon=False, ncol=min(n, 4), loc="upper right", fontsize=9)
fig.tight_layout(); fig.savefig(out, facecolor="white"); print("chart", out)
