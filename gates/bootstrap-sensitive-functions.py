#!/usr/bin/env python3
import argparse
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath

import yaml


GATE_DIR = Path(__file__).resolve().parent
CAPABILITIES_GATE = GATE_DIR / "extract-python-capabilities.py"
INVENTORY_GATE = GATE_DIR / "extract-python-inventory.py"


def load_gate_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


capability_gate = load_gate_module(CAPABILITIES_GATE, "extract_python_capabilities")
inventory_gate = load_gate_module(INVENTORY_GATE, "extract_python_inventory")


def normalize_path(path):
    normalized = str(PurePosixPath(str(path).replace("\\", "/")))
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def load_yaml(path):
    with open(path, "r", encoding="utf-8") as stream:
        return yaml.safe_load(stream) or {}


def iter_python_files(repo):
    root = Path(repo)
    for current_root, dirs, files in os.walk(root):
        dirs[:] = sorted(name for name in dirs if name != ".git")
        for name in sorted(files):
            if name.endswith(".py"):
                path = Path(current_root, name)
                yield path, normalize_path(path.relative_to(root))


def read_text(path):
    with open(path, "r", encoding="utf-8") as stream:
        return stream.read()


def function_signature_hash(source, source_path, item):
    if item["name"] == "<module>":
        signature = "<module>"
    else:
        try:
            tree = ast.parse(source, filename=source_path)
        except SyntaxError:
            signature = item["name"]
        else:
            signature = item["name"]
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.lineno != item["start_line"]:
                    continue
                prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
                signature = f"{prefix}{item['name']}({ast.unparse(node.args)})"
                break
    return hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]


def deepest_function_for_line(items, line):
    functions = [
        item
        for item in items
        if item.get("type") in ("function", "async_function")
        and item.get("start_line") <= line <= item.get("end_line")
    ]
    if not functions:
        return {
            "type": "module",
            "name": "<module>",
            "start_line": 1,
            "end_line": 10**12,
            "decorators": [],
        }
    return sorted(
        functions,
        key=lambda item: (item["end_line"] - item["start_line"], item["start_line"], item["name"]),
    )[0]


def candidate_key(path, item):
    return (path, item["name"], item["start_line"])


def evidence_key(evidence):
    return (
        evidence.get("source", ""),
        evidence.get("capability_id", ""),
        evidence.get("table", ""),
        evidence.get("kind", ""),
        evidence.get("name", ""),
        evidence.get("line", 0),
    )


