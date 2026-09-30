"""
Master aggregation step: reads the 4 independently-produced dimension
result files (correctness/maintainability/security/efficiency_scores.json,
each written by its own evaluate_*.py script) and combines them into the
per-model Quality Index under 3 weighting schemes.

This script does no compiling, testing, or scanning itself - that's the
whole point of the split (see evaluate_correctness.py's docstring): this
step is genuinely fast (pure arithmetic over already-computed numbers), so
re-running it after a QI weighting tweak costs milliseconds, not an hour.
"""
import json
import statistics
from pathlib import Path

from models_config import MODELS

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "results"
QI_OUT = RESULTS_DIR / "qi_results.json"

WEIGHTS = {
    "equal":                {"correctness": 0.25, "maintainability": 0.25, "security": 0.25, "efficiency": 0.25},
    "correctness_priority": {"correctness": 0.40, "maintainability": 0.20, "security": 0.20, "efficiency": 0.20},
    "other_priority":       {"correctness": 0.10, "maintainability": 0.30, "security": 0.30, "efficiency": 0.30},
}


def load_and_merge() -> list[dict]:
    correctness = json.loads((RESULTS_DIR / "correctness_scores.json").read_text())
    maintainability = {(r["model"], r["bug"]): r["maintainability"] for r in json.loads((RESULTS_DIR / "maintainability_scores.json").read_text())}
    security = {(r["model"], r["bug"]): r["security"] for r in json.loads((RESULTS_DIR / "security_scores.json").read_text())}
    efficiency = {(r["model"], r["bug"]): r["tokens_per_sec"] for r in json.loads((RESULTS_DIR / "efficiency_scores.json").read_text())}

    merged = []
    for c in correctness:
        key = (c["model"], c["bug"])
        merged.append({
            "model": c["model"], "bug": c["bug"], "benchmark": c["benchmark"],
            "correct": c["correct"], "compiled": c["compiled"],
            "maintainability": maintainability.get(key, 0.0),
            "security": security.get(key, 0.0),
            "tokens_per_sec": efficiency.get(key, 0.0),
        })
    return merged


def normalize_efficiency(scores: list[dict]) -> dict[str, float]:
    """Ratio-to-fastest (v/hi), NOT min-max ((v-lo)/(hi-lo)).

    Found via a real issue: min-max forces the slowest model to exactly
    0.0 and the fastest to exactly 1.0 regardless of the true magnitude of
    the difference, which guarantees efficiency spans the FULL 0-1 range no
    matter what - while correctness/maintainability/security don't get this
    treatment and keep their natural (much narrower) spread. Under "equal"
    weights this let the least-correct model (fastest, but wrong 85% of the
    time) outrank models 2-3x more accurate, purely because min-max handed
    it a mechanical advantage no other dimension got.

    Ratio-to-fastest keeps the fastest model at 1.0 (unavoidable - it's
    compared to itself) but gives everyone else their genuine relative
    speed rather than an artificially floored score. If the real gap is
    modest (2x), scores cluster near 1.0; if it's extreme (10x), they
    spread out - reflecting reality instead of an artifact of exactly which
    models happened to be in this particular comparison."""
    models_present = sorted({s["model"] for s in scores})
    per_model_tps = {}
    for model in models_present:
        vals = [s["tokens_per_sec"] for s in scores if s["model"] == model and s["tokens_per_sec"] > 0]
        per_model_tps[model] = statistics.mean(vals) if vals else 0.0
    if not per_model_tps:
        return {}
    hi = max(per_model_tps.values())
    if hi == 0:
        return {m: 0.0 for m in per_model_tps}
    return {m: v / hi for m, v in per_model_tps.items()}


def aggregate(scores: list[dict]) -> dict:
    eff_norm = normalize_efficiency(scores)
    per_model = {}
    for model in eff_norm:
        model_scores = [s for s in scores if s["model"] == model]
        per_model[model] = {
            "n_bugs": len(model_scores),
            "n_compiled": sum(1 for s in model_scores if s["compiled"]),
            "correctness": statistics.mean(1.0 if s["correct"] else 0.0 for s in model_scores),
            "maintainability": statistics.mean(s["maintainability"] for s in model_scores),
            "security": statistics.mean(s["security"] for s in model_scores),
            "efficiency": eff_norm[model],
        }
    return per_model


def compute_qi_table(per_model: dict) -> dict:
    qi_tables = {}
    for scheme_name, w in WEIGHTS.items():
        table = {
            model: sum(w[d] * dims[d] for d in ("correctness", "maintainability", "security", "efficiency"))
            for model, dims in per_model.items()
        }
        qi_tables[scheme_name] = dict(sorted(table.items(), key=lambda kv: -kv[1]))
    return qi_tables


def model_label(model: str) -> str:
    return MODELS.get(model, {}).get("label", model)


if __name__ == "__main__":
    scores = load_and_merge()
    print(f"Loaded {len(scores)} merged patch records.")

    per_model = aggregate(scores)
    print("\nPer-model dimension averages:")
    for model, dims in per_model.items():
        print(f"  {model_label(model):<35} n={dims['n_bugs']:>3} compiled={dims['n_compiled']:>3} "
              f"correctness={dims['correctness']:.3f} maintainability={dims['maintainability']:.3f} "
              f"security={dims['security']:.3f} efficiency={dims['efficiency']:.3f}")

    qi_tables = compute_qi_table(per_model)
    for scheme, table in qi_tables.items():
        print(f"\n=== QI ranking: {scheme} ===")
        for i, (model, qi) in enumerate(table.items(), 1):
            print(f"{i}. {model_label(model):<35} QI={qi:.4f}")

    QI_OUT.write_text(json.dumps({"per_model": per_model, "qi": qi_tables}, indent=2), encoding="utf-8")
    print(f"\nWrote {QI_OUT}")
