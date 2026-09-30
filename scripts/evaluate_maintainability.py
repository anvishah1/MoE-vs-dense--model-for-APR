"""
Dimension 2/4: maintainability (cyclomatic complexity vs. reference fix).
Gated on `compiled` (from results/correctness_scores.json), not `correct` -
a patch that compiles but fails a test still has real, measurable
complexity; only genuinely broken (non-compiling) code gets 0.0, since
there's no valid structure to measure.

Independent of evaluate_correctness.py and evaluate_security.py: reruns
neither compile+test nor the security scan, so tweaking the complexity
decay formula only costs this script's (fast) runtime.
"""
import json
from pathlib import Path

from generate_patches import parse_bug_id
from complexity_scorer import maintainability_score, defects4j_maintainability_score

REPO_ROOT = Path(__file__).resolve().parent.parent
PATCHES_DIR = REPO_ROOT / "results" / "patches"
CORRECTNESS_PATH = REPO_ROOT / "results" / "correctness_scores.json"
OUT_PATH = REPO_ROOT / "results" / "maintainability_scores.json"


def main():
    correctness = {(r["model"], r["bug"]): r for r in json.loads(CORRECTNESS_PATH.read_text())}

    results = []
    model_dirs = [d for d in PATCHES_DIR.iterdir() if d.is_dir()]
    total = sum(len(list(d.glob("*.json"))) for d in model_dirs)
    done = 0
    for model_dir in model_dirs:
        for f in sorted(model_dir.glob("*.json")):
            record = json.loads(f.read_text(encoding="utf-8"))
            model, bug_id = record["model"], record["bug"]
            done += 1
            print(f"[{done}/{total}] {model} :: {bug_id} ... ", end="", flush=True)

            c = correctness.get((model, bug_id))
            if not c or not c["compiled"]:
                print("skipped (did not compile) -> 0.0")
                results.append({"model": model, "bug": bug_id, "maintainability": 0.0})
                continue

            benchmark, pid, bid = parse_bug_id(bug_id)
            try:
                if benchmark == "quixbugs":
                    score = maintainability_score(bug_id, record["patch_code"])["score"]
                else:
                    score = defects4j_maintainability_score(pid, bid, record["patch_code"])["score"]
                print(f"maintainability={score:.3f}")
                results.append({"model": model, "bug": bug_id, "maintainability": score})
            except Exception as e:
                print(f"ERROR: {e}")
                results.append({"model": model, "bug": bug_id, "maintainability": 0.0, "error": str(e)})

    OUT_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nWrote {len(results)} results to {OUT_PATH}")


if __name__ == "__main__":
    main()
