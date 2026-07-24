#!/usr/bin/env python3
import argparse
import fnmatch
import json
import os
import subprocess
import sys
from pathlib import PurePosixPath

import yaml


PASS = 0
BLOCKED = 1
APPROVAL_REQUIRED = 2
DEFAULT_BROAD_SCOPE_THRESHOLD_PERCENT = 80
ROOT_SCOPE_GLOBS = {"*", "**"}


def normalize_path(path):
    return str(PurePosixPath(path.replace("\\", "/")))


def match_glob(path, pattern):
    path_parts = [part for part in normalize_path(path).split("/") if part]
    pattern_parts = [part for part in normalize_path(pattern).split("/") if part]

    def match_parts(path_index, pattern_index):
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)

        pattern_part = pattern_parts[pattern_index]
        if pattern_part == "**":
            if pattern_index == len(pattern_parts) - 1:
                return True
            for next_path_index in range(path_index, len(path_parts) + 1):
                if match_parts(next_path_index, pattern_index + 1):
                    return True
            return False

        if path_index >= len(path_parts):
            return False
        if not fnmatch.fnmatchcase(path_parts[path_index], pattern_part):
            return False
        return match_parts(path_index + 1, pattern_index + 1)

    return match_parts(0, 0)


def load_intent(path):
    if not os.path.exists(path):
        raise FileNotFoundError("의도 선언 누락: change-intent.yaml 파일을 찾을 수 없습니다.")

    with open(path, "r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream) or {}

    intent = data.get("change_intent") or {}
    return {
        "allowed_paths": intent.get("allowed_paths") or [],
        "forbidden_paths": intent.get("forbidden_paths") or [],
        "expected_paths": intent.get("expected_paths") or [],
        "broad_scope_threshold_percent": DEFAULT_BROAD_SCOPE_THRESHOLD_PERCENT,
        "requirement_id": intent.get("requirement_id"),
        "author": intent.get("author"),
    }


def read_name_status(diff_input):
    if os.path.exists(diff_input):
        with open(diff_input, "r", encoding="utf-8") as stream:
            return stream.read().splitlines()

    result = subprocess.run(
        ["git", "diff", "--name-status", diff_input],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git diff 실행 실패")
    return result.stdout.splitlines()


def parse_changed_files(name_status_lines):
    files = []
    for line in name_status_lines:
        line = line.strip()
        if not line:
            continue

        parts = line.split("\t")
        status = parts[0]
        if status.startswith(("R", "C")) and len(parts) >= 3:
            path = parts[2]
        elif len(parts) >= 2:
            path = parts[1]
        else:
            continue
        files.append(normalize_path(path))
    return files


def diff_base_ref(diff_input):
    if os.path.exists(diff_input):
        return "HEAD"
    if "..." in diff_input:
        return diff_input.split("...", 1)[0] or "HEAD"
    if ".." in diff_input:
        return diff_input.split("..", 1)[0] or "HEAD"
    return "HEAD"


def repo_top_level_dirs(diff_input, files):
    base_ref = diff_base_ref(diff_input)
    result = subprocess.run(
        ["git", "ls-tree", "-d", "--name-only", base_ref],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode == 0:
        dirs = [normalize_path(line.strip()) for line in result.stdout.splitlines() if line.strip()]
        if dirs:
            return sorted(set(dirs))

    return sorted({path.split("/", 1)[0] for path in files if "/" in path})


def normalize_scope_glob(pattern):
    normalized = normalize_path(pattern).strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.strip("/")


def root_scope_globs(allowed_paths):
    return sorted(
        pattern
        for pattern in allowed_paths
        if normalize_scope_glob(pattern) in ROOT_SCOPE_GLOBS
    )


def covered_top_level_dirs(allowed_paths, top_level_dirs):
    covered = []
    for directory in top_level_dirs:
        probe_path = f"{directory}/__scope_probe__"
        for pattern in allowed_paths:
            if match_glob(directory, pattern) or match_glob(probe_path, pattern):
                covered.append(directory)
                break
    return sorted(covered)


def broad_scope_result(files, intent, diff_input):
    empty = {
        "too_broad": False,
        "reasons": [],
        "root_globs": [],
        "covered_top_level_dirs": [],
        "top_level_dir_count": 0,
        "coverage_percent": 0,
        "threshold_percent": intent["broad_scope_threshold_percent"],
    }
    if not files:
        return empty

    allowed_paths = intent["allowed_paths"]
    top_level_dirs = repo_top_level_dirs(diff_input, files)
    roots = root_scope_globs(allowed_paths)
    covered = covered_top_level_dirs(allowed_paths, top_level_dirs)
    coverage_percent = 0
    if top_level_dirs:
        coverage_percent = int((len(covered) * 100) / len(top_level_dirs))

    reasons = []
    if roots:
        reasons.append("root_scope_glob")
    if top_level_dirs and coverage_percent >= intent["broad_scope_threshold_percent"]:
        reasons.append("top_level_coverage")

    return {
        "too_broad": bool(reasons),
        "reasons": reasons,
        "root_globs": roots,
        "covered_top_level_dirs": covered,
        "top_level_dir_count": len(top_level_dirs),
        "coverage_percent": coverage_percent,
        "threshold_percent": intent["broad_scope_threshold_percent"],
    }


def check_files(files, intent, diff_input):
    allowed_paths = intent["allowed_paths"]
    forbidden_paths = intent["forbidden_paths"]
    expected_paths = intent["expected_paths"]
    scope_too_broad = broad_scope_result(files, intent, diff_input)

    forbidden_touched = []
    out_of_scope = []
    missing_expected = sorted(
        pattern
        for pattern in expected_paths
        if not any(match_glob(path, pattern) for path in files)
    )

    for path in files:
        in_forbidden = any(match_glob(path, pattern) for pattern in forbidden_paths)
        in_allowed = any(match_glob(path, pattern) for pattern in allowed_paths)

        if in_forbidden:
            forbidden_touched.append(path)
        elif not in_allowed:
            out_of_scope.append(path)

    if forbidden_touched:
        verdict = "blocked"
        exit_code = BLOCKED
    elif out_of_scope or scope_too_broad["too_broad"] or missing_expected:
        verdict = "approval_required"
        exit_code = APPROVAL_REQUIRED
    else:
        verdict = "pass"
        exit_code = PASS

    return {
        "gate": "check-change-intent",
        "verdict": verdict,
        "changed_files": files,
        "out_of_scope_paths": out_of_scope,
        "forbidden_touched": forbidden_touched,
        "expected_paths": expected_paths,
        "missing_expected": missing_expected,
        "scope_too_broad": scope_too_broad,
        "exit_code": exit_code,
    }


def print_text(result):
    verdict = result["verdict"]
    if verdict == "pass":
        print("PASS: 변경 파일이 change-intent allowed_paths 범위 안에 있습니다.")
    elif verdict == "blocked":
        print("BLOCKED: forbidden_paths 변경이 감지되었습니다.")
    else:
        print("APPROVAL_REQUIRED: 변경 의도 확인이 필요합니다.")

    if not result["changed_files"]:
        print("changed_files: 0")
    for path in result["forbidden_touched"]:
        print(f"forbidden: {path}")
    for path in result["out_of_scope_paths"]:
        print(f"out_of_scope: {path}")
    for pattern in result["missing_expected"]:
        print(f"missing_expected: {pattern}")
    if result["expected_paths"]:
        print(
            "expected_paths_semantics: 각 항목은 변경 파일 중 1개 이상 매칭 시 충족; "
            "리터럴 경로 권장(glob은 거친 보증); rename은 목적지, delete는 경로 변경으로만 판정"
        )
    if result["scope_too_broad"]["too_broad"]:
        reasons = ",".join(result["scope_too_broad"]["reasons"])
        print(f"scope_too_broad: {reasons}")


def main():
    parser = argparse.ArgumentParser(
        description="Check whether changed files stay within change-intent path scope."
    )
    parser.add_argument("diff_input", help="git diff ref (base..head) or name-status file")
    parser.add_argument("change_intent", help="change-intent.yaml path")
    parser.add_argument("--json", action="store_true", help="print structured JSON")
    args = parser.parse_args()

    try:
        intent = load_intent(args.change_intent)
        files = parse_changed_files(read_name_status(args.diff_input))
        result = check_files(files, intent, args.diff_input)
    except FileNotFoundError as error:
        result = {
            "gate": "check-change-intent",
            "verdict": "blocked",
            "error": str(error),
            "exit_code": BLOCKED,
        }
    except Exception as error:
        result = {
            "gate": "check-change-intent",
            "verdict": "blocked",
            "error": str(error),
            "exit_code": BLOCKED,
        }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    elif "error" in result:
        print(f"BLOCKED: {result['error']}")
    else:
        print_text(result)

    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
