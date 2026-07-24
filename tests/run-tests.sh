#!/usr/bin/env bash
set -u

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR" || exit 1

python3 - <<'PY'
import json
import os
import shutil
import subprocess
import sys

import yaml


GATES = {
    "check-change-intent": ".harness/gates/check-change-intent.py",
    "check-sensitive-zones": ".harness/gates/check-sensitive-zones.py",
    "generate-change-evidence": ".harness/gates/generate-change-evidence.py",
    "extract-python-inventory": ".harness/gates/extract-python-inventory.py",
    "extract-java-inventory": ".harness/gates/extract-java-inventory.py",
    "map-diff-to-functions": ".harness/gates/map-diff-to-functions.py",
    "classify-python-function-changes": ".harness/gates/classify-python-function-changes.py",
    "extract-gov-annotations": ".harness/gates/extract-gov-annotations.py",
    "check-function-gov-level": ".harness/gates/check-function-gov-level.py",
    "extract-python-capabilities": ".harness/gates/extract-python-capabilities.py",
    "extract-java-capabilities": ".harness/gates/extract-java-capabilities.py",
    "check-new-capabilities": ".harness/gates/check-new-capabilities.py",
    "check-policy-change": ".harness/gates/check-policy-change.py",
    "bootstrap-sensitive-zones": ".harness/gates/bootstrap-sensitive-zones.py",
    "bootstrap-sensitive-functions": ".harness/gates/bootstrap-sensitive-functions.py",
    "extract-sinks": ".harness/gates/extract-sinks.py",
    "extract-callgraph": ".harness/gates/extract-callgraph.py",
    "extract-java-callgraph": ".harness/gates/extract-java-callgraph.py",
    "check-indirect-impact": ".harness/gates/check-indirect-impact.py",
    "language-router": ".harness/gates/language-router.py",
    "check-tree-sitter-languages": ".harness/gates/check-tree-sitter-languages.py",
}
ROOT_DIR = os.getcwd()


def run_command(command, extra_env=None):
    env = None
    if extra_env:
        env = os.environ.copy()
        for key, value in extra_env.items():
            if key == "PYTHONPATH":
                path_value = value
                if not os.path.isabs(path_value):
                    path_value = os.path.join(ROOT_DIR, path_value)
                existing = env.get("PYTHONPATH")
                env[key] = f"{path_value}{os.pathsep}{existing}" if existing else path_value
            else:
                env[key] = str(value)
    return subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def prepare_function_mapping_fixture(fixture_dir):
    work_dir = subprocess.check_output(["mktemp", "-d"], text=True).strip()
    run_command(["git", "init", "-q", work_dir])
    run_command(["git", "-C", work_dir, "config", "user.email", "codex@example.invalid"])
    run_command(["git", "-C", work_dir, "config", "user.name", "Codex Test"])
    subprocess.run(["cp", "-R", f"{fixture_dir}/base/.", work_dir], check=True)
    run_command(["git", "-C", work_dir, "add", "."])
    run_command(["git", "-C", work_dir, "commit", "-qm", "base"])
    base = run_command(["git", "-C", work_dir, "rev-parse", "HEAD"]).stdout.strip()
    for entry in os.listdir(work_dir):
        if entry == ".git":
            continue
        entry_path = os.path.join(work_dir, entry)
        if os.path.isdir(entry_path):
            shutil.rmtree(entry_path)
        else:
            os.remove(entry_path)
    subprocess.run(["cp", "-R", f"{fixture_dir}/head/.", work_dir], check=True)
    run_command(["git", "-C", work_dir, "add", "."])
    run_command(["git", "-C", work_dir, "add", "-u"])
    run_command(["git", "-C", work_dir, "commit", "-qm", "head"])
    head = run_command(["git", "-C", work_dir, "rev-parse", "HEAD"]).stdout.strip()
    return work_dir, f"{base}..{head}"


def load_output(gate, stdout):
    if gate in (
        "check-change-intent",
        "check-sensitive-zones",
        "language-router",
        "check-tree-sitter-languages",
    ):
        return json.loads(stdout)
    return yaml.safe_load(stdout)


