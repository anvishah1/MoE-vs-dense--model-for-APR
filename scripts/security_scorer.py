"""
Security dimension: static scan of the patched file via SpotBugs + FindSecBugs.

Confirmed by inspecting findsecbugs-plugin-1.14.0.jar's findbugs.xml: every
FindSecBugs pattern is tagged category="SECURITY", which lets us pull out
just the security-relevant findings from SpotBugs' full report (which
otherwise mixes in style/correctness/performance categories we don't want
here - those aren't this dimension's job).

Known limitation (expected, not a bug): QuixBugs programs are small, I/O-free
algorithmic snippets with essentially no attack surface, so this dimension is
expected to mostly read 0 findings both before and after patching on QuixBugs.
It's the Defects4J half of the testbed (real-world projects) where this
dimension is expected to actually vary - that's the whole reason Defects4J
was added. Building/validating this on QuixBugs first only confirms the tool
runs and parses correctly, not that it's discriminative yet.
"""
import json
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
QUIXBUGS_DIR = REPO_ROOT / "data" / "quixbugs_raw"
SPOTBUGS_JAR = REPO_ROOT / "tools" / "spotbugs" / "spotbugs-4.10.4" / "lib" / "spotbugs.jar"
FINDSECBUGS_JAR = REPO_ROOT / "tools" / "spotbugs" / "plugin" / "findsecbugs-plugin-1.14.0.jar"
JAVA_HOME_BIN = Path("C:/Program Files/Eclipse Adoptium/jdk-17.0.20.101-hotspot/bin")

# Same decay-from-baseline shape as the maintainability scorer, for consistency:
# 1.0 if the patch introduces no more SECURITY findings than the reference
# fix, decaying as findings increase beyond that baseline.
DECAY_RATE = 0.5


def _find_jdk_bin(exe_name: str) -> str:
    found = shutil.which(exe_name)
    if found:
        return found
    candidate = JAVA_HOME_BIN / f"{exe_name}.exe"
    if candidate.exists():
        return str(candidate)
    raise FileNotFoundError(f"could not locate {exe_name}")


JAVAC = _find_jdk_bin("javac")
JAVA = _find_jdk_bin("java")


def compile_single_file(source_code: str, class_name: str, workdir: Path, extra_sources: list[Path] = ()) -> Path | None:
    """Compiles source_code (plus any extra helper files needed, e.g. Node.java
    for QuixBugs) into workdir/out. Returns the output dir, or None on failure."""
    if workdir.exists():
        shutil.rmtree(workdir)
    src_dir = workdir / "src"
    out_dir = workdir / "out"
    src_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)

    target = src_dir / f"{class_name}.java"
    target.write_text(source_code, encoding="utf-8")
    for extra in extra_sources:
        shutil.copy(extra, src_dir / extra.name)

    cmd = [JAVAC, "-d", str(out_dir), str(target), *[str(src_dir / e.name) for e in extra_sources]]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        return None
    return out_dir


