#!/usr/bin/env python3
import argparse
import fnmatch
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from pathlib import PurePosixPath

import yaml


PASS = 0
BLOCKED = 1
APPROVAL_REQUIRED = 2
VALID_MATURITY = {"enforcing", "shadow"}


GATE_DIR = Path(__file__).resolve().parent
TOOL_VERSION = "0.2-task019"


NOT_CHECKED_STATEMENTS = [
    "runtime execution paths",
    "unregistered sensitive business logic",
    "cross-commit cumulative risk",
    "non-Python semantic analysis",
    "complete dynamic obfuscation recovery",
]

INTENT_NOT_DECLARED_STATEMENT = (
    "change intent path scope (intent_not_declared: no declaration; scope was not checked)"
)


class IntentNotDeclaredError(FileNotFoundError):
    pass


def load_gate_module(filename, module_name):
    spec = importlib.util.spec_from_file_location(module_name, GATE_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


function_gov_gate = load_gate_module(
    "check-function-gov-level.py", "check_function_gov_level_gate"
)
language_router_gate = load_gate_module("language-router.py", "language_router_gate")


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


def run_git(args, repo="."):
    result = subprocess.run(
        ["git"] + args,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=repo,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result.stdout


def read_diff_lines(diff_input, mode, repo="."):
    if os.path.exists(diff_input):
        with open(diff_input, "r", encoding="utf-8") as stream:
            return stream.read().splitlines()
    return run_git(["diff", mode, diff_input], repo).splitlines()


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


def parse_numstat(numstat_lines):
    files = {}
    for line in numstat_lines:
        line = line.strip()
        if not line:
            continue

        parts = line.split("\t")
        if len(parts) < 3:
            continue

        added = 0 if parts[0] == "-" else int(parts[0])
        removed = 0 if parts[1] == "-" else int(parts[1])
        path = parts[-1]
        if " => " in path:
            path = path.split(" => ", 1)[1].rstrip("}")
        files[normalize_path(path)] = {"added": added, "removed": removed}
    return files


def base_ref_from_diff_input(diff_input):
    if os.path.exists(diff_input):
        return None
    if "..." in diff_input:
        return diff_input.split("...", 1)[0]
    if ".." in diff_input:
        return diff_input.split("..", 1)[0]
    return diff_input


def resolve_base_commit(diff_input, repo="."):
    base_ref = base_ref_from_diff_input(diff_input)
    if not base_ref:
        return "unknown"
    return run_git(["rev-parse", base_ref], repo).strip()


def resolve_generated_on(diff_input, generated_on, repo="."):
    if generated_on:
        return generated_on

    base_ref = base_ref_from_diff_input(diff_input)
    if not base_ref:
        return "1970-01-01"
    return run_git(["show", "-s", "--format=%cs", base_ref], repo).strip()


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def policy_sha(paths):
    return {
        Path(path).name: file_sha256(path)
        for path in sorted(paths, key=lambda item: str(item))
    }


def language_coverage(diff_input, language_routing, repo):
    if not language_routing:
        return {
            "checked": [],
            "not_checked": [],
            "policy_path": None,
            "errors": [],
        }
    if not os.path.exists(language_routing):
        message = f"language routing policy missing: {language_routing}"
        return {
            "checked": [],
            "not_checked": [message],
            "policy_path": None,
            "errors": [message],
        }
    result = language_router_gate.build_result(diff_input, language_routing, repo)
    checked = []
    not_checked = []
    for item in result.get("coverage", {}).get("stubbed", []):
        layers = item.get("layers") or {}
        available = sorted(layer for layer, state in layers.items() if state.get("available"))
        unavailable = sorted(layer for layer, state in layers.items() if not state.get("available"))
        if available:
            checked.append(
                {
                    "gate": "language-router",
                    "checked": f"{item['adapter']} {item['extension']} layers available: {', '.join(available)}",
                }
            )
        if unavailable:
            not_checked.append(
                f"{item['adapter']} {item['extension']} layers not available: {', '.join(unavailable)}"
            )
        for error in item.get("layer_errors") or []:
            not_checked.append(f"language routing layer error: {error}")
    for item in result.get("coverage", {}).get("unsupported", []):
        not_checked.append(
            f"deep semantic analysis unsupported for {item['extension'] or '<none>'}"
        )
    return {
        "checked": checked,
        "not_checked": sorted(set(not_checked)),
        "policy_path": language_routing,
        "errors": [],
    }


def load_intent(path):
    if not os.path.exists(path):
        raise IntentNotDeclaredError(
            "의도 선언 누락: change-intent.yaml 파일을 찾을 수 없습니다."
        )

    with open(path, "r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream) or {}

    intent = data.get("change_intent") or {}
    return {
        "requirement_id": intent.get("requirement_id"),
        "author": intent.get("author"),
        "allowed_paths": intent.get("allowed_paths") or [],
        "forbidden_paths": intent.get("forbidden_paths") or [],
        "expected_paths": intent.get("expected_paths") or [],
    }


def load_sensitive_policy(path):
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


def load_routing_policy(path):
    with open(path, "r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream) or {}
    return {
        "routing": data.get("routing") or [],
        "default_reviewer": data.get("default_reviewer"),
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


def intent_result(files, intent):
    out_of_scope = []
    forbidden_touched = []
    missing_expected = sorted(
        pattern
        for pattern in intent["expected_paths"]
        if not any(match_glob(path, pattern) for path in files)
    )

    for path in files:
        in_forbidden = any(match_glob(path, pattern) for pattern in intent["forbidden_paths"])
        in_allowed = any(match_glob(path, pattern) for pattern in intent["allowed_paths"])

        if in_forbidden:
            forbidden_touched.append(path)
        elif not in_allowed:
            out_of_scope.append(path)

    return {
        "status": "fail" if forbidden_touched or out_of_scope or missing_expected else "pass",
        "out_of_scope_paths": sorted(out_of_scope),
        "forbidden_touched": sorted(forbidden_touched),
        "expected_paths": intent["expected_paths"],
        "missing_expected": missing_expected,
        "expected_paths_semantics": (
            "each pattern is satisfied by at least one changed file; literal paths are recommended "
            "because globs are coarse; renames use the destination path and deletes count only as a path change"
        ),
    }


def not_declared_intent_result(intent_check):
    return {
        **intent_check,
        "status": "not_declared",
        "out_of_scope_paths": [],
        "forbidden_touched": [],
        "expected_paths": [],
        "missing_expected": [],
    }


def sensitive_result(files, policy):
    frozen_touched = []
    protected_touched = []
    watched_touched = []
    shadow_hits = []
    zone_level_by_path = {}

    for path in files:
        all_records = matching_zone_records(path, policy)
        records = strongest_records(
            [record for record in all_records if record.get("maturity") != "shadow"]
        )
        shadow_records = strongest_records(
            [record for record in all_records if record.get("maturity") == "shadow"]
        )
        for record in shadow_records:
            shadow_hits.append(public_record(record))
        if not records:
            all_strongest = strongest_records(all_records)
            zone_level_by_path[path] = (
                all_strongest[0]["level"] if all_strongest else policy["unlisted_level"]
            )
            continue

        zone_level_by_path[path] = records[0]["level"]
        for record in records:
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
        status = "blocked"
    elif protected_touched:
        status = "approval_required"
    else:
        status = "pass"

    return {
        "status": status,
        "frozen_touched": frozen_touched,
        "protected_touched": protected_touched,
        "watched_touched": watched_touched,
        "shadow_hits": shadow_hits,
        "errors": policy.get("errors", []),
        "zone_level_by_path": zone_level_by_path,
    }


def reviewers_for_files(files, routing_policy):
    reviewers = []
    seen = set()

    def add_reviewer(reviewer):
        if reviewer and reviewer not in seen:
            seen.add(reviewer)
            reviewers.append(reviewer)

    for path in files:
        matched = False
        for route in routing_policy["routing"]:
            pattern = route.get("match_path")
            if not pattern or not match_glob(path, pattern):
                continue
            matched = True
            add_reviewer(route.get("reviewer"))
        if not matched:
            add_reviewer(routing_policy["default_reviewer"])

    return reviewers


def build_reasons(intent_check, sensitive_check):
    reasons = []
    if intent_check.get("status") == "not_declared":
        reasons.append("intent_not_declared:change-intent.yaml file was not found")
    for path in intent_check["forbidden_touched"]:
        reasons.append(f"forbidden_path:{path}")
    for item in sensitive_check["frozen_touched"]:
        reasons.append(f"frozen:{item['path']}:{item['reason']}")
    for path in intent_check["out_of_scope_paths"]:
        reasons.append(f"out_of_scope:{path}")
    for pattern in intent_check["missing_expected"]:
        reasons.append(f"missing_expected:{pattern}")
    for item in sensitive_check["protected_touched"]:
        reasons.append(f"protected:{item['path']}:{item['reason']}")
    for item in sensitive_check["watched_touched"]:
        reasons.append(f"watched:{item['path']}:{item['reason']}")
    return reasons


def can_run_function_gov(diff_input):
    return not os.path.exists(diff_input) and ".." in diff_input


def executed_gate_records(diff_input):
    records = [
        {
            "gate": "check-change-intent",
            "checked": "changed paths against declared allowed_paths, forbidden_paths, and optional expected_paths (one changed-file match per pattern; literal paths recommended because globs are coarse; renames use destination paths and deletes count as path changes)",
        },
        {
            "gate": "check-sensitive-zones",
            "checked": "changed paths against sensitive-zones policy",
        },
    ]
    if can_run_function_gov(diff_input):
        records.append(
            {
                "gate": "check-function-gov-level",
                "checked": "changed Python functions against @gov annotations and changed Java methods against @Gov/framework annotations when framework policy is configured",
            }
        )
    return records


def evidence_checked_records(diff_input, intent_declared):
    records = executed_gate_records(diff_input)
    if intent_declared:
        return records
    return [record for record in records if record["gate"] != "check-change-intent"]


def verdict_statement(verdict):
    if verdict == "pass":
        return "no governance violation detected"
    if verdict == "approval_required":
        return "governance review required"
    return "governance violation detected"


def coverage_statement(diff_input, verdict, checked=None):
    return {
        "verdict_statement": verdict_statement(verdict),
        "checked": executed_gate_records(diff_input) if checked is None else checked,
        "not_checked": NOT_CHECKED_STATEMENTS,
    }


def function_reason(prefix, item):
    reason = item.get("reason") or ""
    suffix = f":{reason}" if reason else ""
    return f"{prefix}:{item.get('path')}::{item.get('name')}:{item.get('side')}{suffix}"


def build_function_gov_result(diff_input, sensitive_zones, repo, framework_annotations=None):
    if not can_run_function_gov(diff_input):
        return {
            "verdict": "pass",
            "changed_functions": [],
            "reasons": [],
            "errors": [],
        }

    result = function_gov_gate.check_function_gov_level(
        diff_input, sensitive_zones, repo, framework_annotations
    )
    reasons = []
    for item in result.get("frozen_touched", []):
        reasons.append(function_reason("function_frozen", item))
    for item in result.get("protected_touched", []):
        reasons.append(function_reason("function_protected", item))
    for item in result.get("watched_touched", []):
        reasons.append(function_reason("function_watched", item))
    for error in result.get("errors", []):
        encoded = json.dumps(error, ensure_ascii=False, sort_keys=True)
        reasons.append(f"function_analysis_error:{encoded}")

    return {
        "verdict": result.get("verdict", "approval_required"),
        "changed_functions": result.get("changed_functions", []),
        "reasons": reasons,
        "errors": result.get("errors", []),
        "coverage_not_checked": result.get("coverage_not_checked", []),
    }


def combine_verdicts(intent_check, sensitive_check, function_gov):
    verdict, exit_code = verdict_and_exit(intent_check, sensitive_check)
    function_verdict = function_gov.get("verdict")

    if verdict == "blocked" or function_verdict == "blocked":
        return "blocked", BLOCKED
    if (
        verdict == "approval_required"
        or function_verdict == "approval_required"
        or function_gov.get("errors")
    ):
        return "approval_required", APPROVAL_REQUIRED
    return "pass", PASS


def verdict_and_exit(intent_check, sensitive_check):
    if intent_check["forbidden_touched"] or sensitive_check["frozen_touched"]:
        return "blocked", BLOCKED
    if (
        intent_check.get("status") == "not_declared"
        or intent_check["out_of_scope_paths"]
        or intent_check["missing_expected"]
        or sensitive_check["protected_touched"]
    ):
        return "approval_required", APPROVAL_REQUIRED
    return "pass", PASS


def build_evidence(args):
    intent_declared = True
    try:
        intent = load_intent(args.change_intent)
    except IntentNotDeclaredError:
        intent_declared = False
        intent = {
            "requirement_id": None,
            "author": None,
            "allowed_paths": [],
            "forbidden_paths": [],
            "expected_paths": [],
        }
    sensitive_policy = load_sensitive_policy(args.sensitive_zones)
    routing_policy = load_routing_policy(args.approval_routing)

    name_status_lines = read_diff_lines(args.diff_input, "--name-status", args.repo)
    if args.numstat_input:
        with open(args.numstat_input, "r", encoding="utf-8") as stream:
            numstat_lines = stream.read().splitlines()
    elif os.path.exists(args.diff_input):
        numstat_lines = []
    else:
        numstat_lines = read_diff_lines(args.diff_input, "--numstat", args.repo)

    files = parse_changed_files(name_status_lines)
    numstat = parse_numstat(numstat_lines)
    lang_coverage = language_coverage(args.diff_input, args.language_routing, args.repo)
    intent_check = intent_result(files, intent)
    if not intent_declared:
        intent_check = not_declared_intent_result(intent_check)
    sensitive_check = sensitive_result(files, sensitive_policy)
    function_gov = build_function_gov_result(
        args.diff_input, args.sensitive_zones, args.repo, args.framework_annotations
    )
    verdict, exit_code = combine_verdicts(intent_check, sensitive_check, function_gov)
    if lang_coverage["errors"]:
        verdict, exit_code = "blocked", BLOCKED

    changed_files = []
    for path in files:
        changed_files.append(
            {
                "path": path,
                "zone_level": sensitive_check["zone_level_by_path"].get(
                    path, sensitive_policy["unlisted_level"]
                ),
                "in_allowed_paths": (
                    any(match_glob(path, pattern) for pattern in intent["allowed_paths"])
                    if intent_declared
                    else None
                ),
            }
        )

    summary = {
        "files_changed": len(files),
        "lines_added": sum(item["added"] for item in numstat.values()),
        "lines_removed": sum(item["removed"] for item in numstat.values()),
    }

    evidence = {
        "change_evidence": {
            "requirement_id": intent["requirement_id"],
            "author": intent["author"],
            "generated_on": resolve_generated_on(args.diff_input, args.generated_on, args.repo),
            "base_commit": resolve_base_commit(args.diff_input, args.repo),
            "tool_version": TOOL_VERSION,
            "python_version": sys.version.split()[0],
            "policy_sha": policy_sha(
                [
                    path
                    for path in (
                        args.sensitive_zones,
                        args.approval_routing,
                        lang_coverage.get("policy_path"),
                    )
                    if path
                ]
            ),
            "summary": summary,
            "changed_files": changed_files,
            "changed_functions": function_gov["changed_functions"],
            "intent_check": intent_check,
            "sensitive_zone_check": {
                "status": sensitive_check["status"],
                "frozen_touched": sensitive_check["frozen_touched"],
                "protected_touched": sensitive_check["protected_touched"],
                "watched_touched": sensitive_check["watched_touched"],
                "shadow_hits": sensitive_check["shadow_hits"],
            },
            "blast_radius": {
                "shared_modules_changed": [],
                "importers_count": None,
            },
            "verdict": verdict,
            "coverage_statement": {
                **coverage_statement(
                    args.diff_input,
                    verdict,
                    checked=(
                        evidence_checked_records(args.diff_input, intent_declared)
                        + lang_coverage["checked"]
                    ),
                ),
                "not_checked": (
                    [INTENT_NOT_DECLARED_STATEMENT] + NOT_CHECKED_STATEMENTS
                    if not intent_declared
                    else NOT_CHECKED_STATEMENTS
                )
                + function_gov.get("coverage_not_checked", [])
                + lang_coverage["not_checked"],
            },
            "reasons": (
                build_reasons(intent_check, sensitive_check)
                + function_gov["reasons"]
                + lang_coverage["errors"]
            ),
            "reviewer_required": reviewers_for_files(files, routing_policy),
        }
    }
    return evidence, exit_code


def main():
    parser = argparse.ArgumentParser(
        description="Generate a change-evidence YAML audit card for a diff."
    )
    parser.add_argument("diff_input", help="git diff ref (base..head) or name-status file")
    parser.add_argument(
        "--change-intent",
        default="change-intent.yaml",
        help="change-intent.yaml path",
    )
    parser.add_argument(
        "--sensitive-zones",
        default="policies/sensitive-zones.yaml",
        help="policies/sensitive-zones.yaml path",
    )
    parser.add_argument(
        "--approval-routing",
        default="policies/approval-routing.yaml",
        help="policies/approval-routing.yaml path",
    )
    parser.add_argument(
        "--language-routing",
        default=None,
        help="policies/language-routing.yaml path",
    )
    parser.add_argument(
        "--framework-annotations",
        default=None,
        help="policies/framework-annotations.yaml path for Java/Spring annotations",
    )
    parser.add_argument(
        "--numstat-input",
        help="optional git diff --numstat file when diff_input is a name-status file",
    )
    parser.add_argument(
        "--generated-on",
        help="override generated_on date for deterministic tests",
    )
    parser.add_argument("--repo", default=".", help="git repository to inspect")
    parser.add_argument("--output", help="write YAML to this file instead of stdout")
    args = parser.parse_args()

    try:
        evidence, exit_code = build_evidence(args)
    except Exception as error:
        evidence = {
            "change_evidence": {
                "requirement_id": None,
                "author": None,
                "generated_on": args.generated_on or "1970-01-01",
                "base_commit": "unknown",
                "tool_version": TOOL_VERSION,
                "python_version": sys.version.split()[0],
                "policy_sha": {},
                "summary": {
                    "files_changed": 0,
                    "lines_added": 0,
                    "lines_removed": 0,
                },
                "changed_files": [],
                "changed_functions": [],
                "intent_check": {
                    "status": "fail",
                    "out_of_scope_paths": [],
                    "forbidden_touched": [],
                    "expected_paths": [],
                    "missing_expected": [],
                    "expected_paths_semantics": (
                        "each pattern is satisfied by at least one changed file; literal paths are recommended "
                        "because globs are coarse; renames use the destination path and deletes count only as a path change"
                    ),
                },
                "sensitive_zone_check": {
                    "status": "blocked",
                    "frozen_touched": [],
                    "protected_touched": [],
                    "watched_touched": [],
                },
                "blast_radius": {
                    "shared_modules_changed": [],
                    "importers_count": None,
                },
                "verdict": "blocked",
                "coverage_statement": coverage_statement(args.diff_input, "blocked", checked=[]),
                "reasons": [str(error)],
                "reviewer_required": [],
            }
        }
        exit_code = BLOCKED

    output = yaml.safe_dump(evidence, allow_unicode=True, sort_keys=False)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as stream:
            stream.write(output)
    else:
        print(output, end="")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
