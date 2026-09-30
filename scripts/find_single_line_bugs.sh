#!/bin/bash
# Runs inside the defects4j container. For every bug in every project, diffs
# the buggy vs fixed revision on non-test .java files and reports the total
# added+removed line count, so we can select bugs whose source-level fix is
# a single line (excludes test files - we care about the size of the SOURCE
# fix, not any regression tests bundled into the same fix commit).
set -e
OUT=/tmp/all_bug_diffs.csv
echo "pid,bid,buggy_rev,fixed_rev,lines_changed,files_changed" > "$OUT"

for pid in Chart Cli Closure Codec Collections Compress Csv Gson JacksonCore JacksonDatabind JacksonXml Jsoup JxPath Lang Math Mockito Time; do
    csv="/defects4j/framework/projects/${pid}/active-bugs.csv"
    case "$pid" in
        Chart) repo_name="jfreechart" ;;
        Cli) repo_name="commons-cli.git" ;;
        Closure) repo_name="closure-compiler.git" ;;
        Codec) repo_name="commons-codec.git" ;;
        Collections) repo_name="commons-collections.git" ;;
        Compress) repo_name="commons-compress.git" ;;
        Csv) repo_name="commons-csv.git" ;;
        Gson) repo_name="gson.git" ;;
        JacksonCore) repo_name="jackson-core.git" ;;
        JacksonDatabind) repo_name="jackson-databind.git" ;;
        JacksonXml) repo_name="jackson-dataformat-xml.git" ;;
        Jsoup) repo_name="jsoup.git" ;;
        JxPath) repo_name="commons-jxpath.git" ;;
        Lang) repo_name="commons-lang.git" ;;
        Math) repo_name="commons-math.git" ;;
        Mockito) repo_name="mockito.git" ;;
        Time) repo_name="joda-time.git" ;;
    esac
    repo="/defects4j/project_repos/${repo_name}"
    if [ ! -d "$repo" ]; then
        echo "WARN: no repo dir for $pid ($repo)" >&2
        continue
    fi

    tail -n +2 "$csv" | while IFS=',' read -r bid buggy fixed rest; do
        cd "$repo"
        lines=$(git diff --numstat "$buggy" "$fixed" -- '*.java' 2>/dev/null | grep -viE '(^|/)test|Test[^/]*\.java' | awk '{added+=$1; removed+=$2} END {print added+removed}')
        nfiles=$(git diff --numstat "$buggy" "$fixed" -- '*.java' 2>/dev/null | grep -viE '(^|/)test|Test[^/]*\.java' | wc -l)
        echo "${pid},${bid},${buggy},${fixed},${lines:-0},${nfiles:-0}" >> "$OUT"
    done
    echo "done: $pid" >&2
done
echo "Wrote $OUT"
