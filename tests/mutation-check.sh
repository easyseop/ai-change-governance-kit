#!/usr/bin/env bash
set -u

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR" || exit 1

python3 - <<'PY'
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


ROOT = Path.cwd()
CASES_PATH = Path("tests/cases.yaml")
POLICY_FIXTURE = Path("tests/fixtures/maturity/sensitive-zones.yaml")
RUNNER = ["bash", "tests/run-tests.sh"]


def file_sha(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def copy_repo(destination):
    def ignore(_dir, names):
        ignored = {".git", ".pytest_cache", "__pycache__"}
        return {name for name in names if name in ignored}

    shutil.copytree(ROOT, destination, ignore=ignore)


def load_cases(repo):
    with (repo / CASES_PATH).open("r", encoding="utf-8") as stream:
        return yaml.safe_load(stream)["cases"]


def write_cases(repo, cases):
    with (repo / CASES_PATH).open("w", encoding="utf-8") as stream:
        yaml.safe_dump({"cases": cases}, stream, allow_unicode=True, sort_keys=False)


def run_selected_case(repo, case_name):
    env = os.environ.copy()
    env["TEST_CASE_NAME"] = case_name
    return subprocess.run(
        RUNNER,
        cwd=repo,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def run_group(repo, group):
    env = os.environ.copy()
    env["TEST_CASE_GROUP"] = group
    return subprocess.run(
        RUNNER,
        cwd=repo,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def mutate_verdict(value):
    replacements = {
        "pass": "blocked",
        "blocked": "pass",
        "approval_required": "pass",
    }
    return replacements.get(value, "pass")


def mutate_exit_code(value):
    return 2 if value == 0 else 0


def check_expectation_mutations(repo):
    original_cases = load_cases(repo)
    dead = []
    tested = 0

    for index, case in enumerate(original_cases):
        expect = case.get("expect") or {}
        for field, mutator in (("verdict", mutate_verdict), ("exit_code", mutate_exit_code)):
            if field not in expect:
                continue
            mutated_cases = yaml.safe_load(yaml.safe_dump(original_cases, sort_keys=False))
            mutated_cases[index]["expect"][field] = mutator(expect[field])
            write_cases(repo, mutated_cases)
            completed = run_selected_case(repo, case["name"])
            tested += 1
            if completed.returncode == 0:
                dead.append(f"{case['name']}:{field}")

    write_cases(repo, original_cases)
    return tested, dead


def check_policy_mutation(repo):
    policy_path = repo / POLICY_FIXTURE
    with policy_path.open("r", encoding="utf-8") as stream:
        policy = yaml.safe_load(stream)
    original = yaml.safe_load(yaml.safe_dump(policy, sort_keys=False))

    mutated = yaml.safe_load(yaml.safe_dump(policy, sort_keys=False))
    for zone in mutated.get("zones", []):
        if zone.get("path") == "app/enforcing/**":
            zone["level"] = "watched"
            zone["required_approval"] = None
            break
    else:
        return False, "policy fixture target app/enforcing/** not found"

    with policy_path.open("w", encoding="utf-8") as stream:
        yaml.safe_dump(mutated, stream, allow_unicode=True, sort_keys=False)
    completed = run_selected_case(repo, "maturity-zone-enforcing-approval")

    with policy_path.open("w", encoding="utf-8") as stream:
        yaml.safe_dump(original, stream, allow_unicode=True, sort_keys=False)

    if completed.returncode == 0:
        return False, "maturity-zone-enforcing-approval stayed PASS after protected->watched policy mutation"
    return True, "maturity-zone-enforcing-approval failed after protected->watched policy mutation"


def check_group(repo, group):
    completed = run_group(repo, group)
    if completed.returncode != 0:
        return False, completed.stdout + completed.stderr
    return True, completed.stdout


def main():
    before_hashes = {
        str(CASES_PATH): file_sha(CASES_PATH),
        str(POLICY_FIXTURE): file_sha(POLICY_FIXTURE),
    }
    with tempfile.TemporaryDirectory(prefix="acgh-mutation-") as temp_dir:
        repo = Path(temp_dir) / "repo"
        copy_repo(repo)

        tested, dead = check_expectation_mutations(repo)
        policy_ok, policy_message = check_policy_mutation(repo)
        metamorphic_ok, metamorphic_output = check_group(repo, "metamorphic")
        negative_ok, negative_output = check_group(repo, "negative-corpus")

    after_hashes = {
        str(CASES_PATH): file_sha(CASES_PATH),
        str(POLICY_FIXTURE): file_sha(POLICY_FIXTURE),
    }
    restore_ok = before_hashes == after_hashes

    print(f"Expectation mutations checked: {tested}")
    print(f"Policy mutation: {policy_message}")
    print("Metamorphic group: PASS" if metamorphic_ok else "Metamorphic group: FAIL")
    print("Negative corpus group: PASS" if negative_ok else "Negative corpus group: FAIL")
    print("Original files unchanged: PASS" if restore_ok else "Original files unchanged: FAIL")

    errors = []
    if dead:
        errors.append("dead expectation mutations: " + ", ".join(dead))
    if not policy_ok:
        errors.append(policy_message)
    if not metamorphic_ok:
        errors.append(metamorphic_output.rstrip())
    if not negative_ok:
        errors.append(negative_output.rstrip())
    if not restore_ok:
        errors.append(f"hash mismatch: before={before_hashes} after={after_hashes}")

    if errors:
        print("FAIL mutation-check")
        for error in errors:
            print(f"  - {error}")
        return 1

    print("PASS mutation-check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
PY
