#!/usr/bin/env python3
import argparse
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import PurePosixPath

import yaml


PASS = 0
APPROVAL_REQUIRED = 2

POLICY_GLOBS = ["policies/*.yaml"]
ENFORCEMENT_GLOBS = [
    ".harness/**",
    "tests/run-tests.sh",
    "tests/cases.yaml",
    ".github/workflows/**",
    "CODEOWNERS",
]
LEVEL_STRENGTH = {
    None: 0,
    "": 0,
    "free": 0,
    "pass": 0,
    "watched": 1,
    "approval_required": 2,
    "protected": 2,
    "blocked": 3,
    "frozen": 3,
}


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
        ["git", *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=repo,
    )
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(stderr.strip() or f"git {' '.join(args)} failed")
    return stdout


def split_rev_range(rev_range):
    if ".." not in rev_range:
        raise ValueError("diff input must be a git rev range like <base>..<head>")
    base, head = rev_range.split("..", 1)
    if not base or not head:
        raise ValueError("diff input must include both base and head refs")
    return base, head


def changed_paths(base, head, repo):
    output = run_git(["diff", "--name-status", "--no-renames", base, head], repo)
    paths = set()
    for raw_line in output.splitlines():
        parts = raw_line.split("\t")
        if len(parts) < 2:
            continue
        for path in parts[1:]:
            paths.add(normalize_path(path))
    return sorted(paths)


def path_exists_at_ref(ref, path, repo):
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{ref}:{path}"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=repo,
    )
    return result.returncode == 0


def text_at_ref(ref, path, repo):
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=repo,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(stderr.strip() or f"git show {ref}:{path} failed")
    return result.stdout.decode("utf-8", errors="replace")


def yaml_at_ref(ref, path, repo):
    text = text_at_ref(ref, path, repo)
    return yaml.safe_load(text) or {}


def is_policy_path(path):
    return any(match_glob(path, pattern) for pattern in POLICY_GLOBS)


def is_enforcement_path(path):
    return any(match_glob(path, pattern) for pattern in ENFORCEMENT_GLOBS)


def level_strength(value):
    if isinstance(value, str):
        return LEVEL_STRENGTH.get(value, 0)
    if value is True:
        return 1
    if value is False:
        return 0
    return LEVEL_STRENGTH.get(value, 0)


def record(path, kind, pointer, before=None, after=None, reason=None):
    item = {
        "path": path,
        "kind": kind,
        "pointer": pointer,
    }
    if before is not None:
        item["before"] = before
    if after is not None:
        item["after"] = after
    if reason:
        item["reason"] = reason
    return item


def scalar_is_weaker(pointer, before, after):
    key = pointer.rsplit("/", 1)[-1]
    if key in {"level", "max_verdict", "unlisted_level"}:
        return level_strength(after) < level_strength(before)
    if key == "new_only":
        return before is False and after is True
    return False


def maturity_weakened(before, after):
    before_maturity = before.get("maturity", "enforcing")
    after_maturity = after.get("maturity", "enforcing")
    return before_maturity == "enforcing" and after_maturity == "shadow"


def normalize_list_item(item):
    if isinstance(item, dict):
        return json.dumps(item, ensure_ascii=False, sort_keys=True)
    return str(item)


def compare_structures(path, before, after, pointer=""):
    findings = []
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) - set(after)):
            findings.append(
                record(
                    path,
                    "removed_key",
                    f"{pointer}/{key}",
                    before=before[key],
                    reason="policy key removed",
                )
            )
        for key in sorted(set(before) & set(after)):
            findings.extend(compare_structures(path, before[key], after[key], f"{pointer}/{key}"))
        return findings

    if isinstance(before, list) and isinstance(after, list):
        before_items = {normalize_list_item(item): item for item in before}
        after_items = {normalize_list_item(item): item for item in after}
        for key in sorted(set(before_items) - set(after_items)):
            findings.append(
                record(
                    path,
                    "removed_list_item",
                    pointer,
                    before=before_items[key],
                    reason="policy list item removed",
                )
            )
        return findings

    if before != after and scalar_is_weaker(pointer, before, after):
        findings.append(
            record(
                path,
                "weakened_value",
                pointer,
                before=before,
                after=after,
                reason="policy value weakened",
            )
        )
    return findings


def keyed_records(items, key):
    result = {}
    if not isinstance(items, list):
        return result
    for item in items:
        if isinstance(item, dict) and item.get(key) is not None:
            result[str(item[key])] = item
    return result


