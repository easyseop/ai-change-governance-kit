#!/usr/bin/env python3
import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from pathlib import PurePosixPath


PASS = 0
APPROVAL_REQUIRED = 2

DEFAULT_POLICY = "policies/sensitive-capabilities.yaml"
DEFAULT_JAVA_POLICY = "policies/java-sensitive-capabilities.yaml"
GATE_DIR = Path(__file__).resolve().parent


def load_gate_module(filename, module_name):
    spec = importlib.util.spec_from_file_location(module_name, GATE_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


capability_gate = load_gate_module(
    "extract-python-capabilities.py", "extract_python_capabilities_gate"
)
java_capability_gate = load_gate_module(
    "extract-java-capabilities.py", "extract_java_capabilities_gate"
)
LEVEL_STRENGTH = {"watched": 0, "protected": 1}


def normalize_path(path):
    return str(PurePosixPath(path.replace("\\", "/")))


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


def path_exists_at_ref(ref, path, repo):
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{ref}:{path}"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=repo,
    )
    return result.returncode == 0


def source_at_ref(ref, path, repo):
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
    return result.stdout.decode("utf-8")


def changed_capability_paths(base, head, repo):
    output = run_git(["diff", "--name-status", "--no-renames", base, head], repo)
    paths = set()
    for raw_line in output.splitlines():
        parts = raw_line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        candidates = parts[1:]
        if status.startswith("R") or status.startswith("C"):
            candidates = parts[1:3]
        for path in candidates:
            normalized = normalize_path(path)
            if normalized.endswith((".py", ".java")):
                paths.add(normalized)
    return sorted(paths)


def absent_result(path):
    return {
        "path": path,
        "capabilities": [],
        "unresolved_dynamic": [],
        "errors": [],
        "parse_error": False,
        "unreadable": False,
        "absent": True,
    }


def gate_for_path(path):
    if path.endswith(".java"):
        return java_capability_gate
    return capability_gate


def policy_for_path(path, python_policy, java_policy):
    if path.endswith(".java"):
        return java_policy
    return python_policy


def extract_at_ref(ref, path, policy, repo):
    gate = gate_for_path(path)
    try:
        source = source_at_ref(ref, path, repo)
    except UnicodeDecodeError as error:
        _, catalog_errors = gate.load_catalog(policy)
        return {
            "path": path,
            "capabilities": [],
            "unresolved_dynamic": [],
            "errors": catalog_errors,
            "parse_error": False,
            "unreadable": f"unreadable source: {error}",
        }
    return gate.extract_from_source(source, path, policy)


def capability_index(result):
    return {capability["id"]: capability for capability in result.get("capabilities", [])}


def catalog_metadata(policy, gate):
    capabilities, _ = gate.load_catalog(policy)
    return {capability["id"]: capability for capability in capabilities}


def new_capability_record(path, capability, metadata):
    cap_id = capability["id"]
    meta = metadata.get(cap_id, {})
    return {
        "path": path,
        "id": cap_id,
        "level": capability.get("level"),
        "maturity": meta.get("maturity", "enforcing"),
        "reason": meta.get("reason"),
        "reviewer": meta.get("reviewer"),
        "signals": capability.get("signals", []),
    }


def escalated_capability_record(path, base_capability, head_capability, metadata):
    record = new_capability_record(path, head_capability, metadata)
    record["base_level"] = base_capability.get("level")
    record["change"] = "level_escalation"
    return record


def fail_closed_record(path, reason):
    return {
        "path": path,
        "level": "protected",
        "reason": reason,
    }


def sort_capability_records(records):
    return sorted(
        records,
        key=lambda item: (
            item["path"],
            item["id"],
            item.get("level") or "",
            json.dumps(item.get("signals", []), ensure_ascii=False, sort_keys=True),
        ),
    )


def sort_fail_closed(records):
    return sorted(records, key=lambda item: (item["path"], item["reason"]))


