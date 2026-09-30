"""
Drives the running Defects4J 2.0.0 Docker container (name: `d4j`) to
checkout, patch, compile, and test candidate fixes for the 100 selected
small-diff real-world bugs (see select_defects4j_bugs.py).

Unlike QuixBugs (self-contained single-file compiles), Defects4J bugs live
inside full real-world Maven/Ant projects, so each bug needs an actual
`defects4j checkout` into its own working directory inside the container.
Checkout + metadata export happens once per bug and is cached to a local
JSON file (data/defects4j_meta/), reused across all 5 models' patch
attempts - only the single modified source file is swapped and recompiled
per patch, avoiding a repeated multi-second `defects4j export` round-trip
and letting the ant incremental build stay warm.

Correctness is judged by trigger tests only (the tests that fail on the
buggy revision and pass on the fixed revision - Defects4J's own bug
reproduction oracle), not the full project test suite. This is the
standard, tractable choice at this dataset's scale: a full-suite run on a
project like Closure can take minutes, and doing that for every one of
500 (bug, model) patch attempts would blow the project's time budget. This
is a documented scope choice: a patch that fixes the trigger test but
breaks an unrelated test would be (incorrectly) scored as passing - noted
as a limitation in the paper, mirroring the same tradeoff pass@1 made for
functional correctness generally.
"""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
META_DIR = REPO_ROOT / "data" / "defects4j_meta"
CONTAINER = "d4j"
CHECKOUTS_ROOT = "/checkouts"


def _find_docker() -> str:
    found = shutil.which("docker")
    if found:
        return found
    known = Path("C:/Program Files/Docker/Docker/resources/bin/docker.exe")
    if known.exists():
        return str(known)
    raise FileNotFoundError("could not locate docker executable")


DOCKER = _find_docker()


def _docker_exec(cmd: str, timeout: int = 300) -> subprocess.CompletedProcess:
    """Found via a real incident: subprocess.run(..., timeout=) only kills
    the host-side `docker exec` client on timeout - it does NOT propagate
    into the container's PID namespace, so a hung test (e.g. a patch that
    introduces an infinite loop) just keeps running as an orphan forever.
    Several such zombies from hours-old runs were found still consuming
    ~100% CPU each, starving the current scoring pass. Wrapping the inner
    command with the container's own `timeout` ensures the process is
    killed from inside its own namespace regardless of what happens to the
    host-side client. -9 (SIGKILL) since these are JVMs that won't always
    honor SIGTERM promptly."""
    # `cmd` is often compound ("cd X && defects4j test ..."), so timeout has
    # to wrap the whole chain via a nested bash -c, not just the first token.
    wrapped = f"timeout -s KILL {timeout}s bash -c '{cmd}'"
    return subprocess.run(
        [DOCKER, "exec", CONTAINER, "bash", "-c", wrapped],
        capture_output=True, text=True, timeout=timeout + 10,
    )


def workdir_for(pid: str, bid: str) -> str:
    return f"{CHECKOUTS_ROOT}/{pid}_{bid}b"


def _meta_path(pid: str, bid: str) -> Path:
    return META_DIR / f"{pid}_{bid}.json"


def checkout_and_cache_metadata(pid: str, bid: str, force: bool = False) -> dict:
    """Checks out the buggy version (if not already done) and caches the
    modified-file path + trigger tests to a local JSON file. Safe to call
    repeatedly - skips work that's already done unless force=True."""
    meta_path = _meta_path(pid, bid)
    if meta_path.exists() and not force:
        return json.loads(meta_path.read_text(encoding="utf-8"))

    wd = workdir_for(pid, bid)
    exists = _docker_exec(f"test -d {wd} && echo yes || echo no").stdout.strip() == "yes"
    if not exists:
        r = _docker_exec(f"mkdir -p {CHECKOUTS_ROOT} && defects4j checkout -p {pid} -v {bid}b -w {wd}", timeout=300)
        if "Check out program version" not in (r.stdout + r.stderr):
            raise RuntimeError(f"checkout failed for {pid}-{bid}: {r.stdout}\n{r.stderr}")

    fqcn = _docker_exec(f"defects4j export -p classes.modified -w {wd}", timeout=120).stdout.strip().splitlines()[-1]
    src_dir = _docker_exec(f"defects4j export -p dir.src.classes -w {wd}", timeout=120).stdout.strip().splitlines()[-1]
    bin_dir = _docker_exec(f"defects4j export -p dir.bin.classes -w {wd}", timeout=120).stdout.strip().splitlines()[-1]
    triggers_raw = _docker_exec(f"defects4j export -p tests.trigger -w {wd}", timeout=120).stdout.strip().splitlines()
    triggers = [line for line in triggers_raw if "::" in line]
    rel_path = f"{src_dir}/{fqcn.replace('.', '/')}.java"
    class_rel_path = f"{bin_dir}/{fqcn.replace('.', '/')}.class"

    meta = {
        "pid": pid, "bid": bid, "workdir": wd, "fqcn": fqcn,
        "rel_path": rel_path, "abs_path": f"{wd}/{rel_path}",
        "class_abs_path": f"{wd}/{class_rel_path}",
        "trigger_tests": triggers,
    }
    META_DIR.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def get_buggy_source(pid: str, bid: str) -> str:
    """Reads the original buggy source of the modified file, for prompt
    construction. Safe to call after run_patch() has overwritten the file
    in-container, since it re-reads via `git show` of the tagged buggy
    revision rather than the (possibly patched) working tree."""
    meta = checkout_and_cache_metadata(pid, bid)
    r = _docker_exec(
        f"cd {meta['workdir']} && git show D4J_{pid}_{bid}_BUGGY_VERSION:{meta['rel_path']}",
        timeout=60,
    )
    if r.returncode != 0:
        raise RuntimeError(f"could not read buggy source for {pid}-{bid}: {r.stderr}")
    return r.stdout


