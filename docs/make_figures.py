"""Regenerate docs/images/*.png from docs/results.json.

    pip install matplotlib
    python docs/make_figures.py

Style: light chart surface so the PNGs read the same on GitHub's light and dark themes; the first three
slots of a colour-blind-checked categorical palette (blue, orange, aqua); text in neutral ink, never in
series colours; every bar carries its value (the aqua alone is under 3:1 contrast on the surface).
"""

from __future__ import annotations

import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "images")
DATA = json.load(open(os.path.join(HERE, "results.json"), encoding="utf-8"))

SURFACE, INK, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "text.color": INK, "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.edgecolor": GRID, "axes.spines.top": False, "axes.spines.right": False,
    "font.size": 10, "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "legend.frameon": False,
})


def finish(fig, name):
    os.makedirs(OUT, exist_ok=True)
    fig.savefig(os.path.join(OUT, name), dpi=170, bbox_inches="tight")
    plt.close(fig)
    print("wrote", name)


def style_axes(ax, axis="y"):
    ax.grid(axis=axis, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.spines["left"].set_color(GRID)
    ax.spines["bottom"].set_color(GRID)


def label_bars(ax, bars, fmt="{:.2f}", horizontal=False, pad=3):
    for bar in bars:
        value = bar.get_width() if horizontal else bar.get_height()
        if horizontal:
            ax.annotate(fmt.format(value), (value, bar.get_y() + bar.get_height() / 2), xytext=(pad, 0),
                        textcoords="offset points", va="center", fontsize=8.5, color=INK)
        else:
            ax.annotate(fmt.format(value), (bar.get_x() + bar.get_width() / 2, value), xytext=(0, pad),
                        textcoords="offset points", ha="center", fontsize=8.5, color=INK)


def hbar_pair(ax, labels, first, second, names, colors, fmt="{:.2f}"):
    height = 0.36
    ys = range(len(labels))
    b1 = ax.barh([y - height / 2 for y in ys], first, height - 0.04, color=colors[0], label=names[0],
                 edgecolor=SURFACE, linewidth=1.5)
    b2 = ax.barh([y + height / 2 for y in ys], second, height - 0.04, color=colors[1], label=names[1],
                 edgecolor=SURFACE, linewidth=1.5)
    ax.set_yticks(list(ys))
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    label_bars(ax, b1, fmt, horizontal=True)
    label_bars(ax, b2, fmt, horizontal=True)
    style_axes(ax, "x")


# 1. Romanized and mixed queries: transliteration off vs on ------------------------------------------------
def fig_translit():
    sweep = DATA["threshold_sweep"]
    groups = ["roman-bn", "roman-hi", "mixed", "hi->en", "bn->en", "rare-en", "en", "bn", "hi"]
    labels = [DATA["group_labels"][g] for g in groups]
    off = [sweep["hit3"][g][0] for g in groups]
    on = [sweep["hit3"][g][2] for g in groups]
    fig, ax = plt.subplots(figsize=(9, 5.4))
    hbar_pair(ax, labels, off, on, ["Transliteration off", "Transliteration on (default)"], [BLUE, ORANGE])
    ax.set_xlim(0, 1.12)
    ax.set_xlabel("hit@3 (the right page is in the top 3)")
    ax.set_title("Romanized and mixed queries now work; every other group is unchanged")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.13), ncol=2)
    finish(fig, "translit_hit3_by_group.png")


# 2. TRANSLIT_CONFIDENCE sweep -----------------------------------------------------------------------------
def fig_threshold():
    sweep = DATA["threshold_sweep"]
    settings = ["Translit.\noff", "Fallback\noff (0.0)", "0.5\n(default)", "0.8", "0.95"]
    fig, (a, b) = plt.subplots(1, 2, figsize=(11, 4.6))
    bars = a.bar(range(5), sweep["hit3"]["mixed"], 0.6, color=BLUE, edgecolor=SURFACE, linewidth=1.5)
    label_bars(a, bars)
    a.set_xticks(range(5))
    a.set_xticklabels(settings)
    a.set_ylim(0, 1.1)
    a.set_ylabel("hit@3, mixed English + romanized (10 questions)")
    a.set_title("Accuracy: the fallback fixes mixed queries")
    style_axes(a)

    width = 0.36
    xs = list(range(5))
    mixed = sweep["seconds_per_query"]["mixed"]
    english = sweep["seconds_per_query"]["en"]
    b1 = b.bar([x - width / 2 for x in xs], mixed, width - 0.04, color=BLUE, label="Mixed queries",
               edgecolor=SURFACE, linewidth=1.5)
    b2 = b.bar([x + width / 2 for x in xs], english, width - 0.04, color=ORANGE, label="English queries",
               edgecolor=SURFACE, linewidth=1.5)
    label_bars(b, b1, "{:.2f}")
    label_bars(b, b2, "{:.2f}")
    b.set_xticks(xs)
    b.set_xticklabels(settings)
    b.set_ylim(0, 4.8)
    b.set_ylabel("seconds per query")
    b.set_title("Cost: only low-confidence queries pay extra")
    b.legend(loc="upper left")
    style_axes(b)
    fig.suptitle("TRANSLIT_CONFIDENCE: 0.5, 0.8 and 0.95 are equally accurate; 0.5 is the cheapest",
                 x=0.01, ha="left", fontsize=12, fontweight="bold", y=1.02)
    finish(fig, "translit_confidence_sweep.png")