def case_command(case):
    gate = case["gate"]
    data = case.get("input", {})
    script = GATES[gate]

    if gate == "check-change-intent":
        if data.get("fixture_dir"):
            work_dir, rev_range = prepare_function_mapping_fixture(data["fixture_dir"])
            script_path = f"{ROOT_DIR}/{script}"
            intent_path = f"{ROOT_DIR}/{data['change_intent']}"
            return [
                "bash",
                "-c",
                (
                    f"cd {work_dir!r} && "
                    f"python3 {script_path!r} {rev_range!r} {intent_path!r} --json"
                ),
            ]
        return [
            "python3",
            script,
            data["name_status"],
            data["change_intent"],
            "--json",
        ]

    if gate == "check-sensitive-zones":
        return [
            "python3",
            script,
            data["name_status"],
            data.get("policy", "policies/sensitive-zones.yaml"),
            "--json",
        ]

    if gate == "generate-change-evidence":
        if data.get("fixture_dir"):
            work_dir, rev_range = prepare_function_mapping_fixture(data["fixture_dir"])
            command = [
                "python3",
                f"{ROOT_DIR}/{script}",
                rev_range,
                "--repo",
                work_dir,
                "--change-intent",
                data["change_intent"],
                "--sensitive-zones",
                f"{ROOT_DIR}/{data.get('policy', 'policies/sensitive-zones.yaml')}",
                "--approval-routing",
                f"{ROOT_DIR}/policies/approval-routing.yaml",
                "--framework-annotations",
                f"{ROOT_DIR}/{data.get('framework_annotations', 'policies/framework-annotations.yaml')}",
                "--generated-on",
                "2026-06-30",
            ]
            if data.get("language_routing"):
                command.extend(["--language-routing", f"{ROOT_DIR}/{data['language_routing']}"])
            return command
        command = [
            "python3",
            script,
            data["name_status"],
            "--change-intent",
            data["change_intent"],
            "--sensitive-zones",
            data.get("policy", "policies/sensitive-zones.yaml"),
            "--approval-routing",
            "policies/approval-routing.yaml",
            "--framework-annotations",
            data.get("framework_annotations", "policies/framework-annotations.yaml"),
            "--generated-on",
            "2026-06-30",
        ]
        if data.get("language_routing"):
            command.extend(["--language-routing", data["language_routing"]])
        if data.get("numstat"):
            command.extend(["--numstat-input", data["numstat"]])
        return command

    if gate in ("extract-python-inventory", "extract-java-inventory"):
        return [
            "python3",
            script,
            data["source_file"],
            "--json",
        ]

    if gate == "extract-gov-annotations":
        return [
            "python3",
            script,
            data["source_file"],
            "--json",
        ]

    if gate == "extract-python-capabilities":
        command = [
            "python3",
            script,
            data["source_file"],
        ]
        if data.get("policy"):
            command.append(data["policy"])
        command.append("--json")
        return command

    if gate == "extract-java-capabilities":
        command = [
            "python3",
            script,
            data["source_file"],
        ]
        if data.get("policy"):
            command.append(data["policy"])
        command.append("--json")
        return command

    if gate == "map-diff-to-functions":
        work_dir, rev_range = prepare_function_mapping_fixture(data["fixture_dir"])
        return [
            "python3",
            f"{ROOT_DIR}/{script}",
            rev_range,
            "--repo",
            work_dir,
            "--json",
        ]

    if gate == "classify-python-function-changes":
        work_dir, rev_range = prepare_function_mapping_fixture(data["fixture_dir"])
        return [
            "python3",
            f"{ROOT_DIR}/{script}",
            rev_range,
            "--repo",
            work_dir,
            "--json",
        ]

    if gate == "check-function-gov-level":
        work_dir, rev_range = prepare_function_mapping_fixture(data["fixture_dir"])
        return [
            "python3",
            f"{ROOT_DIR}/{script}",
            rev_range,
            f"{ROOT_DIR}/policies/sensitive-zones.yaml",
            "--framework-annotations",
            f"{ROOT_DIR}/{data.get('framework_annotations', 'policies/framework-annotations.yaml')}",
            "--repo",
            work_dir,
            "--json",
        ]

    if gate == "check-new-capabilities":
        work_dir, rev_range = prepare_function_mapping_fixture(data["fixture_dir"])
        command = [
            "python3",
            f"{ROOT_DIR}/{script}",
            rev_range,
            f"{ROOT_DIR}/{data.get('policy', 'policies/sensitive-capabilities.yaml')}",
            "--repo",
            work_dir,
            "--json",
        ]
        if data.get("java_policy"):
            command.extend(["--java-policy", f"{ROOT_DIR}/{data['java_policy']}"])
        return command

    if gate == "check-policy-change":
        work_dir, rev_range = prepare_function_mapping_fixture(data["fixture_dir"])
        return [
            "python3",
            f"{ROOT_DIR}/{script}",
            rev_range,
            "--repo",
            work_dir,
            "--json",
        ]

    if gate == "bootstrap-sensitive-zones":
        command = [
            "python3",
            script,
            data["repo"],
            "--rules",
            data["rules"],
            "--json",
        ]
        if data.get("codeowners"):
            command.extend(["--codeowners", data["codeowners"]])
        if data.get("previous"):
            command.extend(["--previous", data["previous"]])
        return command

    if gate == "bootstrap-sensitive-functions":
        command = [
            "python3",
            script,
            data["repo"],
            data.get("policy", "policies/sensitive-capabilities.yaml"),
            "--json",
        ]
        if data.get("tables"):
            command.extend(["--tables", data["tables"]])
        if data.get("previous"):
            command.extend(["--previous", data["previous"]])
        return command

    if gate == "extract-sinks":
        command = [
            "python3",
            script,
            data.get("repo", "."),
            "--sensitive-zones",
            data.get("sensitive_zones", "policies/sensitive-zones.yaml"),
            "--sink-registry",
            data.get("sink_registry", "policies/sink-registry.yaml"),
            "--json",
        ]
        return command

    if gate in {"extract-callgraph", "extract-java-callgraph"}:
        return [
            "python3",
            script,
            data.get("repo", "."),
            "--json",
        ]

    if gate == "check-indirect-impact":
        work_dir, rev_range = prepare_function_mapping_fixture(data["fixture_dir"])
        return [
            "python3",
            f"{ROOT_DIR}/{script}",
            rev_range,
            "--repo",
            work_dir,
            "--sensitive-zones",
            f"{ROOT_DIR}/{data.get('sensitive_zones', 'policies/sensitive-zones.yaml')}",
            "--sink-registry",
            f"{ROOT_DIR}/{data['sink_registry']}",
            "--language-routing",
            f"{ROOT_DIR}/{data.get('language_routing', 'policies/language-routing.yaml')}",
            "--json",
        ]

    if gate == "language-router":
        return [
            "python3",
            script,
            data["name_status"],
            "--language-routing",
            data.get("language_routing", "policies/language-routing.yaml"),
            "--json",
        ]

    if gate == "check-tree-sitter-languages":
        return ["python3", script, "--json"]

    raise ValueError(f"unsupported gate: {gate}")


def values_at(records, key):
    return [record.get(key) for record in records]


def assert_equal(errors, label, actual, expected):
    if actual != expected:
        errors.append(f"{label}: expected {expected!r}, got {actual!r}")


def check_path_records(errors, label, records, expected_paths):
    actual_paths = values_at(records, "path")
    assert_equal(errors, label, actual_paths, expected_paths)


