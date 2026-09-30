"""
Compiles a single candidate patch for a QuixBugs Java bug against its official
JUnit test class and reports pass/fail counts.

Each QuixBugs Java program is a single self-contained class in package
`java_programs`; its test lives in `java_testcases.junit` and imports
`java_programs.<NAME>` directly (confirmed by inspecting BITCOUNT_TEST.java).
So a patch only needs the target file + its test file to compile - no need to
pull in the other 41 programs.
"""
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
QUIXBUGS_DIR = REPO_ROOT / "data" / "quixbugs_raw"
JARS_DIR = REPO_ROOT / "tools" / "jars"
CLASSPATH = f"{JARS_DIR / 'junit-4.12.jar'};{JARS_DIR / 'hamcrest-core-1.3.jar'}"

def _find_jdk_bin(exe_name: str) -> str:
    """This project's shells have a stale PATH from before the JDK was
    installed (see project notes), so resolve explicitly instead of relying
    on PATH lookup."""
    import os
    import shutil as _shutil

    found = _shutil.which(exe_name)
    if found:
        return found
    java_home = os.environ.get("JAVA_HOME")
    if java_home and (Path(java_home) / "bin" / f"{exe_name}.exe").exists():
        return str(Path(java_home) / "bin" / f"{exe_name}.exe")
    known = Path("C:/Program Files/Eclipse Adoptium/jdk-17.0.20.101-hotspot/bin") / f"{exe_name}.exe"
    if known.exists():
        return str(known)
    raise FileNotFoundError(f"could not locate {exe_name}; set JAVA_HOME")


JAVAC = _find_jdk_bin("javac")
JAVA = _find_jdk_bin("java")


@dataclass
class TestResult:
    compiled: bool
    total: int = 0
    failed: int = 0
    error: str = ""

    @property
    def passed(self) -> int:
        return self.total - self.failed

    @property
    def all_passed(self) -> bool:
        return self.compiled and self.total > 0 and self.failed == 0


def list_bugs() -> list[str]:
    """The 42 files under java_programs/ include 2 shared helper classes
    (Node, WeightedEdge) that have no _TEST.java and aren't bugs themselves -
    filtering on "has a test file" cleanly yields the real 40 bugs."""
    all_names = sorted(p.stem for p in (QUIXBUGS_DIR / "java_programs").glob("*.java"))
    test_dir = QUIXBUGS_DIR / "java_testcases" / "junit"
    return [n for n in all_names if (test_dir / f"{n}_TEST.java").exists()]


def run_quixbugs_bug(bug_name: str, patched_source: str, workdir: Path, timeout: int = 30) -> TestResult:
    """patched_source must be the full content of java_programs/<bug_name>.java
    (i.e. keep the `package java_programs;` line and class signature intact)."""
    if workdir.exists():
        shutil.rmtree(workdir)
    src_dir = workdir / "java_programs"
    test_dir = workdir / "java_testcases" / "junit"
    out_dir = workdir / "out"
    src_dir.mkdir(parents=True)
    test_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)

    (src_dir / f"{bug_name}.java").write_text(patched_source, encoding="utf-8")

    # Node.java and WeightedEdge.java are shared helper classes (used via
    # same-package visibility by e.g. BREADTH_FIRST_SEARCH, MINIMUM_SPANNING_TREE)
    # and QuixFixOracleHelper.java is shared by 19 test files - none are part
    # of any single bug's file but all are required on the compile path.
    # Always include them, harmless if unused.
    for helper in ("Node.java", "WeightedEdge.java"):
        if bug_name != helper[:-5]:
            shutil.copy(QUIXBUGS_DIR / "java_programs" / helper, src_dir / helper)
    oracle_src = QUIXBUGS_DIR / "java_testcases" / "junit" / "QuixFixOracleHelper.java"
    shutil.copy(oracle_src, test_dir / "QuixFixOracleHelper.java")

    test_src = QUIXBUGS_DIR / "java_testcases" / "junit" / f"{bug_name}_TEST.java"
    if not test_src.exists():
        return TestResult(compiled=False, error=f"no test file for {bug_name}")
    shutil.copy(test_src, test_dir / f"{bug_name}_TEST.java")

    compile_cmd = [
        JAVAC, "-cp", CLASSPATH, "-d", str(out_dir),
        str(src_dir / f"{bug_name}.java"),
        str(src_dir / "Node.java"),
        str(src_dir / "WeightedEdge.java"),
        str(test_dir / "QuixFixOracleHelper.java"),
        str(test_dir / f"{bug_name}_TEST.java"),
    ]
    try:
        proc = subprocess.run(compile_cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return TestResult(compiled=False, error="compile timeout")
    if proc.returncode != 0:
        return TestResult(compiled=False, error=proc.stdout + proc.stderr)

    run_cmd = [
        JAVA, "-cp", f"{out_dir};{CLASSPATH}",
        "org.junit.runner.JUnitCore", f"java_testcases.junit.{bug_name}_TEST",
    ]
    try:
        proc = subprocess.run(run_cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return TestResult(compiled=True, total=0, failed=0, error="test run timeout (likely infinite loop)")

    out = proc.stdout + proc.stderr
    if "OK (" in out:
        m = re.search(r"OK \((\d+) tests?\)", out)
        total = int(m.group(1)) if m else 0
        return TestResult(compiled=True, total=total, failed=0)
    m = re.search(r"Tests run: (\d+),\s*Failures: (\d+)", out)
    if m:
        return TestResult(compiled=True, total=int(m.group(1)), failed=int(m.group(2)))
    return TestResult(compiled=True, total=0, failed=0, error=f"unparsed output: {out[:500]}")


if __name__ == "__main__":
    # Self-check: correct reference implementations should pass 100%,
    # and the original buggy versions should mostly fail. If this doesn't
    # hold, the harness itself is broken and nothing downstream can be trusted.
    scratch = REPO_ROOT / "scripts" / "_scratch_harness_selftest"
    bugs = list_bugs()
    print(f"{len(bugs)} Java bugs found")

    correct_pass, correct_total = 0, 0
    buggy_fail, buggy_total = 0, 0
    for bug in bugs:
        correct_src = (QUIXBUGS_DIR / "correct_java_programs" / f"{bug}.java").read_text(encoding="utf-8")
        correct_src = correct_src.replace("package correct_java_programs;", "package java_programs;")
        r = run_quixbugs_bug(bug, correct_src, scratch)
        correct_total += 1
        if r.all_passed:
            correct_pass += 1
        else:
            print(f"  [UNEXPECTED] correct/{bug} did not fully pass: compiled={r.compiled} {r.passed}/{r.total} err={r.error[:200]}")

        buggy_src = (QUIXBUGS_DIR / "java_programs" / f"{bug}.java").read_text(encoding="utf-8")
        r2 = run_quixbugs_bug(bug, buggy_src, scratch)
        buggy_total += 1
        if not r2.all_passed:
            buggy_fail += 1
        else:
            print(f"  [NOTE] buggy/{bug} passed all tests anyway (weak test suite) {r2.passed}/{r2.total}")

    shutil.rmtree(scratch, ignore_errors=True)
    print(f"\nCorrect programs fully passing: {correct_pass}/{correct_total}")
    print(f"Buggy programs correctly caught as failing: {buggy_fail}/{buggy_total}")