# 3. Where q25 is lost ---------------------------------------------------------------------------------------
def fig_q25():
    trace = DATA["q25_trace"]
    stages = trace["stages"]
    fig, ax = plt.subplots(figsize=(9.5, 4.8))
    missing = 32

    def series(values):
        return [missing if v is None else v for v in values]

    xs = list(range(len(stages)))
    for values, color, name, shift in (
        (trace["english"], BLUE, "English question", -0.07),
        (trace["hindi"], ORANGE, "Hindi question (q25)", 0.07),
    ):
        points = [x + shift for x in xs]
        ax.scatter(points, series(values), s=70, color=color, edgecolor=SURFACE, linewidth=1.5, zorder=3, label=name)
        for x, v in zip(points, values):
            text = "not found" if v is None else str(v)
            dy = 10 if color == ORANGE else -15
            ax.annotate(text, (x, missing if v is None else v), xytext=(0, dy), textcoords="offset points",
                        ha="center", fontsize=8.5, color=INK)
    ax.set_ylim(missing + 3, -1)
    ax.set_yticks([1, 3, 5, 10, 15, 20, 25, 30])
    ax.axhline(3, color=GRID, linewidth=1.2, linestyle="--")
    ax.annotate("top 3", (len(stages) - 0.5, 3), xytext=(0, 4), textcoords="offset points", ha="right",
                fontsize=8.5, color=MUTED)
    ax.set_xticks(xs)
    ax.set_xticklabels(stages)
    ax.set_ylabel("rank of the correct chunk (1 = best)")
    ax.set_title("q25: dense search finds the answer at #1; BM25, RRF and the reranker lose it")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=2)
    style_axes(ax)
    finish(fig, "q25_stage_trace.png")


# 4. BM25 weight sweep -----------------------------------------------------------------------------------------
def fig_bm25_weight():
    sweep = DATA["bm25_weight_sweep"]
    xs = list(range(len(sweep["weights"])))
    fig, ax = plt.subplots(figsize=(9.5, 4.8))
    end_offsets = {"q25": (12, 9), "q24": (12, -9), "q27": (12, 0)}
    for qid, color in (("q27", AQUA), ("q24", BLUE), ("q25", ORANGE)):
        values = sweep["rrf_rank"][qid]
        ax.plot(xs, values, color=color, linewidth=2, marker="o", markersize=7, markeredgecolor=SURFACE,
                markeredgewidth=1.5, label=qid, zorder=3)
        ax.annotate(f"{qid} = {values[-1]}", (xs[-1], values[-1]), xytext=end_offsets[qid], textcoords="offset points",
                    va="center", fontsize=9, color=INK)
        if qid == "q25":
            for x, v in zip(xs[:-1], values[:-1]):
                ax.annotate(str(v), (x, v), xytext=(0, 9), textcoords="offset points", ha="center", fontsize=8, color=MUTED)
    ax.axhline(3, color=GRID, linewidth=1.2, linestyle="--")
    ax.annotate("top 3", (0, 3), xytext=(-2, 4), textcoords="offset points", fontsize=8.5, color=MUTED)
    ax.set_xticks(xs)
    ax.set_xticklabels(sweep["weights"])
    ax.set_xlim(-0.3, len(xs) - 0.1)
    ax.set_ylim(12.5, 0)
    ax.set_xlabel("BM25 weight in the RRF merge (applied when the question's language differs from the top dense hit's)")
    ax.set_ylabel("RRF rank of the correct chunk")
    ax.set_title("A soft BM25 weight only helps once it is tiny (about 0.015 or less)")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=3)
    style_axes(ax)
    finish(fig, "bm25_weight_sweep.png")