def run_patch(pid: str, bid: str, patched_source: str, timeout: int = 300) -> dict:
    """Overwrites the modified file with patched_source, compiles, and runs
    only this bug's trigger tests. Returns compiled flag + per-trigger-test
    pass/fail + raw output for debugging."""
    meta = checkout_and_cache_metadata(pid, bid)

    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / "patch.java"
        local.write_text(patched_source, encoding="utf-8")
        cp = subprocess.run([DOCKER, "cp", str(local), f"{CONTAINER}:{meta['abs_path']}"],
                             capture_output=True, text=True)
        if cp.returncode != 0:
            return {"compiled": False, "error": f"docker cp failed: {cp.stderr}", "trigger_results": {}}

    compile_r = _docker_exec(f"cd {meta['workdir']} && defects4j compile", timeout=timeout)
    compile_out = compile_r.stdout + compile_r.stderr
    if "BUILD FAILED" in compile_out:
        return {"compiled": False, "error": compile_out[-2000:], "trigger_results": {}}

    trigger_results = {}
    for test in meta["trigger_tests"]:
        test_r = _docker_exec(f"cd {meta['workdir']} && defects4j test -t {test}", timeout=timeout)
        out = test_r.stdout + test_r.stderr
        # "Failing tests: 0" -> pass. Any other "Failing tests: N>0" -> fail.
        # (Defects4J prints this exact line for -t single-test runs too.)
        passed = "Failing tests: 0" in out
        trigger_results[test] = passed

    return {
        "compiled": True,
        "trigger_results": trigger_results,
        "all_trigger_tests_pass": all(trigger_results.values()) if trigger_results else False,
    }


def get_patch_scope(pid: str, bid: str) -> dict:
    """Locates the method(s) containing the bug's fix, using the git diff
    hunk headers (exact changed line numbers in the buggy file) plus
    `lizard` (method start/end line boundaries). Falls back to the whole
    file if no enclosing method is found (e.g. a field/annotation-level
    change, which is rare for the small diffs in our selected set).

    This exists because Defects4J files are real-world classes - median 882
    lines, max 5837, across our 100 selected bugs - and asking a small
    local model to retype an entire multi-thousand-line file to fix 1-3
    lines would fail for reasons unrelated to actual repair skill
    (transcription drift, generation-length limits), not because it
    couldn't fix the bug.
    """
    import lizard

    meta = checkout_and_cache_metadata(pid, bid)
    full_source = get_buggy_source(pid, bid)

    diff_r = _docker_exec(
        f"cd {meta['workdir']} && git diff -U0 D4J_{pid}_{bid}_BUGGY_VERSION D4J_{pid}_{bid}_FIXED_VERSION -- {meta['rel_path']}",
        timeout=60,
    )
    anchors = []
    for line in diff_r.stdout.splitlines():
        if line.startswith("@@"):
            old_part = line.split("@@")[1].strip().split(" ")[0].lstrip("-")  # "start[,count]"
            parts = old_part.split(",")
            old_start = int(parts[0])
            old_count = int(parts[1]) if len(parts) > 1 else 1
            anchors.append(max(1, old_start if old_count > 0 else old_start + 1))

    result = lizard.analyze_file.analyze_source_code(f"{pid}_{bid}.java", full_source)
    touched = []
    for anchor in anchors:
        for f in result.function_list:
            if f.start_line <= anchor <= f.end_line:
                touched.append(f)
                break

    if not touched:
        return {
            "start_line": 1, "end_line": len(full_source.splitlines()),
            "region_source": full_source, "whole_file": True, "method_names": [],
        }

    start_line = min(f.start_line for f in touched)
    end_line = max(f.end_line for f in touched)
    lines = full_source.splitlines(keepends=True)
    region_source = "".join(lines[start_line - 1:end_line])

    return {
        "start_line": start_line, "end_line": end_line,
        "region_source": region_source, "whole_file": False,
        "method_names": sorted(set(f.name for f in touched)),
    }


