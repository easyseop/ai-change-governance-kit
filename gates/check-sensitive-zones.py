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
VALID_MATURITY = {"enforcing", "shadow"}


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


def load_policy(path):
    with open(path, "r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream) or {}

    defaults = data.get("defaults") or {}
    zones = []
    errors = []
    for index, zone in enumerate(data.get("zones") or []):
        normalized = dict(zone)
        maturity = normalized.get("maturity", "enforcing")
        if maturity not in VALID_MATURITY:
            errors.append(
                {
                    "error": "invalid_maturity",
                    "index": index,
                    "path": normalized.get("path"),
                    "maturity": maturity,
                }
            )
            maturity = "enforcing"
        normalized["maturity"] = maturity
        zones.append(normalized)

    return {
        "block_levels": defaults.get("block_levels") or [],
        "approve_levels": defaults.get("approve_levels") or [],
        "warn_levels": defaults.get("warn_levels") or [],
        "unlisted_level": defaults.get("unlisted_level") or "free",
        "zones": zones,
        "errors": errors,
    }


def level_strength(level, policy):
    if level in policy["block_levels"]:
        return 3
    if level in policy["approve_levels"]:
        return 2
    if level in policy["warn_levels"]:
        return 1
    return 0


def matching_zone_records(path, policy):
    records = []
    for zone in policy["zones"]:
        pattern = zone.get("path")
        if not pattern or not match_glob(path, pattern):
            continue
        level = zone.get("level", policy["unlisted_level"])
        records.append(
            {
                "path": path,
                "zone": normalize_path(pattern),
                "level": level,
                "reason": zone.get("reason", ""),
                "required_approval": zone.get("required_approval"),
                "maturity": zone.get("maturity", "enforcing"),
                "strength": level_strength(level, policy),
            }
        )
    return records


def strongest_records(records):
    if not records:
        return []
    max_strength = max(record["strength"] for record in records)
    strongest = [record for record in records if record["strength"] == max_strength]
    return sorted(strongest, key=lambda record: (record["path"], record["zone"], record["reason"]))


def public_record(record):
    result = {
        "path": record["path"],
        "zone": record["zone"],
        "level": record["level"],
        "reason": record["reason"],
    }
    if record["required_approval"]:
        result["required_approval"] = record["required_approval"]
    if record.get("maturity") == "shadow":
        result["maturity"] = "shadow"
    return result


def check_files(files, policy):
    frozen_touched = []
    protected_touched = []
    watched_touched = []
    shadow_hits = []

    for path in files:
        records = matching_zone_records(path, policy)
        for record in strongest_records(
            [record for record in records if record.get("maturity") == "shadow"]
        ):
            shadow_hits.append(public_record(record))
        for record in strongest_records(
            [record for record in records if record.get("maturity") != "shadow"]
        ):
            public = public_record(record)
            level = record["level"]
            if level in policy["block_levels"]:
                frozen_touched.append(public)
            elif level in policy["approve_levels"]:
                protected_touched.append(public)
            elif level in policy["warn_levels"]:
                watched_touched.append(public)

    frozen_touched = sorted(frozen_touched, key=lambda item: (item["path"], item["zone"]))
    protected_touched = sorted(protected_touched, key=lambda item: (item["path"], item["zone"]))
    watched_touched = sorted(watched_touched, key=lambda item: (item["path"], item["zone"]))
    shadow_hits = sorted(shadow_hits, key=lambda item: (item["path"], item["zone"]))

    if frozen_touched:
        verdict = "blocked"
        exit_code = BLOCKED
    elif protected_touched:
        verdict = "approval_required"
        exit_code = APPROVAL_REQUIRED
    else:
        verdict = "pass"
        exit_code = PASS

    return {
        "gate": "check-sensitive-zones",
        "verdict": verdict,
        "changed_files": files,
        "frozen_touched": frozen_touched,
        "protected_touched": protected_touched,
        "watched_touched": watched_touched,
        "shadow_hits": shadow_hits,
        "errors": policy.get("errors", []),
        "exit_code": exit_code,
    }


def print_text(result):
    verdict = result["verdict"]
    if verdict == "pass":
        if result["watched_touched"]:
            print("PASS: watched 민감 경로 변경이 감지되었습니다.")
        else:
            print("PASS: 민감 경로 변경이 감지되지 않았습니다.")
    elif verdict == "blocked":
        print("BLOCKED: frozen 민감 경로 변경이 감지되었습니다.")
    else:
        print("APPROVAL_REQUIRED: protected 민감 경로 변경이 감지되었습니다.")

    if not result["changed_files"]:
        print("changed_files: 0")
    for item in result["frozen_touched"]:
        print(f"frozen: {item['path']} ({item['reason']})")
    for item in result["protected_touched"]:
        approval = item.get("required_approval", "")
        suffix = f", required_approval={approval}" if approval else ""
        print(f"protected: {item['path']} ({item['reason']}{suffix})")
    for item in result["watched_touched"]:
        print(f"watched: {item['path']} ({item['reason']})")
    for item in result.get("shadow_hits", []):
        print(f"shadow: {item['path']} ({item['level']}, {item['reason']})")
    for error in result.get("errors", []):
        print(f"error: {error}")


def main():
    parser = argparse.ArgumentParser(
        description="Check whether changed files touch sensitive path zones."
    )
    parser.add_argument("diff_input", help="git diff ref (base..head) or name-status file")
    parser.add_argument("sensitive_zones", help="policies/sensitive-zones.yaml path")
    parser.add_argument("--json", action="store_true", help="print structured JSON")
    args = parser.parse_args()

    try:
        policy = load_policy(args.sensitive_zones)
        files = parse_changed_files(read_name_status(args.diff_input))
        result = check_files(files, policy)
    except Exception as error:
        result = {
            "gate": "check-sensitive-zones",
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