def validate_json_gate(case, result, exit_code):
    expect = case["expect"]
    errors = []
    assert_equal(errors, "exit_code", exit_code, expect["exit_code"])
    assert_equal(errors, "verdict", result.get("verdict"), expect["verdict"])

    for key in ("out_of_scope_paths", "forbidden_touched", "expected_paths", "missing_expected"):
        if key in expect:
            assert_equal(errors, key, result.get(key), expect[key])
    if "scope_too_broad" in expect:
        assert_equal(
            errors,
            "scope_too_broad.too_broad",
            result.get("scope_too_broad", {}).get("too_broad"),
            expect["scope_too_broad"],
        )
    if "scope_too_broad_reasons" in expect:
        assert_equal(
            errors,
            "scope_too_broad.reasons",
            result.get("scope_too_broad", {}).get("reasons"),
            expect["scope_too_broad_reasons"],
        )
    if "scope_top_level_dir_count" in expect:
        assert_equal(
            errors,
            "scope_too_broad.top_level_dir_count",
            result.get("scope_too_broad", {}).get("top_level_dir_count"),
            expect["scope_top_level_dir_count"],
        )
    if "scope_coverage_percent" in expect:
        assert_equal(
            errors,
            "scope_too_broad.coverage_percent",
            result.get("scope_too_broad", {}).get("coverage_percent"),
            expect["scope_coverage_percent"],
        )
    if "scope_covered_top_level_dirs" in expect:
        assert_equal(
            errors,
            "scope_too_broad.covered_top_level_dirs",
            result.get("scope_too_broad", {}).get("covered_top_level_dirs"),
            expect["scope_covered_top_level_dirs"],
        )

    for key in ("frozen_touched", "protected_touched", "watched_touched"):
        if key in expect:
            check_path_records(errors, key, result.get(key, []), expect[key])
    if "shadow_hits" in expect:
        check_path_records(errors, "shadow_hits", result.get("shadow_hits", []), expect["shadow_hits"])
    if "errors_present" in expect:
        assert_equal(errors, "errors_present", bool(result.get("errors")), expect["errors_present"])

    if "required_approval" in expect:
        approvals = values_at(result.get("protected_touched", []), "required_approval")
        assert_equal(errors, "required_approval", approvals, expect["required_approval"])

    return errors


def validate_evidence(case, result, exit_code):
    expect = case["expect"]
    errors = []
    evidence = result.get("change_evidence", {})

    assert_equal(errors, "exit_code", exit_code, expect["exit_code"])
    assert_equal(errors, "verdict", evidence.get("verdict"), expect["verdict"])
    assert_equal(errors, "summary.files_changed", evidence.get("summary", {}).get("files_changed"), expect["files_changed"])
    assert_equal(errors, "summary.lines_added", evidence.get("summary", {}).get("lines_added"), expect["lines_added"])
    assert_equal(errors, "summary.lines_removed", evidence.get("summary", {}).get("lines_removed"), expect["lines_removed"])
    assert_equal(errors, "changed_files", evidence.get("changed_files"), expect["changed_files"])
    for key in ("expected_paths", "missing_expected"):
        if key in expect:
            assert_equal(
                errors,
                f"intent_check.{key}",
                evidence.get("intent_check", {}).get(key),
                expect[key],
            )
    if "intent_status" in expect:
        assert_equal(
            errors,
            "intent_check.status",
            evidence.get("intent_check", {}).get("status"),
            expect["intent_status"],
        )
    if "changed_functions" in expect:
        actual = [
            {
                "path": item.get("path"),
                "name": item.get("name"),
                "before_line": item.get("before_line"),
                "after_line": item.get("after_line"),
                "source": item.get("source"),
            }
            for item in evidence.get("changed_functions", [])
        ]
        assert_equal(errors, "changed_functions", actual, expect["changed_functions"])
    for key in ("frozen_touched", "protected_touched", "watched_touched"):
        if key in expect:
            check_path_records(
                errors,
                f"sensitive_zone_check.{key}",
                evidence.get("sensitive_zone_check", {}).get(key, []),
                expect[key],
            )
    if "reasons_contain" in expect:
        reasons = evidence.get("reasons", [])
        for expected_reason in expect["reasons_contain"]:
            if not any(expected_reason in reason for reason in reasons):
                errors.append(f"reasons: expected entry containing {expected_reason!r}, got {reasons!r}")
    if "coverage_checked_gates" in expect:
        coverage = evidence.get("coverage_statement", {})
        checked_gates = [item.get("gate") for item in coverage.get("checked", [])]
        assert_equal(errors, "coverage.checked.gates", checked_gates, expect["coverage_checked_gates"])
    if "coverage_not_checked" in expect:
        coverage = evidence.get("coverage_statement", {})
        assert_equal(errors, "coverage.not_checked", coverage.get("not_checked"), expect["coverage_not_checked"])
    if "verdict_statement" in expect:
        coverage = evidence.get("coverage_statement", {})
        assert_equal(errors, "coverage.verdict_statement", coverage.get("verdict_statement"), expect["verdict_statement"])
    if "shadow_hits" in expect:
        check_path_records(
            errors,
            "sensitive_zone_check.shadow_hits",
            evidence.get("sensitive_zone_check", {}).get("shadow_hits", []),
            expect["shadow_hits"],
        )
    if expect.get("no_safe_text"):
        encoded = json.dumps(evidence, ensure_ascii=False).lower()
        if "safe" in encoded or "안전" in encoded:
            errors.append("coverage text: forbidden safe/안전 wording found")
    if expect.get("version_metadata_present"):
        if not evidence.get("tool_version"):
            errors.append("tool_version: expected a value")
        if not evidence.get("python_version"):
            errors.append("python_version: expected a value")
        policy_sha = evidence.get("policy_sha", {})
        for key in ("approval-routing.yaml", "sensitive-zones.yaml"):
            value = policy_sha.get(key)
            if not isinstance(value, str) or len(value) != 64:
                errors.append(f"policy_sha.{key}: expected sha256 hex value, got {value!r}")
    if expect.get("schema_keys_match_template"):
        with open("templates/change-evidence.template.yaml", "r", encoding="utf-8") as stream:
            template = yaml.safe_load(stream).get("change_evidence", {})
        assert_equal(errors, "schema.keys", sorted(evidence.keys()), sorted(template.keys()))
    return errors


