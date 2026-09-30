"""
Dimension 3/4: security (SpotBugs+FindSecBugs findings vs. reference fix).
Gated on `compiled`, same reasoning as maintainability - see that script's
docstring. This is the dimension that was previously a mathematically
forced mirror of correctness (every compiling-AND-correct patch scored
exactly 1.0, every incorrect patch exactly 0.0); gating on compiled instead
of correct restores real, independent variation, especially on Defects4J
where a compiling-but-wrong patch can genuinely diverge from the reference
in ways that show up in the scan.

Needs the patch actually compiled in the container right now to scan its
.class files (security_scorer.defects4j_security_score assumes this), so
Defects4J patches get recompiled here independently of
evaluate_correctness.py - the trigger-test run is skipped (that's the part
this split avoids paying for again), just not the compile itself.
"""
import json
from pathlib import Path

from generate_patches import parse_bug_id
from security_scorer import security_score as quixbugs_security_score, defects4j_security_score
from defects4j_harness import run_patch as d4j_run_patch, restore_original as d4j_restore

REPO_ROOT = Path(__file__).resolve().parent.parent
PATCHES_DIR = REPO_ROOT / "results" / "patches"
CORRECTNESS_PATH = REPO_ROOT / "results" / "correctness_scores.json"
OUT_PATH = REPO_ROOT / "results" / "security_scores.json"
SCRATCH = REPO_ROOT / "scripts" / "_scratch_security"


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
                results.append({"model": model, "bug": bug_id, "security": 0.0})
                continue

            benchmark, pid, bid = parse_bug_id(bug_id)
            try:
                if benchmark == "quixbugs":
                    score = quixbugs_security_score(bug_id, record["patch_code"], SCRATCH)["score"]
                else:
                    r = d4j_run_patch(pid, bid, record["patch_code"])
                    if not r["compiled"]:
                        raise RuntimeError("recompile for security scan failed unexpectedly")
                    score = defects4j_security_score(pid, bid)["score"]
                    d4j_restore(pid, bid)
                print(f"security={score:.3f}")
                results.append({"model": model, "bug": bug_id, "security": score})
            except Exception as e:
                print(f"ERROR: {e}")
                results.append({"model": model, "bug": bug_id, "security": 0.0, "error": str(e)})

    OUT_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nWrote {len(results)} results to {OUT_PATH}")


if __name__ == "__main__":
    main()