def check_new_capabilities(rev_range, policy, repo=".", java_policy=DEFAULT_JAVA_POLICY):
    base, head = split_rev_range(rev_range)
    paths = changed_capability_paths(base, head, repo)
    languages = {
        "java" if path.endswith(".java") else "python"
        for path in paths
    }
    metadata_by_language = {}
    if "python" in languages:
        metadata_by_language["python"] = catalog_metadata(policy, capability_gate)
    if "java" in languages:
        metadata_by_language["java"] = catalog_metadata(java_policy, java_capability_gate)
    new_capabilities = []
    warned_capabilities = []
    shadow_capabilities = []
    fail_closed = []
    errors = []

    for path in paths:
        path_policy = policy_for_path(path, policy, java_policy)
        language = "java" if path.endswith(".java") else "python"
        metadata = metadata_by_language[language]
        base_exists = path_exists_at_ref(base, path, repo)
        head_exists = path_exists_at_ref(head, path, repo)
        base_result = extract_at_ref(base, path, path_policy, repo) if base_exists else absent_result(path)
        head_result = extract_at_ref(head, path, path_policy, repo) if head_exists else absent_result(path)

        if base_exists and (base_result.get("parse_error") or base_result.get("unreadable")):
            errors.append(
                {
                    "path": path,
                    "side": "base",
                    "error": base_result.get("parse_error") or base_result.get("unreadable"),
                }
            )
            base_caps = {}
        else:
            base_caps = capability_index(base_result)

        if head_exists and (head_result.get("parse_error") or head_result.get("unreadable")):
            reason = "head file could not be parsed after the change"
            errors.append(
                {
                    "path": path,
                    "side": "head",
                    "error": head_result.get("parse_error") or head_result.get("unreadable"),
                }
            )
            fail_closed.append(fail_closed_record(path, reason))
            continue

        for error in base_result.get("errors", []):
            errors.append({"path": path, "side": "base", **error})
        for error in head_result.get("errors", []):
            errors.append({"path": path, "side": "head", **error})

        head_caps = capability_index(head_result)
        for cap_id in sorted(set(head_caps) - set(base_caps)):
            record = new_capability_record(path, head_caps[cap_id], metadata)
            if record["maturity"] == "shadow":
                shadow_capabilities.append(record)
            elif record["level"] == "watched":
                warned_capabilities.append(record)
            else:
                new_capabilities.append(record)
        for cap_id in sorted(set(head_caps) & set(base_caps)):
            base_level = base_caps[cap_id].get("level")
            head_level = head_caps[cap_id].get("level")
            if LEVEL_STRENGTH.get(head_level, 0) <= LEVEL_STRENGTH.get(base_level, 0):
                continue
            record = escalated_capability_record(
                path, base_caps[cap_id], head_caps[cap_id], metadata
            )
            if record["maturity"] == "shadow":
                shadow_capabilities.append(record)
            elif record["level"] == "watched":
                warned_capabilities.append(record)
            else:
                new_capabilities.append(record)

    new_capabilities = sort_capability_records(new_capabilities)
    warned_capabilities = sort_capability_records(warned_capabilities)
    shadow_capabilities = sort_capability_records(shadow_capabilities)
    fail_closed = sort_fail_closed(fail_closed)

    if new_capabilities or fail_closed or errors:
        verdict = "approval_required"
        exit_code = APPROVAL_REQUIRED
    else:
        verdict = "pass"
        exit_code = PASS

    return {
        "gate": "check-new-capabilities",
        "verdict": verdict,
        "base_commit": run_git(["rev-parse", base], repo).strip(),
        "head_commit": run_git(["rev-parse", head], repo).strip(),
        "new_capabilities": new_capabilities,
        "warned_capabilities": warned_capabilities,
        "shadow_capabilities": shadow_capabilities,
        "fail_closed": fail_closed,
        "errors": errors,
        "exit_code": exit_code,
    }


def print_text(result):
    if result["verdict"] == "approval_required":
        print("APPROVAL_REQUIRED: 신규 민감 능력 또는 분석 오류가 감지되었습니다.")
    elif result["warned_capabilities"]:
        print("PASS: watched 신규 능력이 감지되었습니다.")
    else:
        print("PASS: 신규 민감 능력이 감지되지 않았습니다.")

    for item in result.get("new_capabilities", []):
        print(f"protected: {item['path']}::{item['id']}")
    for item in result.get("warned_capabilities", []):
        print(f"watched: {item['path']}::{item['id']}")
    for item in result.get("shadow_capabilities", []):
        print(f"shadow: {item['path']}::{item['id']} level={item['level']}")
    for item in result.get("fail_closed", []):
        print(f"fail_closed: {item['path']} {item['reason']}")


def main():
    parser = argparse.ArgumentParser(
        description="Check base/head Python and Java changes for newly introduced sensitive capabilities."
    )
    parser.add_argument("diff_input", help="git diff ref range, for example <base>..<head>")
    parser.add_argument("policy", nargs="?", default=DEFAULT_POLICY, help="Python sensitive capability catalog yaml")
    parser.add_argument(
        "--java-policy",
        default=DEFAULT_JAVA_POLICY,
        help="Java sensitive capability catalog yaml",
    )
    parser.add_argument("--repo", default=".", help="git repository to inspect")
    parser.add_argument("--json", action="store_true", help="print structured JSON")
    args = parser.parse_args()

    try:
        result = check_new_capabilities(args.diff_input, args.policy, args.repo, args.java_policy)
    except Exception as error:
        result = {
            "gate": "check-new-capabilities",
            "verdict": "approval_required",
            "new_capabilities": [],
            "warned_capabilities": [],
            "shadow_capabilities": [],
            "fail_closed": [
                {
                    "path": "<unknown>",
                    "level": "protected",
                    "reason": "capability analysis failed",
                }
            ],
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