# 5. Variance experiments ------------------------------------------------------------------------------------------
def fig_variance():
    var = DATA["variance_experiments"]
    fig, (a, b) = plt.subplots(1, 2, figsize=(12.5, 4.8), gridspec_kw={"width_ratios": [1, 1.2], "wspace": 0.75})
    width = 0.36
    right = [var["z_top1"]["dense"]["correct_mean"], var["z_top1"]["bm25"]["correct_mean"]]
    wrong = [var["z_top1"]["dense"]["wrong_mean"], var["z_top1"]["bm25"]["wrong_mean"]]
    b1 = a.bar([-width / 2, 1 - width / 2], right, width - 0.04, color=BLUE, label="Top hit was correct",
               edgecolor=SURFACE, linewidth=1.5)
    b2 = a.bar([width / 2, 1 + width / 2], wrong, width - 0.04, color=ORANGE, label="Top hit was wrong",
               edgecolor=SURFACE, linewidth=1.5)
    label_bars(a, b1, "{:.1f}")
    label_bars(a, b2, "{:.1f}")
    a.set_xticks([0, 1])
    a.set_xticklabels(["Dense (cosine)", "BM25"])
    a.set_ylim(0, 9)
    a.set_ylabel("mean z-score of the top hit")
    a.set_title("A standing-out top hit predicts a correct one")
    a.legend(loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=2)
    style_axes(a)

    rows = var["fusion"]
    ys = range(len(rows))
    bars = b.barh(list(ys), [r["hit3"] for r in rows], 0.6, color=BLUE, edgecolor=SURFACE, linewidth=1.5)
    label_bars(b, bars, "{:.3f}", horizontal=True)
    b.set_yticks(list(ys))
    b.set_yticklabels([r["name"] for r in rows])
    b.invert_yaxis()
    b.set_xlim(0, 1.08)
    b.set_xlabel("hit@3, 73 typed questions (one question = 0.014)")
    b.set_title("...but weighting the merge by it gains nothing")
    style_axes(b, "x")
    finish(fig, "variance_experiments.png")


# 6. Reranker vs retrieval only -----------------------------------------------------------------------------------
def fig_retrieval_vs_full():
    data = DATA["retrieval_vs_full"]
    labels = [DATA["group_labels"][g] for g in data["groups"]]
    fig, ax = plt.subplots(figsize=(9, 4.8))
    hbar_pair(ax, labels, data["retrieval"], data["full"],
              ["Retrieval only (kNN + BM25 + RRF)", "Full pipeline (+ rerank, diversify, QA)"], [BLUE, ORANGE])
    ax.set_xlim(0, 1.12)
    ax.set_xlabel("hit@3")
    ax.set_title("The cross-encoder reranker does the heaviest lifting")
    ax.legend(loc="lower right")
    finish(fig, "retrieval_vs_full_pipeline.png")


# 7. Feature ablation ----------------------------------------------------------------------------------------------
def fig_ablation():
    rows = DATA["ablation_retrieval"]["rows"]
    fig, (a, b) = plt.subplots(1, 2, figsize=(11.5, 4.4), sharey=True)
    ys = list(range(len(rows)))
    for ax, key, title in ((a, "hit3", "hit@3"), (b, "mrr", "MRR")):
        bars = ax.barh(ys, [r[key] for r in rows], 0.6, color=BLUE, edgecolor=SURFACE, linewidth=1.5)
        label_bars(ax, bars, "{:.3f}" if key == "mrr" else "{:.2f}", horizontal=True)
        ax.set_xlim(0, 0.8)
        ax.set_title(title)
        style_axes(ax, "x")
    a.set_yticks(ys)
    a.set_yticklabels([r["name"] for r in rows])
    a.invert_yaxis()
    fig.suptitle("Retrieval-only ablation (109 questions): features move hit@3 by at most 0.04 and MRR by 0.03, within noise",
                 x=0.01, ha="left", fontsize=12, fontweight="bold", y=1.03)
    finish(fig, "feature_ablation_retrieval.png")


# 8. Latency per group -----------------------------------------------------------------------------------------------
def fig_latency():
    sweep = DATA["threshold_sweep"]
    groups = list(DATA["groups"])
    seconds = [sweep["seconds_per_query"][g][2] for g in groups]
    order = sorted(range(len(groups)), key=lambda i: seconds[i])
    fig, ax = plt.subplots(figsize=(8.5, 4.4))
    bars = ax.barh(range(len(order)), [seconds[i] for i in order], 0.6, color=BLUE, edgecolor=SURFACE, linewidth=1.5)
    label_bars(ax, bars, "{:.2f} s", horizontal=True)
    ax.set_yticks(range(len(order)))
    ax.set_yticklabels([DATA["group_labels"][groups[i]] for i in order])
    ax.set_xlim(0, 4.4)
    ax.set_xlabel("seconds per query, default settings (RTX 3060, full pipeline)")
    ax.set_title("Latency per query type: 2.1-2.7 s; mixed queries pay for a second search")
    style_axes(ax, "x")
    finish(fig, "latency_by_group.png")


if __name__ == "__main__":
    for build in (fig_translit, fig_threshold, fig_q25, fig_bm25_weight, fig_variance, fig_retrieval_vs_full,
                  fig_ablation, fig_latency):
        build()