def compare_sensitive_zones(path, before, after):
    findings = compare_structures(path, before.get("defaults", {}), after.get("defaults", {}), "/defaults")
    before_zones = keyed_records(before.get("zones", []), "path")
    after_zones = keyed_records(after.get("zones", []), "path")
    for zone_path in sorted(set(before_zones) - set(after_zones)):
        findings.append(
            record(
                path,
                "removed_zone",
                f"/zones/{zone_path}",
                before=before_zones[zone_path],
                reason="sensitive zone removed",
            )
        )
    for zone_path in sorted(set(before_zones) & set(after_zones)):
        before_zone = before_zones[zone_path]
        after_zone = after_zones[zone_path]
        if level_strength(after_zone.get("level")) < level_strength(before_zone.get("level")):
            findings.append(
                record(
                    path,
                    "weakened_zone_level",
                    f"/zones/{zone_path}/level",
                    before=before_zone.get("level"),
                    after=after_zone.get("level"),
                    reason="sensitive zone level lowered",
                )
            )
        if maturity_weakened(before_zone, after_zone):
            findings.append(
                record(
                    path,
                    "weakened_zone_maturity",
                    f"/zones/{zone_path}/maturity",
                    before=before_zone.get("maturity", "enforcing"),
                    after=after_zone.get("maturity", "enforcing"),
                    reason="sensitive zone maturity changed to shadow",
                )
            )
        if before_zone.get("required_approval") and not after_zone.get("required_approval"):
            findings.append(
                record(
                    path,
                    "removed_required_approval",
                    f"/zones/{zone_path}/required_approval",
                    before=before_zone.get("required_approval"),
                    reason="required approval removed",
                )
            )
    return findings


def compare_capabilities(path, before, after):
    findings = compare_structures(path, before.get("defaults", {}), after.get("defaults", {}), "/defaults")
    before_caps = keyed_records(before.get("capabilities", []), "id")
    after_caps = keyed_records(after.get("capabilities", []), "id")
    for cap_id in sorted(set(before_caps) - set(after_caps)):
        findings.append(
            record(
                path,
                "removed_capability",
                f"/capabilities/{cap_id}",
                before=before_caps[cap_id],
                reason="capability rule removed",
            )
        )
    for cap_id in sorted(set(before_caps) & set(after_caps)):
        before_cap = before_caps[cap_id]
        after_cap = after_caps[cap_id]
        if level_strength(after_cap.get("level")) < level_strength(before_cap.get("level")):
            findings.append(
                record(
                    path,
                    "weakened_capability_level",
                    f"/capabilities/{cap_id}/level",
                    before=before_cap.get("level"),
                    after=after_cap.get("level"),
                    reason="capability level lowered",
                )
            )
        if maturity_weakened(before_cap, after_cap):
            findings.append(
                record(
                    path,
                    "weakened_capability_maturity",
                    f"/capabilities/{cap_id}/maturity",
                    before=before_cap.get("maturity", "enforcing"),
                    after=after_cap.get("maturity", "enforcing"),
                    reason="capability maturity changed to shadow",
                )
            )
        findings.extend(
            compare_structures(
                path,
                before_cap.get("signals", {}),
                after_cap.get("signals", {}),
                f"/capabilities/{cap_id}/signals",
            )
        )
    return findings


def compare_routing(path, before, after):
    findings = compare_structures(path, before, after)
    before_routes = before.get("routing", []) if isinstance(before, dict) else []
    after_routes = after.get("routing", []) if isinstance(after, dict) else []
    if isinstance(before_routes, list) and isinstance(after_routes, list) and len(after_routes) < len(before_routes):
        findings.append(
            record(
                path,
                "removed_routing_rule",
                "/routing",
                before=len(before_routes),
                after=len(after_routes),
                reason="approval routing rule removed",
            )
        )
    return findings


def compare_policy_file(path, before, after):
    if path.endswith("sensitive-zones.yaml"):
        return compare_sensitive_zones(path, before, after)
    if path.endswith("sensitive-capabilities.yaml"):
        return compare_capabilities(path, before, after)
    if path.endswith("approval-routing.yaml"):
        return compare_routing(path, before, after)
    return compare_structures(path, before, after)


def removed_and_added_diff_lines(base, head, path, repo):
    output = run_git(["diff", "--no-ext-diff", "--unified=0", base, head, "--", path], repo)
    removed = []
    added = []
    for line in output.splitlines():
        if line.startswith(("---", "+++", "@@")):
            continue
        if line.startswith("-"):
            removed.append(line[1:])
        elif line.startswith("+"):
            added.append(line[1:])
    return removed, added


def normalize_yaml_plain_scalar(value):
    stripped = value.strip()
    quote = None
    for index, char in enumerate(stripped):
        if char in ("'", '"') and (index == 0 or stripped[index - 1] != "\\"):
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
        if char == "#" and quote is None and index > 0 and stripped[index - 1].isspace():
            stripped = stripped[:index].strip()
            break
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in ("'", '"'):
        return stripped[1:-1].strip()
    return stripped


