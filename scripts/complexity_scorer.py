"""
Maintainability dimension: cyclomatic complexity via `lizard`, scored as the
patch's complexity relative to QuixBugs' own reference-correct fix for that bug.
"""
import math
from pathlib import Path

import lizard

REPO_ROOT = Path(__file__).resolve().parent.parent
QUIXBUGS_DIR = REPO_ROOT / "data" / "quixbugs_raw"

# score = exp(-DECAY_RATE * max(0, patched_ccn - reference_ccn)).
# Arbitrary but documented; raw CCN delta is always reported alongside the
# score so the formula's effect is auditable rather than hidden inside a
# single number.
DECAY_RATE = 0.15


def total_cyclomatic_complexity(source_code: str, filename: str = "Patch.java") -> int:
    """Sums CCN across all functions/methods in the file. QuixBugs programs
    are typically a single class with 1-3 methods, so this captures the
    whole unit rather than trying to guess which method is "the" fix."""
    result = lizard.analyze_file.analyze_source_code(filename, source_code)
    return sum(f.cyclomatic_complexity for f in result.function_list)


def reference_complexity(bug_name: str) -> int:
    ref_path = QUIXBUGS_DIR / "correct_java_programs" / f"{bug_name}.java"
    return total_cyclomatic_complexity(ref_path.read_text(encoding="utf-8"), f"{bug_name}.java")


def maintainability_score_generic(name: str, patched_source: str, reference_source: str) -> dict:
    """Benchmark-agnostic core: score is 1.0 when the patch is at least as
    simple as the reference fix, decaying exponentially as it grows more
    complex than the reference. Comparing whole-file CCN (rather than just
    the changed method) is fine even for Defects4J's larger files: every
    method except the changed one is identical between patched and
    reference, so it contributes equally to both sides and cancels out of
    the delta - only the actually-changed method's complexity difference
    survives."""
    patched_ccn = total_cyclomatic_complexity(patched_source, f"{name}.java")
    ref_ccn = total_cyclomatic_complexity(reference_source, f"{name}_ref.java")
    delta = max(0, patched_ccn - ref_ccn)
    score = math.exp(-DECAY_RATE * delta)
    return {"name": name, "patched_ccn": patched_ccn, "reference_ccn": ref_ccn, "delta": delta, "score": score}


def maintainability_score(bug_name: str, patched_source: str) -> dict:
    """QuixBugs-specific: resolves the reference fix from correct_java_programs/."""
    ref_ccn = reference_complexity(bug_name)
    patched_ccn = total_cyclomatic_complexity(patched_source, f"{bug_name}.java")
    delta = max(0, patched_ccn - ref_ccn)
    score = math.exp(-DECAY_RATE * delta)
    return {
        "bug": bug_name,
        "patched_ccn": patched_ccn,
        "reference_ccn": ref_ccn,
        "delta": delta,
        "score": score,
    }


def defects4j_maintainability_score(pid: str, bid: str, patched_full_source: str) -> dict:
    """Defects4J-specific: resolves the reference fix as the officially
    upstream-fixed full file."""
    from defects4j_harness import checkout_and_cache_metadata, _docker_exec

    meta = checkout_and_cache_metadata(pid, bid)
    fixed_source = _docker_exec(
        f"cd {meta['workdir']} && git show D4J_{pid}_{bid}_FIXED_VERSION:{meta['rel_path']}", timeout=60
    ).stdout
    return maintainability_score_generic(f"{pid}_{bid}", patched_full_source, fixed_source)


if __name__ == "__main__":
    # Self-check: scoring each reference-correct file against itself should
    # always yield delta=0, score=1.0. If not, the CCN extraction is
    # inconsistent between two reads of the same logical content.
    from quixbugs_harness import list_bugs

    print(f"{'bug':<30} {'ref_ccn':>8} {'self_score':>11}")
    bad = 0
    for bug in list_bugs():
        ref_src = (QUIXBUGS_DIR / "correct_java_programs" / f"{bug}.java").read_text(encoding="utf-8")
        ref_src = ref_src.replace("package correct_java_programs;", "package java_programs;")
        r = maintainability_score(bug, ref_src)
        print(f"{bug:<30} {r['reference_ccn']:>8} {r['score']:>11.3f}")
        if r["score"] != 1.0:
            bad += 1
    print(f"\n{'OK' if bad == 0 else 'FAIL'}: {bad} bugs did not self-score to 1.0")