def run_spotbugs_security_findings(class_dir: Path, timeout: int = 60) -> list[dict]:
    """Runs SpotBugs+FindSecBugs on a directory of .class files, returns only
    the SECURITY-category findings as a list of {type, priority, rank}."""
    xml_out = class_dir.parent / "spotbugs_report.xml"
    cmd = [
        JAVA, "-Dfindbugs.launchUI=0", "-jar", str(SPOTBUGS_JAR),
        "-textui", "-pluginList", str(FINDSECBUGS_JAR),
        "-low", "-xml", "-effort:max", "-output", str(xml_out),
        str(class_dir),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if not xml_out.exists():
        raise RuntimeError(f"SpotBugs produced no output: {proc.stdout}\n{proc.stderr}")

    tree = ET.parse(xml_out)
    findings = []
    for bug in tree.getroot().findall("BugInstance"):
        if bug.get("category") == "SECURITY":
            findings.append({
                "type": bug.get("type"),
                "priority": bug.get("priority"),
                "rank": bug.get("rank"),
            })
    return findings


def reference_security_findings(bug_name: str) -> list[dict]:
    ref_src = (QUIXBUGS_DIR / "correct_java_programs" / f"{bug_name}.java").read_text(encoding="utf-8")
    ref_src = ref_src.replace("package correct_java_programs;", "package java_programs;")
    workdir = REPO_ROOT / "scripts" / "_scratch_security_ref" / bug_name
    extras = [QUIXBUGS_DIR / "java_programs" / h for h in ("Node.java", "WeightedEdge.java") if bug_name != h[:-5]]
    out_dir = compile_single_file(ref_src, bug_name, workdir, extras)
    if out_dir is None:
        return []
    findings = run_spotbugs_security_findings(out_dir)
    shutil.rmtree(workdir, ignore_errors=True)
    return findings


def security_score(bug_name: str, patched_source: str, workdir: Path) -> dict:
    import math

    extras = [QUIXBUGS_DIR / "java_programs" / h for h in ("Node.java", "WeightedEdge.java") if bug_name != h[:-5]]
    out_dir = compile_single_file(patched_source, bug_name, workdir, extras)
    if out_dir is None:
        return {"bug": bug_name, "compiled": False, "findings": [], "ref_findings": [], "score": 0.0}

    findings = run_spotbugs_security_findings(out_dir)
    ref_findings = reference_security_findings(bug_name)
    delta = max(0, len(findings) - len(ref_findings))
    score = math.exp(-DECAY_RATE * delta)
    return {
        "bug": bug_name,
        "compiled": True,
        "findings": findings,
        "ref_findings": ref_findings,
        "delta": delta,
        "score": score,
    }


DEFECTS4J_SECURITY_BASELINE_DIR = REPO_ROOT / "data" / "defects4j_security_baseline"


def defects4j_class_findings(pid: str, bid: str, timeout: int = 60) -> list[dict]:
    """Assumes the container's current compiled state (target/classes) is
    already what the caller wants scored (e.g. right after
    defects4j_harness.run_patch()). Copies the compiled class(es) out and
    scans them - no recompilation here, since run_patch already did it and
    Defects4J compiles are the slow part of this pipeline at 500-patch scale."""
    from defects4j_harness import copy_compiled_classes

    with tempfile.TemporaryDirectory() as tmp:
        class_dir = copy_compiled_classes(pid, bid, Path(tmp))
        return run_spotbugs_security_findings(class_dir, timeout=timeout)


def defects4j_reference_findings(pid: str, bid: str) -> list[dict]:
    """Findings on the officially upstream-fixed version, cached per bug
    since it's constant across all 5 models' patch attempts."""
    from defects4j_harness import run_patch, restore_original, _docker_exec, checkout_and_cache_metadata

    cache_path = DEFECTS4J_SECURITY_BASELINE_DIR / f"{pid}_{bid}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    meta = checkout_and_cache_metadata(pid, bid)
    fixed_source = _docker_exec(
        f"cd {meta['workdir']} && git show D4J_{pid}_{bid}_FIXED_VERSION:{meta['rel_path']}", timeout=60
    ).stdout
    r = run_patch(pid, bid, fixed_source, timeout=300)
    findings = defects4j_class_findings(pid, bid) if r["compiled"] else []
    restore_original(pid, bid)

    DEFECTS4J_SECURITY_BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(findings, indent=2), encoding="utf-8")
    return findings


def defects4j_security_score(pid: str, bid: str, already_compiled: bool = True) -> dict:
    """Scores the CURRENTLY-compiled patch in the container (caller should
    have already run defects4j_harness.run_patch() with the candidate patch
    right before calling this, so we don't recompile twice)."""
    import math

    if not already_compiled:
        raise ValueError("caller must compile the patch first via defects4j_harness.run_patch()")

    findings = defects4j_class_findings(pid, bid)
    ref_findings = defects4j_reference_findings(pid, bid)
    delta = max(0, len(findings) - len(ref_findings))
    score = math.exp(-DECAY_RATE * delta)
    return {"pid": pid, "bid": bid, "findings": findings, "ref_findings": ref_findings, "delta": delta, "score": score}


if __name__ == "__main__":
    from quixbugs_harness import list_bugs

    scratch = REPO_ROOT / "scripts" / "_scratch_security_selftest"
    bugs = list_bugs()
    total_ref_findings = 0
    errors = 0
    print(f"Running SpotBugs+FindSecBugs self-test on {len(bugs)} reference-correct QuixBugs programs...")
    for bug in bugs:
        try:
            findings = reference_security_findings(bug)
            total_ref_findings += len(findings)
            if findings:
                print(f"  {bug}: {len(findings)} SECURITY finding(s): {[f['type'] for f in findings]}")
        except Exception as e:
            errors += 1
            print(f"  [ERROR] {bug}: {e}")
    shutil.rmtree(scratch, ignore_errors=True)
    shutil.rmtree(REPO_ROOT / "scripts" / "_scratch_security_ref", ignore_errors=True)
    print(f"\nRan cleanly on {len(bugs) - errors}/{len(bugs)} bugs.")
    print(f"Total SECURITY findings across all 40 reference programs: {total_ref_findings}")
    print("(0 or near-0 is EXPECTED here - see module docstring. This confirms the tool runs;")
    print(" it does not confirm discriminative power, which is Defects4J's job.)")