def candidate_fingerprint(anchor, capabilities, evidence):
    identity = {
        "anchor": anchor,
        "capabilities": sorted(capabilities),
        "evidence": [
            {
                "source": item.get("source"),
                "capability_id": item.get("capability_id"),
                "table": item.get("table"),
                "kind": item.get("kind"),
                "name": item.get("name"),
            }
            for item in sorted(evidence, key=evidence_key)
        ],
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def add_candidate(candidates, path, source, item, capability_id, evidence):
    key = candidate_key(path, item)
    if key not in candidates:
        anchor = {
            "symbol": f"{path}::{item['name']}",
            "signature_hash": function_signature_hash(source, path, item),
        }
        candidates[key] = {
            "path": path,
            "function": item["name"],
            "type": item["type"],
            "start_line": item["start_line"],
            "end_line": None if item["end_line"] == 10**12 else item["end_line"],
            "anchor": anchor,
            "capabilities": set(),
            "evidence": [],
        }
    candidates[key]["capabilities"].add(capability_id)
    candidates[key]["evidence"].append(evidence)


def capability_candidates(repo, policy_path):
    candidates = {}
    errors = []
    for abs_path, rel_path in iter_python_files(repo):
        result = capability_gate.extract_capabilities(str(abs_path), policy_path)
        if result.get("parse_error") or result.get("unreadable"):
            errors.append(
                {
                    "path": rel_path,
                    "parse_error": result.get("parse_error"),
                    "unreadable": result.get("unreadable"),
                }
            )
            continue
        source = read_text(abs_path)
        inventory = inventory_gate.extract_inventory(source, rel_path)
        items = inventory.get("items", [])
        for capability in result.get("capabilities", []):
            for signal in capability.get("signals", []):
                item = deepest_function_for_line(items, signal["line"])
                add_candidate(
                    candidates,
                    rel_path,
                    source,
                    item,
                    capability["id"],
                    {
                        "source": "capability_signal",
                        "capability_id": capability["id"],
                        "kind": signal["kind"],
                        "name": signal["name"],
                        "line": signal["line"],
                    },
                )
    return candidates, errors


def load_table_names(path):
    if not path:
        return []
    data = load_yaml(path) if path.endswith((".yaml", ".yml")) else None
    if isinstance(data, dict):
        values = data.get("tables") or data.get("table_names") or []
    elif isinstance(data, list):
        values = data
    else:
        with open(path, "r", encoding="utf-8") as stream:
            values = [line.strip() for line in stream]
    return sorted({str(value).strip().lower() for value in values if str(value).strip()})


def string_nodes_by_function(source, source_path, items):
    tree = ast.parse(source, filename=source_path)
    records = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            records.append((node.lineno, node.value.lower()))
    mapped = []
    for line, value in records:
        mapped.append((deepest_function_for_line(items, line), line, value))
    return mapped


def table_candidates(repo, table_names):
    candidates = {}
    errors = []
    if not table_names:
        return candidates, errors
    for abs_path, rel_path in iter_python_files(repo):
        try:
            source = read_text(abs_path)
            inventory = inventory_gate.extract_inventory(source, rel_path)
            if inventory.get("parse_error"):
                errors.append({"path": rel_path, "parse_error": inventory.get("parse_error")})
                continue
            for item, line, value in string_nodes_by_function(source, rel_path, inventory.get("items", [])):
                for table in table_names:
                    if table not in value:
                        continue
                    add_candidate(
                        candidates,
                        rel_path,
                        source,
                        item,
                        f"sql_table:{table}",
                        {
                            "source": "sql_table_reference",
                            "capability_id": f"sql_table:{table}",
                            "table": table,
                            "kind": "string_literal",
                            "name": table,
                            "line": line,
                        },
                    )
        except (OSError, UnicodeDecodeError, SyntaxError) as error:
            errors.append({"path": rel_path, "error": str(error)})
    return candidates, errors


def merge_candidate_maps(*maps):
    merged = {}
    for candidate_map in maps:
        for key, candidate in candidate_map.items():
            if key not in merged:
                merged[key] = {
                    **candidate,
                    "capabilities": set(candidate["capabilities"]),
                    "evidence": list(candidate["evidence"]),
                }
                continue
            merged[key]["capabilities"].update(candidate["capabilities"])
            merged[key]["evidence"].extend(candidate["evidence"])
    return merged


def previous_records(path):
    if not path:
        return {}, []
    data = load_yaml(path)
    if "bootstrap_sensitive_functions" in data:
        data = data.get("bootstrap_sensitive_functions") or {}
    fingerprints = {}
    anchors = []
    for candidate in data.get("candidates", []):
        fingerprint = candidate.get("fingerprint")
        status = candidate.get("status")
        anchor = candidate.get("anchor") or {}
        if fingerprint and status in ("accepted", "rejected"):
            fingerprints[fingerprint] = status
        if status in ("accepted", "rejected") and anchor:
            anchors.append(anchor)
    return fingerprints, anchors


def anchor_note(anchor, previous_anchors):
    for previous in previous_anchors:
        if previous.get("symbol") == anchor["symbol"] and previous.get("signature_hash") != anchor["signature_hash"]:
            return "anchor signature changed; re-confirm this function before adoption"
        if previous.get("symbol") != anchor["symbol"] and previous.get("signature_hash") == anchor["signature_hash"]:
            return "possible move or rename; re-confirm this function before adoption"
    return None


def finalize_candidates(candidate_map, previous_path=None):
    previous_fingerprints, previous_anchors = previous_records(previous_path)
    candidates = []
    suppressed = {"accepted": 0, "rejected": 0}
    for candidate in sorted(candidate_map.values(), key=lambda item: (item["path"], item["function"], item["start_line"])):
        capabilities = sorted(candidate["capabilities"])
        evidence = sorted(candidate["evidence"], key=evidence_key)
        fingerprint = candidate_fingerprint(candidate["anchor"], capabilities, evidence)
        previous_status = previous_fingerprints.get(fingerprint)
        if previous_status == "accepted":
            suppressed["accepted"] += 1
            continue
        if previous_status == "rejected":
            suppressed["rejected"] += 1
            continue
        record = {
            "path": candidate["path"],
            "function": candidate["function"],
            "type": candidate["type"],
            "start_line": candidate["start_line"],
            "end_line": candidate["end_line"],
            "anchor": candidate["anchor"],
            "capabilities": capabilities,
            "status": "proposed",
            "fingerprint": fingerprint,
            "rejected_reason": None,
            "rejected_by": None,
            "review_note": anchor_note(candidate["anchor"], previous_anchors),
            "evidence": evidence,
        }
        candidates.append(record)
    return candidates, suppressed


def build_draft(repo, policy_path, tables_path=None, previous_path=None):
    capability_map, capability_errors = capability_candidates(repo, policy_path)
    table_map, table_errors = table_candidates(repo, load_table_names(tables_path))
    candidates, suppressed = finalize_candidates(
        merge_candidate_maps(capability_map, table_map),
        previous_path,
    )
    return {
        "bootstrap_sensitive_functions": {
            "generated_by": "bootstrap-sensitive-functions",
            "source_repo": os.path.abspath(repo).replace("\\", "/"),
            "mode": "draft_only",
            "adoption_note": "Candidates are proposed only; apply them manually after review.",
            "limitation_statement": (
                "This scanner only finds functions with deterministic capability or configured SQL table signals. "
                "Pure business-critical logic with no such signal is not detected by this scanner or the current harness; "
                "later sink tracing and unknown-code review are needed for that gap."
            ),
            "summary": {
                "candidate_count": len(candidates),
                "suppressed_accepted": suppressed["accepted"],
                "suppressed_rejected": suppressed["rejected"],
                "sql_tables_loaded": len(load_table_names(tables_path)),
            },
            "candidates": candidates,
            "errors": sorted(capability_errors + table_errors, key=lambda item: json.dumps(item, sort_keys=True)),
        }
    }


def main():
    parser = argparse.ArgumentParser(
        description="Generate a draft sensitive-functions candidate list from capability and SQL table signals."
    )
    parser.add_argument("repo", help="target repository path to scan")
    parser.add_argument(
        "policy",
        nargs="?",
        default="policies/sensitive-capabilities.yaml",
        help="sensitive capability catalog yaml",
    )
    parser.add_argument("--tables", help="optional YAML/text file with sensitive SQL table names")
    parser.add_argument("--previous", help="optional previous draft with accepted/rejected fingerprints")
    parser.add_argument("--json", action="store_true", help="print structured JSON")
    args = parser.parse_args()

    try:
        result = build_draft(args.repo, args.policy, args.tables, args.previous)
    except Exception as error:
        result = {
            "bootstrap_sensitive_functions": {
                "generated_by": "bootstrap-sensitive-functions",
                "source_repo": os.path.abspath(args.repo).replace("\\", "/"),
                "mode": "draft_only",
                "summary": {
                    "candidate_count": 0,
                    "suppressed_accepted": 0,
                    "suppressed_rejected": 0,
                    "sql_tables_loaded": 0,
                },
                "candidates": [],
                "errors": [str(error)],
            }
        }
        if args.json:
            print(json.dumps(result["bootstrap_sensitive_functions"], ensure_ascii=False, sort_keys=True, indent=2))
        else:
            print(yaml.safe_dump(result, allow_unicode=True, sort_keys=False))
        return 1

    if args.json:
        print(json.dumps(result["bootstrap_sensitive_functions"], ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(yaml.safe_dump(result, allow_unicode=True, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
