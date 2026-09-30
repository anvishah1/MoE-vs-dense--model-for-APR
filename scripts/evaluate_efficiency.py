"""
Dimension 4/4: efficiency. Unlike the other 3, nothing to compute here -
tokens/sec, latency, and VRAM were already captured by efficiency_scorer
at generation time and saved into each patch's own JSON record. This
script just collects those numbers into results/efficiency_scores.json
for compute_final_results.py to read, so all 4 dimensions have a uniform
per-patch results file to combine.
"""
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PATCHES_DIR = REPO_ROOT / "results" / "patches"
OUT_PATH = REPO_ROOT / "results" / "efficiency_scores.json"


def main():
    results = []
    for model_dir in PATCHES_DIR.iterdir():
        if not model_dir.is_dir():
            continue
        for f in sorted(model_dir.glob("*.json")):
            record = json.loads(f.read_text(encoding="utf-8"))
            results.append({
                "model": record["model"],
                "bug": record["bug"],
                "tokens_per_sec": record.get("tokens_per_sec", 0.0),
                "eval_count": record.get("eval_count", 0),
                "peak_vram_mb": record.get("peak_vram_mb"),
            })

    OUT_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Wrote {len(results)} results to {OUT_PATH}")


if __name__ == "__main__":
    main()