def reconstruct_full_source(pid: str, bid: str, scope: dict, patched_region_source: str) -> str:
    """Splices a model's fixed method(s) back into the full original buggy
    file, using the same scope dict get_patch_scope() returned."""
    if scope.get("whole_file"):
        return patched_region_source
    full_source = get_buggy_source(pid, bid)
    lines = full_source.splitlines(keepends=True)
    before = "".join(lines[:scope["start_line"] - 1])
    after = "".join(lines[scope["end_line"]:])
    patched = patched_region_source if patched_region_source.endswith("\n") else patched_region_source + "\n"
    return before + patched + after


def copy_compiled_classes(pid: str, bid: str, local_dir: Path) -> Path:
    """Copies the modified class's compiled .class file(s) (including any
    inner/anonymous classes, e.g. Util$1.class) out of the container into
    local_dir, preserving the package directory structure so it can be
    pointed at directly as a SpotBugs analysis target. Assumes `defects4j
    compile` has already been run in-container (e.g. via run_patch)."""
    meta = checkout_and_cache_metadata(pid, bid)
    class_path = Path(meta["class_abs_path"])
    class_dir_in_container = str(class_path.parent).replace("\\", "/")
    simple_name = class_path.stem  # e.g. "Util"

    pkg_rel_dir = Path(meta["fqcn"].replace(".", "/")).parent
    local_pkg_dir = local_dir / pkg_rel_dir
    local_pkg_dir.mkdir(parents=True, exist_ok=True)

    find_r = _docker_exec(f"find {class_dir_in_container} -maxdepth 1 -name '{simple_name}*.class'", timeout=30)
    class_files = [line for line in find_r.stdout.strip().splitlines() if line]
    for container_path in class_files:
        fname = container_path.rsplit("/", 1)[-1]
        subprocess.run([DOCKER, "cp", f"{CONTAINER}:{container_path}", str(local_pkg_dir / fname)],
                        capture_output=True, text=True, check=True)
    return local_dir


def restore_original(pid: str, bid: str) -> None:
    """Resets the modified file back to the buggy original in the container
    working tree (each defects4j checkout has its own local git repo)."""
    meta = checkout_and_cache_metadata(pid, bid)
    _docker_exec(f"cd {meta['workdir']} && git checkout -- {meta['rel_path']}", timeout=30)


if __name__ == "__main__":
    # Self-check on Cli-5, already manually validated above: buggy source
    # should compile+fail its trigger tests, and the *actual* upstream fix
    # (fetched from the fixed revision) should compile+pass them.
    pid, bid = "Cli", "5"
    meta = checkout_and_cache_metadata(pid, bid)
    print("metadata:", json.dumps(meta, indent=2))

    buggy_src = get_buggy_source(pid, bid)
    print(f"\nbuggy source ({len(buggy_src)} chars) starts with:\n{buggy_src[:200]}")

    r_buggy = run_patch(pid, bid, buggy_src)
    print(f"\nre-applying the ORIGINAL buggy source: compiled={r_buggy['compiled']}, "
          f"trigger_results={r_buggy.get('trigger_results')}")
    assert r_buggy["compiled"] and not r_buggy["all_trigger_tests_pass"], "buggy source should fail its trigger tests"

    fixed_src_r = _docker_exec(f"cd {meta['workdir']} && git show D4J_{pid}_{bid}_FIXED_VERSION:{meta['rel_path']}")
    fixed_src = fixed_src_r.stdout
    r_fixed = run_patch(pid, bid, fixed_src)
    print(f"applying the real upstream FIX: compiled={r_fixed['compiled']}, "
          f"trigger_results={r_fixed.get('trigger_results')}")
    assert r_fixed["compiled"] and r_fixed["all_trigger_tests_pass"], "real fix should pass its trigger tests"

    restore_original(pid, bid)
    print("\nOK: harness correctly distinguishes buggy vs fixed on Cli-5")