def validate_inventory(case, result, exit_code):
    expect = case["expect"]
    errors = []
    assert_equal(errors, "exit_code", exit_code, expect["exit_code"])

    if expect.get("parse_error_present"):
        if not result.get("parse_error"):
            errors.append("parse_error: expected a parse error, got none")
    elif "parse_error" in expect:
        assert_equal(errors, "parse_error", result.get("parse_error"), expect["parse_error"])

    if "items" in expect:
        assert_equal(errors, "items", result.get("items"), expect["items"])
    if "lang" in expect:
        assert_equal(errors, "lang", result.get("lang"), expect["lang"])
    if expect.get("common_ir_fields"):
        for item in result.get("items", []):
            for key in ("signature_start_line", "signature", "annotations"):
                if key not in item:
                    errors.append(f"items.{item.get('name')}: missing common IR field {key}")
    return errors


def validate_language_router(case, result, exit_code):
    expect = case["expect"]
    errors = []
    assert_equal(errors, "exit_code", exit_code, expect["exit_code"])
    assert_equal(errors, "verdict", result.get("verdict"), expect["verdict"])
    if "changed_files" in expect:
        assert_equal(errors, "changed_files", result.get("changed_files"), expect["changed_files"])
    if "coverage_counts" in expect:
        actual = {
            key: len(result.get("coverage", {}).get(key, []))
            for key in ("supported", "stubbed", "unsupported")
        }
        assert_equal(errors, "coverage_counts", actual, expect["coverage_counts"])
    if "file_routes" in expect:
        actual = [
            {
                "path": item.get("path"),
                "adapter": item.get("adapter"),
                "status": item.get("status"),
                "deep_analysis": item.get("deep_analysis"),
            }
            for item in result.get("files", [])
        ]
        assert_equal(errors, "file_routes", actual, expect["file_routes"])
    if "layer_errors_present" in expect:
        actual = any(item.get("layer_errors") for item in result.get("files", []))
        assert_equal(errors, "layer_errors_present", actual, expect["layer_errors_present"])
    if expect.get("deterministic_stdout"):
        first = run_command(case_command(case)).stdout
        second = run_command(case_command(case)).stdout
        assert_equal(errors, "deterministic_stdout", first, second)
    return errors


def validate_tree_sitter(case, result, exit_code):
    expect = case["expect"]
    errors = []
    assert_equal(errors, "exit_code", exit_code, expect["exit_code"])
    assert_equal(errors, "verdict", result.get("verdict"), expect["verdict"])
    if "languages" in expect:
        assert_equal(
            errors,
            "languages",
            [item.get("language") for item in result.get("languages", [])],
            expect["languages"],
        )
    if "has_error" in expect:
        assert_equal(
            errors,
            "has_error",
            [item.get("has_error") for item in result.get("languages", [])],
            expect["has_error"],
        )
    if "versions" in expect:
        for key, value in expect["versions"].items():
            assert_equal(errors, f"versions.{key}", result.get("versions", {}).get(key), value)
    if expect.get("deterministic_stdout"):
        first = run_command(case_command(case)).stdout
        second = run_command(case_command(case)).stdout
        assert_equal(errors, "deterministic_stdout", first, second)
    return errors


def validate_function_mapping(case, result, exit_code):
    expect = case["expect"]
    errors = []
    assert_equal(errors, "exit_code", exit_code, expect["exit_code"])
    if result.get("error"):
        errors.append(f"unexpected error: {result['error']}")
        return errors
    if "coverage" in expect:
        assert_equal(errors, "coverage", result.get("coverage"), expect["coverage"])

    actual_files = []
    include_parse_error = any(
        "parse_error_present" in expected_file for expected_file in expect["files"]
    )
    for file_record in result.get("files", []):
        actual_file = {
            "path": file_record.get("path"),
            "status": file_record.get("status"),
            "touched_function_names": values_at(
                file_record.get("touched_functions", []), "name"
            ),
            "hunk_function_names": [
                values_at(hunk.get("touched_functions", []), "name")
                for hunk in file_record.get("hunks", [])
            ],
        }
        if include_parse_error:
            actual_file["parse_error_present"] = bool(file_record.get("parse_error"))
        actual_files.append(actual_file)
    assert_equal(errors, "files", actual_files, expect["files"])
    return errors


def validate_function_classification(case, result, exit_code):
    expect = case["expect"]
    errors = []
    assert_equal(errors, "exit_code", exit_code, expect["exit_code"])
    if result.get("error"):
        errors.append(f"unexpected error: {result['error']}")
        return errors
    if "coverage" in expect:
        assert_equal(errors, "coverage", result.get("coverage"), expect["coverage"])

    actual_files = []
    for file_record in result.get("files", []):
        actual_files.append(
            {
                "path": file_record.get("path"),
                "status": file_record.get("status"),
                "fallback": file_record.get("fallback"),
                "fallback_reason": file_record.get("fallback_reason"),
                "parse_error_present": bool(file_record.get("parse_error")),
                "function_changes": [
                    {
                        "name": change.get("name"),
                        "type": change.get("type"),
                        "change_type": change.get("change_type"),
                        "signature_changed": change.get("signature_changed"),
                        "body_changed": change.get("body_changed"),
                    }
                    for change in file_record.get("function_changes", [])
                ],
            }
        )
    assert_equal(errors, "files", actual_files, expect["files"])
    return errors


