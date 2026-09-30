"""
Dimension 1/4: functional correctness. Compiles + runs the official tests
for every saved patch (results/patches/{model}/{bug}.json) and writes
results/correctness_scores.json - the only script that touches the slow
compile+test cycle (Defects4J compiles/JUnit runs inside Docker), so a
formula change in maintainability/security never needs to pay this cost
again.

Records BOTH `compiled` and `correct` per patch (not just correct) since
maintainability/security are gated on "compiled", not "passed tests" -
see project notes on why gating non-functional dimensions on test-pass
made security a mathematically forced mirror of correctness.
"""
import json
from pathlib import Path

from generate_patches import parse_bug_id, safe_model_dirname
from quixbugs_harness import run_quixbugs_bug
from defects4j_harness import run_patch as d4j_run_patch, restore_original as d4j_restore
from models_config import MODELS

REPO_ROOT = Path(__file__).resolve().parent.parent
PATCHES_DIR = REPO_ROOT / "results" / "patches"
OUT_PATH = REPO_ROOT / "results" / "correctness_scores.json"
SCRATCH = REPO_ROOT / "scripts" / "_scratch_correctness"


def evaluate_one(model: str, bug_id: str, patch_code: str) -> dict:
    benchmark, pid, bid = parse_bug_id(bug_id)
    if benchmark == "quixbugs":
        r = run_quixbugs_bug(bug_id, patch_code, SCRATCH)
        return {"model": model, "bug": bug_id, "benchmark": benchmark,
                "compiled": r.compiled, "correct": r.all_passed}
    else:
        r = d4j_run_patch(pid, bid, patch_code)
        correct = bool(r["compiled"] and r.get("all_trigger_tests_pass"))
        d4j_restore(pid, bid)
        return {"model": model, "bug": bug_id, "benchmark": benchmark,
                "compiled": r["compiled"], "correct": correct}


def main():
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
            try:
                r = evaluate_one(model, bug_id, record["patch_code"])
                results.append(r)
                print(f"compiled={r['compiled']} correct={r['correct']}")
            except Exception as e:
                print(f"ERROR: {e}")
                results.append({"model": model, "bug": bug_id, "benchmark": parse_bug_id(bug_id)[0],
                                 "compiled": False, "correct": False, "error": str(e)})

    OUT_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nWrote {len(results)} results to {OUT_PATH}")


if __name__ == "__main__":
    main()