def is_gate_bypass_line(line):
    stripped = line.strip()
    if re.search(r"\|\|\s*true\b", stripped):
        return True
    if ":" not in stripped:
        return False
    key, value = stripped.split(":", 1)
    if key.strip() != "continue-on-error":
        return False
    return normalize_yaml_plain_scalar(value).lower() in {"true", "yes", "on"}


def detect_enforcement_bypass(base, head, path, repo):
    removed, added = removed_and_added_diff_lines(base, head, path, repo)
    findings = []
    added_bypass_lines = [line.strip() for line in added if is_gate_bypass_line(line)]
    for line in removed:
        stripped = line.strip()
        if any(added_line.startswith(stripped) for added_line in added_bypass_lines):
            continue
        if ".harness/gates/" in stripped or any(
            name in stripped
            for name in (
                "check-change-intent",
                "check-sensitive-zones",
                "generate-change-evidence",
                "check-function-gov-level",
                "check-new-capabilities",
                "check-policy-change",
            )
        ):
            findings.append(
                record(path, "removed_gate_invocation", "<diff>", before=stripped, reason="gate invocation removed")
            )
    for line in added:
        stripped = line.strip()
        if stripped in added_bypass_lines:
            findings.append(
                record(path, "added_gate_bypass", "<diff>", after=stripped, reason="gate failure bypass added")
            )
    if path.startswith(".github/workflows/"):
        for line in removed:
            stripped = line.strip()
            if "required" in stripped.lower() or "branch-protection" in stripped.lower():
                findings.append(
                    record(
                        path,
                        "removed_required_check",
                        "<diff>",
                        before=stripped,
                        reason="required check line removed",
                    )
                )
    return findings


def check_policy_change(rev_range, repo="."):
    base, head = split_rev_range(rev_range)
    paths = changed_paths(base, head, repo)
    policy_loosening = []
    enforcement_bypass = []
    errors = []

    for path in paths:
        try:
            base_exists = path_exists_at_ref(base, path, repo)
            head_exists = path_exists_at_ref(head, path, repo)

            if is_policy_path(path):
                if base_exists and not head_exists:
                    policy_loosening.append(
                        record(path, "removed_policy_file", "/", reason="policy file removed")
                    )
                elif base_exists and head_exists:
                    policy_loosening.extend(
                        compare_policy_file(
                            path,
                            yaml_at_ref(base, path, repo),
                            yaml_at_ref(head, path, repo),
                        )
                    )

            if is_enforcement_path(path) and base_exists and head_exists:
                enforcement_bypass.extend(detect_enforcement_bypass(base, head, path, repo))
            elif is_enforcement_path(path) and base_exists and not head_exists:
                enforcement_bypass.append(
                    record(path, "removed_enforcement_file", "/", reason="enforcement file removed")
                )
        except Exception as error:
            errors.append({"path": path, "error": str(error)})

    policy_loosening = sorted(policy_loosening, key=lambda item: (item["path"], item["kind"], item["pointer"]))
    enforcement_bypass = sorted(enforcement_bypass, key=lambda item: (item["path"], item["kind"], item["pointer"]))
    verdict = "approval_required" if policy_loosening or enforcement_bypass or errors else "pass"
    exit_code = APPROVAL_REQUIRED if verdict == "approval_required" else PASS
    return {
        "gate": "check-policy-change",
        "verdict": verdict,
        "base_commit": run_git(["rev-parse", base], repo).strip(),
        "head_commit": run_git(["rev-parse", head], repo).strip(),
        "policy_loosening": policy_loosening,
        "enforcement_bypass": enforcement_bypass,
        "errors": errors,
        "exit_code": exit_code,
    }


def print_text(result):
    if result["verdict"] == "approval_required":
        print("APPROVAL_REQUIRED: 정책 완화 또는 집행 우회가 감지되었습니다.")
    else:
        print("PASS: 정책 완화 또는 집행 우회가 감지되지 않았습니다.")
    for item in result.get("policy_loosening", []):
        print(f"policy_loosening: {item['path']} {item['kind']} {item['pointer']}")
    for item in result.get("enforcement_bypass", []):
        print(f"enforcement_bypass: {item['path']} {item['kind']}")


def main():
    parser = argparse.ArgumentParser(
        description="Check policy and harness changes for governance loosening."
    )
    parser.add_argument("diff_input", help="git diff ref range, for example <base>..<head>")
    parser.add_argument("--repo", default=".", help="git repository to inspect")
    parser.add_argument("--json", action="store_true", help="print structured JSON")
    args = parser.parse_args()

    try:
        result = check_policy_change(args.diff_input, args.repo)
    except Exception as error:
        result = {
            "gate": "check-policy-change",
            "verdict": "approval_required",
            "policy_loosening": [],
            "enforcement_bypass": [],
            "errors": [{"error": str(error)}],
            "exit_code": APPROVAL_REQUIRED,
        }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print_text(result)
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