def annotation_summary(annotation):
    return {
        "name": annotation.get("name"),
        "type": annotation.get("type"),
        "order_key": annotation.get("order_key"),
        "level": annotation.get("level"),
        "effective_level": annotation.get("effective_level"),
        "errors": annotation.get("errors"),
        "unresolved": annotation.get("unresolved"),
        "decorators": annotation.get("decorators"),
    }


def validate_gov_annotations(case, result, exit_code):
    expect = case["expect"]
    errors = []
    assert_equal(errors, "exit_code", exit_code, expect["exit_code"])

    if "parse_error_present" in expect:
        assert_equal(errors, "parse_error_present", bool(result.get("parse_error")), expect["parse_error_present"])
    if "unreadable_present" in expect:
        assert_equal(errors, "unreadable_present", bool(result.get("unreadable")), expect["unreadable_present"])
    if "module" in expect:
        assert_equal(errors, "module", result.get("module"), expect["module"])

    if expect.get("deterministic_md5"):
        first = run_command(case_command(case)).stdout
        second = run_command(case_command(case)).stdout
        assert_equal(errors, "deterministic_stdout", first, second)

    if "annotations" in expect:
        actual = [annotation_summary(annotation) for annotation in result.get("annotations", [])]
        assert_equal(errors, "annotations", actual, expect["annotations"])
    if "annotation_metadata" in expect:
        actual = {
            annotation.get("name"): {
                "sink": annotation.get("sink"),
                "reason": annotation.get("reason"),
                "owner": annotation.get("owner"),
            }
            for annotation in result.get("annotations", [])
            if annotation.get("name") in expect["annotation_metadata"]
        }
        assert_equal(errors, "annotation_metadata", actual, expect["annotation_metadata"])

    if "annotation_count" in expect:
        assert_equal(errors, "annotation_count", len(result.get("annotations", [])), expect["annotation_count"])

    return errors


def validate_function_gov_level(case, result, exit_code):
    expect = case["expect"]
    errors = []
    assert_equal(errors, "exit_code", exit_code, expect["exit_code"])
    assert_equal(errors, "verdict", result.get("verdict"), expect["verdict"])

    for key in ("frozen_touched", "protected_touched", "watched_touched"):
        if key in expect:
            actual = [
                {
                    "path": item.get("path"),
                    "name": item.get("name"),
                    "level": item.get("level"),
                    "side": item.get("side"),
                    "errors": item.get("errors"),
                }
                for item in result.get(key, [])
            ]
            assert_equal(errors, key, actual, expect[key])

    if "errors_present" in expect:
        assert_equal(errors, "errors_present", bool(result.get("errors")), expect["errors_present"])

    if expect.get("deterministic_stdout"):
        work_dir, rev_range = prepare_function_mapping_fixture(case["input"]["fixture_dir"])
        command = [
            "python3",
            f"{ROOT_DIR}/{GATES['check-function-gov-level']}",
            rev_range,
            f"{ROOT_DIR}/policies/sensitive-zones.yaml",
            "--framework-annotations",
            f"{ROOT_DIR}/{case['input'].get('framework_annotations', 'policies/framework-annotations.yaml')}",
            "--repo",
            work_dir,
            "--json",
        ]
        first = run_command(command).stdout
        second = run_command(command).stdout
        assert_equal(errors, "deterministic_stdout", first, second)

    return errors


def capability_summary(result):
    return [
        {
            "id": capability.get("id"),
            "level": capability.get("level"),
            "signals": capability.get("signals"),
        }
        for capability in result.get("capabilities", [])
    ]


def validate_python_capabilities(case, result, exit_code):
    expect = case["expect"]
    errors = []
    assert_equal(errors, "exit_code", exit_code, expect["exit_code"])

    if "parse_error_present" in expect:
        assert_equal(errors, "parse_error_present", bool(result.get("parse_error")), expect["parse_error_present"])
    if "unreadable_present" in expect:
        assert_equal(errors, "unreadable_present", bool(result.get("unreadable")), expect["unreadable_present"])
    if "capabilities" in expect:
        assert_equal(errors, "capabilities", capability_summary(result), expect["capabilities"])
    if "capability_ids" in expect:
        assert_equal(
            errors,
            "capability_ids",
            [capability.get("id") for capability in result.get("capabilities", [])],
            expect["capability_ids"],
        )
    if "capability_levels" in expect:
        assert_equal(
            errors,
            "capability_levels",
            [capability.get("level") for capability in result.get("capabilities", [])],
            expect["capability_levels"],
        )
    if "unresolved_dynamic" in expect:
        assert_equal(errors, "unresolved_dynamic", result.get("unresolved_dynamic"), expect["unresolved_dynamic"])
    if "errors" in expect:
        assert_equal(errors, "errors", result.get("errors"), expect["errors"])

    if expect.get("deterministic_stdout"):
        first = run_command(case_command(case)).stdout
        second = run_command(case_command(case)).stdout
        assert_equal(errors, "deterministic_stdout", first, second)

    return errors


def capability_diff_summary(records):
    return [
        {
            "path": record.get("path"),
            "id": record.get("id"),
            "level": record.get("level"),
            "reviewer": record.get("reviewer"),
            "signal_names": [signal.get("name") for signal in record.get("signals", [])],
        }
        for record in records
    ]


def validate_new_capabilities(case, result, exit_code):
    expect = case["expect"]
    errors = []
    assert_equal(errors, "exit_code", exit_code, expect["exit_code"])
    assert_equal(errors, "verdict", result.get("verdict"), expect["verdict"])

    if "new_capabilities" in expect:
        assert_equal(
            errors,
            "new_capabilities",
            capability_diff_summary(result.get("new_capabilities", [])),
            expect["new_capabilities"],
        )
    if "warned_capabilities" in expect:
        assert_equal(
            errors,
            "warned_capabilities",
            capability_diff_summary(result.get("warned_capabilities", [])),
            expect["warned_capabilities"],
        )
    if "shadow_capabilities" in expect:
        assert_equal(
            errors,
            "shadow_capabilities",
            capability_diff_summary(result.get("shadow_capabilities", [])),
            expect["shadow_capabilities"],
        )
    if "fail_closed" in expect:
        assert_equal(errors, "fail_closed", result.get("fail_closed"), expect["fail_closed"])
    if "errors_present" in expect:
        assert_equal(errors, "errors_present", bool(result.get("errors")), expect["errors_present"])

    if expect.get("deterministic_stdout"):
        work_dir, rev_range = prepare_function_mapping_fixture(case["input"]["fixture_dir"])
        command = [
            "python3",
            f"{ROOT_DIR}/{GATES['check-new-capabilities']}",
            rev_range,
            f"{ROOT_DIR}/{case['input'].get('policy', 'policies/sensitive-capabilities.yaml')}",
            "--repo",
            work_dir,
            "--json",
        ]
        first = run_command(command).stdout
        second = run_command(command).stdout
        assert_equal(errors, "deterministic_stdout", first, second)

    return errors


def validate_policy_change(case, result, exit_code):
    expect = case["expect"]
    errors = []
    assert_equal(errors, "exit_code", exit_code, expect["exit_code"])
    assert_equal(errors, "verdict", result.get("verdict"), expect["verdict"])

    if "policy_loosening_kinds" in expect:
        actual = [item.get("kind") for item in result.get("policy_loosening", [])]
        assert_equal(errors, "policy_loosening_kinds", actual, expect["policy_loosening_kinds"])
    if "enforcement_bypass_kinds" in expect:
        actual = [item.get("kind") for item in result.get("enforcement_bypass", [])]
        assert_equal(errors, "enforcement_bypass_kinds", actual, expect["enforcement_bypass_kinds"])
    if "errors_present" in expect:
        assert_equal(errors, "errors_present", bool(result.get("errors")), expect["errors_present"])

    if expect.get("deterministic_stdout"):
        work_dir, rev_range = prepare_function_mapping_fixture(case["input"]["fixture_dir"])
        command = [
            "python3",
            f"{ROOT_DIR}/{GATES['check-policy-change']}",
            rev_range,
            "--repo",
            work_dir,
            "--json",
        ]
        first = run_command(command).stdout
        second = run_command(command).stdout
        assert_equal(errors, "deterministic_stdout", first, second)

    return errors


def validate_bootstrap_sensitive_zones(case, result, exit_code):
    expect = case["expect"]
    errors = []
    assert_equal(errors, "exit_code", exit_code, expect["exit_code"])
    summary = result.get("summary", {})
    candidates = result.get("candidates", [])

    if "candidate_paths" in expect:
        assert_equal(errors, "candidate_paths", values_at(candidates, "path"), expect["candidate_paths"])
    if "candidate_statuses" in expect:
        assert_equal(errors, "candidate_statuses", values_at(candidates, "status"), expect["candidate_statuses"])
    if "candidate_levels" in expect:
        assert_equal(errors, "candidate_levels", values_at(candidates, "level"), expect["candidate_levels"])
    if "candidate_count" in expect:
        assert_equal(errors, "summary.candidate_count", summary.get("candidate_count"), expect["candidate_count"])
    if "suppressed_rejected" in expect:
        assert_equal(errors, "summary.suppressed_rejected", summary.get("suppressed_rejected"), expect["suppressed_rejected"])
    if "suppressed_accepted" in expect:
        assert_equal(errors, "summary.suppressed_accepted", summary.get("suppressed_accepted"), expect["suppressed_accepted"])
    if "codeowners_read" in expect:
        assert_equal(errors, "summary.codeowners_read", summary.get("codeowners_read"), expect["codeowners_read"])
    if "evidence_sources" in expect:
        actual_sources = [
            sorted({evidence.get("source") for evidence in candidate.get("evidence", [])})
            for candidate in candidates
        ]
        assert_equal(errors, "evidence_sources", actual_sources, expect["evidence_sources"])
    if expect.get("draft_only"):
        assert_equal(errors, "mode", result.get("mode"), "draft_only")
        if "automatic" in result.get("adoption_note", "").lower():
            errors.append("adoption_note: must not imply automatic adoption")
    if expect.get("rejection_schema"):
        for candidate in candidates:
            if "rejected_reason" not in candidate or "rejected_by" not in candidate:
                errors.append(f"rejection schema missing from {candidate.get('path')}")
    if expect.get("deterministic_stdout"):
        first = run_command(case_command(case)).stdout
        second = run_command(case_command(case)).stdout
        assert_equal(errors, "deterministic_stdout", first, second)

    return errors


def validate_bootstrap_sensitive_functions(case, result, exit_code):
    expect = case["expect"]
    errors = []
    assert_equal(errors, "exit_code", exit_code, expect["exit_code"])
    summary = result.get("summary", {})
    candidates = result.get("candidates", [])

    if "candidate_functions" in expect:
        actual = [f"{candidate.get('path')}::{candidate.get('function')}" for candidate in candidates]
        assert_equal(errors, "candidate_functions", actual, expect["candidate_functions"])
    if "candidate_capabilities" in expect:
        actual = [candidate.get("capabilities") for candidate in candidates]
        assert_equal(errors, "candidate_capabilities", actual, expect["candidate_capabilities"])
    if "candidate_statuses" in expect:
        assert_equal(errors, "candidate_statuses", values_at(candidates, "status"), expect["candidate_statuses"])
    if "candidate_count" in expect:
        assert_equal(errors, "summary.candidate_count", summary.get("candidate_count"), expect["candidate_count"])
    if "suppressed_rejected" in expect:
        assert_equal(errors, "summary.suppressed_rejected", summary.get("suppressed_rejected"), expect["suppressed_rejected"])
    if "suppressed_accepted" in expect:
        assert_equal(errors, "summary.suppressed_accepted", summary.get("suppressed_accepted"), expect["suppressed_accepted"])
    if "sql_tables_loaded" in expect:
        assert_equal(errors, "summary.sql_tables_loaded", summary.get("sql_tables_loaded"), expect["sql_tables_loaded"])
    if "evidence_sources" in expect:
        actual_sources = [
            sorted({evidence.get("source") for evidence in candidate.get("evidence", [])})
            for candidate in candidates
        ]
        assert_equal(errors, "evidence_sources", actual_sources, expect["evidence_sources"])
    if expect.get("draft_only"):
        assert_equal(errors, "mode", result.get("mode"), "draft_only")
        if "automatic" in result.get("adoption_note", "").lower():
            errors.append("adoption_note: must not imply automatic adoption")
    if expect.get("limitation_statement"):
        statement = result.get("limitation_statement", "")
        for phrase in ("Pure business-critical logic", "not detected"):
            if phrase not in statement:
                errors.append(f"limitation_statement: expected phrase {phrase!r}")
    if expect.get("anchor_present"):
        for candidate in candidates:
            anchor = candidate.get("anchor") or {}
            if not anchor.get("symbol") or not anchor.get("signature_hash"):
                errors.append(f"anchor missing from {candidate.get('path')}::{candidate.get('function')}")
            if not candidate.get("fingerprint"):
                errors.append(f"fingerprint missing from {candidate.get('path')}::{candidate.get('function')}")
    if expect.get("rejection_schema"):
        for candidate in candidates:
            if "rejected_reason" not in candidate or "rejected_by" not in candidate:
                errors.append(f"rejection schema missing from {candidate.get('path')}::{candidate.get('function')}")
    if expect.get("deterministic_stdout"):
        first = run_command(case_command(case)).stdout
        second = run_command(case_command(case)).stdout
        assert_equal(errors, "deterministic_stdout", first, second)

    return errors


def sink_summary(result):
    return [
        {
            "id": sink.get("id"),
            "function": sink.get("function"),
            "source": sink.get("source"),
            "maturity": sink.get("maturity"),
            "hops": sink.get("hops"),
            "owner": sink.get("owner"),
        }
        for sink in result.get("sinks", [])
    ]


def validate_extract_sinks(case, result, exit_code):
    expect = case["expect"]
    errors = []
    assert_equal(errors, "exit_code", exit_code, expect["exit_code"])

    if "sinks" in expect:
        assert_equal(errors, "sinks", sink_summary(result), expect["sinks"])
    if "error_kinds" in expect:
        actual = [error.get("error") for error in result.get("errors", [])]
        assert_equal(errors, "error_kinds", actual, expect["error_kinds"])
    if "errors_present" in expect:
        assert_equal(errors, "errors_present", bool(result.get("errors")), expect["errors_present"])
    if expect.get("deterministic_stdout"):
        first = run_command(case_command(case)).stdout
        second = run_command(case_command(case)).stdout
        assert_equal(errors, "deterministic_stdout", first, second)

    return errors


def validate_extract_callgraph(case, result, exit_code):
    expect = case["expect"]
    errors = []
    assert_equal(errors, "exit_code", exit_code, expect["exit_code"])

    if "nodes" in expect:
        assert_equal(errors, "nodes", [node.get("id") for node in result.get("nodes", [])], expect["nodes"])
    if "edges" in expect:
        actual = [
            {
                "caller": edge.get("caller"),
                "callee": edge.get("callee"),
                "call": edge.get("call"),
            }
            for edge in result.get("edges", [])
        ]
        assert_equal(errors, "edges", actual, expect["edges"])
    if "unresolved_calls" in expect:
        actual = [
            {
                "caller": item.get("caller"),
                "kind": item.get("kind"),
                "name": item.get("name"),
            }
            for item in result.get("unresolved_calls", [])
        ]
        assert_equal(errors, "unresolved_calls", actual, expect["unresolved_calls"])
    if "coverage_unevaluated" in expect:
        actual = [
            {
                "caller": item.get("caller"),
                "kind": item.get("kind"),
                "name": item.get("name"),
            }
            for item in result.get("coverage", {}).get("unevaluated", [])
        ]
        assert_equal(errors, "coverage.unevaluated", actual, expect["coverage_unevaluated"])
    if "coverage_unevaluated_with_dispatch" in expect:
        actual = [
            {
                "caller": item.get("caller"),
                "kind": item.get("kind"),
                "name": item.get("name"),
                "dispatch_targets": item.get("dispatch_targets", []),
            }
            for item in result.get("coverage", {}).get("unevaluated", [])
        ]
        assert_equal(
            errors,
            "coverage.unevaluated_with_dispatch",
            actual,
            expect["coverage_unevaluated_with_dispatch"],
        )
    if "errors_present" in expect:
        assert_equal(errors, "errors_present", bool(result.get("errors")), expect["errors_present"])
    if expect.get("deterministic_stdout"):
        first = run_command(case_command(case)).stdout
        second = run_command(case_command(case)).stdout
        assert_equal(errors, "deterministic_stdout", first, second)

    return errors


def impact_summary(records):
    summary = []
    for record in records:
        item = {
            "sink_id": record.get("sink_id"),
            "changed_function": record.get("changed_function"),
            "path": record.get("path"),
            "hops": record.get("hops"),
            "reviewer": record.get("reviewer"),
            "maturity": record.get("maturity"),
        }
        if record.get("inferred"):
            item["inferred"] = True
        summary.append(item)
    return summary


def validate_indirect_impact(case, result, exit_code):
    expect = case["expect"]
    errors = []
    assert_equal(errors, "exit_code", exit_code, expect["exit_code"])
    assert_equal(errors, "verdict", result.get("verdict"), expect["verdict"])

    if "indirect_impact" in expect:
        assert_equal(
            errors,
            "indirect_impact",
            impact_summary(result.get("indirect_impact", [])),
            expect["indirect_impact"],
        )
    if "shadow_hits" in expect:
        assert_equal(
            errors,
            "shadow_hits",
            impact_summary(result.get("shadow_hits", [])),
            expect["shadow_hits"],
        )
    if "reviewer_required" in expect:
        assert_equal(errors, "reviewer_required", result.get("reviewer_required"), expect["reviewer_required"])
    if "fail_closed_present" in expect:
        assert_equal(errors, "fail_closed_present", bool(result.get("fail_closed")), expect["fail_closed_present"])
    if "fail_closed_details" in expect:
        assert_equal(
            errors,
            "fail_closed_details",
            [item.get("detail") for item in result.get("fail_closed", [])],
            expect["fail_closed_details"],
        )
    if "errors_present" in expect:
        assert_equal(errors, "errors_present", bool(result.get("errors")), expect["errors_present"])
    if "coverage_unevaluated" in expect:
        actual = [
            {
                "caller": item.get("caller"),
                "kind": item.get("kind"),
                "name": item.get("name"),
            }
            for item in result.get("coverage", {}).get("unevaluated", [])
        ]
        assert_equal(errors, "coverage.unevaluated", actual, expect["coverage_unevaluated"])
    if "coverage_unevaluated_with_dispatch" in expect:
        actual = [
            {
                "caller": item.get("caller"),
                "kind": item.get("kind"),
                "name": item.get("name"),
                "dispatch_targets": item.get("dispatch_targets", []),
            }
            for item in result.get("coverage", {}).get("unevaluated", [])
        ]
        assert_equal(
            errors,
            "coverage.unevaluated_with_dispatch",
            actual,
            expect["coverage_unevaluated_with_dispatch"],
        )
    if expect.get("deterministic_stdout"):
        first = run_command(case_command(case)).stdout
        second = run_command(case_command(case)).stdout
        assert_equal(errors, "deterministic_stdout", first, second)

    return errors


def main():
    with open("tests/cases.yaml", "r", encoding="utf-8") as stream:
        cases = yaml.safe_load(stream)["cases"]
    selected_names = {
        name.strip()
        for name in os.environ.get("TEST_CASE_NAME", "").split(",")
        if name.strip()
    }
    selected_group = os.environ.get("TEST_CASE_GROUP", "").strip()
    if selected_names:
        cases = [case for case in cases if case["name"] in selected_names]
    if selected_group:
        cases = [case for case in cases if case.get("group") == selected_group]
    if not cases:
        print("FAIL no test cases selected")
        return 1

    passed = 0
    failed = 0
    group_totals = {}
    group_passed = {}

    for case in cases:
        group = case.get("group", "default")
        group_totals[group] = group_totals.get(group, 0) + 1
        command = case_command(case)
        completed = run_command(command, case.get("env"))
        try:
            result = load_output(case["gate"], completed.stdout)
            if case["gate"] == "generate-change-evidence":
                errors = validate_evidence(case, result, completed.returncode)
            elif case["gate"] in ("extract-python-inventory", "extract-java-inventory"):
                errors = validate_inventory(case, result, completed.returncode)
            elif case["gate"] == "map-diff-to-functions":
                errors = validate_function_mapping(case, result, completed.returncode)
            elif case["gate"] == "classify-python-function-changes":
                errors = validate_function_classification(case, result, completed.returncode)
            elif case["gate"] == "extract-gov-annotations":
                errors = validate_gov_annotations(case, result, completed.returncode)
            elif case["gate"] == "check-function-gov-level":
                errors = validate_function_gov_level(case, result, completed.returncode)
            elif case["gate"] in ("extract-python-capabilities", "extract-java-capabilities"):
                errors = validate_python_capabilities(case, result, completed.returncode)
            elif case["gate"] == "check-new-capabilities":
                errors = validate_new_capabilities(case, result, completed.returncode)
            elif case["gate"] == "check-policy-change":
                errors = validate_policy_change(case, result, completed.returncode)
            elif case["gate"] == "bootstrap-sensitive-zones":
                errors = validate_bootstrap_sensitive_zones(case, result, completed.returncode)
            elif case["gate"] == "bootstrap-sensitive-functions":
                errors = validate_bootstrap_sensitive_functions(case, result, completed.returncode)
            elif case["gate"] == "extract-sinks":
                errors = validate_extract_sinks(case, result, completed.returncode)
            elif case["gate"] in {"extract-callgraph", "extract-java-callgraph"}:
                errors = validate_extract_callgraph(case, result, completed.returncode)
            elif case["gate"] == "check-indirect-impact":
                errors = validate_indirect_impact(case, result, completed.returncode)
            elif case["gate"] == "language-router":
                errors = validate_language_router(case, result, completed.returncode)
            elif case["gate"] == "check-tree-sitter-languages":
                errors = validate_tree_sitter(case, result, completed.returncode)
            else:
                errors = validate_json_gate(case, result, completed.returncode)
        except Exception as error:
            errors = [f"could not validate output: {error}"]

        if errors:
            failed += 1
            print(f"FAIL {case['name']} ({case['gate']})")
            for error in errors:
                print(f"  - {error}")
            if completed.stderr:
                print("  stderr:")
                print(completed.stderr.rstrip())
            if completed.stdout:
                print("  stdout:")
                print(completed.stdout.rstrip())
        else:
            passed += 1
            group_passed[group] = group_passed.get(group, 0) + 1
            print(f"PASS {case['name']} ({case['gate']})")

    print(f"Summary: {passed}/{len(cases)} PASS")
    for group in sorted(group_totals):
        print(f"Group {group}: {group_passed.get(group, 0)}/{group_totals[group]} PASS")
    if failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
PY
