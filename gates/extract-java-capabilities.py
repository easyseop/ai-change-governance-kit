#!/usr/bin/env python3
import argparse
import importlib.util
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import yaml


DEFAULT_POLICY = "policies/java-sensitive-capabilities.yaml"
VALID_LEVELS = {"protected", "watched"}
VALID_MATURITY = {"enforcing", "shadow"}
DEFAULT_INVALID_LEVEL = "protected"
LEVEL_STRENGTH = {"watched": 0, "protected": 1}
GATE_DIR = Path(__file__).resolve().parent
UNRESOLVED_DYNAMIC_METHODS = {"forName", "getMethod", "loadClass", "invoke", "newInstance"}
UNINFORMATIVE_RECEIVER_TYPES = {"Object", "var"}


def load_gate_module(filename, module_name):
    spec = importlib.util.spec_from_file_location(module_name, GATE_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


java_inventory_gate = load_gate_module("extract-java-inventory.py", "extract_java_inventory_gate")


def read_source(path):
    if path == "-":
        return sys.stdin.read()
    with open(path, "r", encoding="utf-8") as stream:
        return stream.read()


def signal_list(signals, key):
    values = signals.get(key) or []
    return [str(value) for value in values]


def load_catalog(path):
    with open(path, "r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream) or {}

    capabilities = []
    errors = []
    seen_ids = set()
    for raw in data.get("capabilities") or []:
        cap_id = raw.get("id")
        if not cap_id:
            errors.append({"error": "missing_capability_id"})
            continue
        if cap_id in seen_ids:
            errors.append({"error": "duplicate_capability_id", "id": cap_id})
        seen_ids.add(cap_id)

        level = raw.get("level")
        if level not in VALID_LEVELS:
            errors.append({"error": "invalid_capability_level", "id": cap_id, "level": level})
            level = DEFAULT_INVALID_LEVEL
        maturity = raw.get("maturity", "enforcing")
        if maturity not in VALID_MATURITY:
            errors.append({"error": "invalid_maturity", "id": cap_id, "maturity": maturity})
            maturity = "enforcing"

        signals = raw.get("signals") or {}
        known = {"imports", "types", "calls", "methods"}
        for key in sorted(set(signals) - known):
            errors.append({"error": "unknown_signal_kind", "id": cap_id, "kind": key})

        capabilities.append(
            {
                "id": cap_id,
                "level": level,
                "maturity": maturity,
                "reason": raw.get("reason"),
                "reviewer": raw.get("reviewer"),
                "imports": signal_list(signals, "imports"),
                "types": signal_list(signals, "types"),
                "calls": signal_list(signals, "calls"),
                "methods": signal_list(signals, "methods"),
            }
        )
    return capabilities, errors


def build_indexes(capabilities):
    import_index = defaultdict(list)
    type_index = defaultdict(list)
    call_index = defaultdict(list)
    method_index = defaultdict(list)
    for cap in capabilities:
        for name in cap["imports"]:
            import_index[name].append(cap)
        for name in cap["types"]:
            type_index[simple_name(name)].append(cap)
        for name in cap["calls"]:
            call_index[name].append(cap)
        for name in cap["methods"]:
            method_index[name].append(cap)
    return import_index, type_index, call_index, method_index


def simple_name(name):
    return str(name).split(".")[-1]


def node_text(source_bytes, node):
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def node_line(node):
    return node.start_point.row + 1


def walk(node):
    yield node
    for child in node.named_children:
        yield from walk(child)


def parse_java(source):
    source_bytes = source.encode("utf-8")
    parser = java_inventory_gate.make_java_parser()
    tree = parser.parse(source_bytes)
    if tree.root_node.has_error:
        raise SyntaxError("syntax error")
    return source_bytes, tree


def import_name(source_bytes, node):
    text = node_text(source_bytes, node).strip()
    text = re.sub(r"^import\s+static\s+", "import ", text)
    text = re.sub(r"^import\s+", "", text).rstrip(";").strip()
    return text


def import_matches(imported, signal):
    return imported == signal or imported.startswith(f"{signal}.") or imported == f"{signal}.*"


def type_name_from_node(source_bytes, node):
    if node is None:
        return None
    return simple_name(node_text(source_bytes, node).strip())


def variable_type_bindings(source_bytes, tree):
    bindings = {}
    for node in walk(tree.root_node):
        if node.type not in {
            "local_variable_declaration",
            "field_declaration",
            "formal_parameter",
        }:
            continue
        type_node = node.child_by_field_name("type")
        type_name = type_name_from_node(source_bytes, type_node)
        if not type_name:
            continue
        for child in node.named_children:
            if child.type == "variable_declarator":
                name_node = child.child_by_field_name("name")
                if name_node is not None:
                    bindings[node_text(source_bytes, name_node).strip()] = type_name
            elif child.type == "identifier" and node.type == "formal_parameter":
                bindings[node_text(source_bytes, child).strip()] = type_name
    return bindings


def call_root_type(source_bytes, node, bindings):
    if node is None:
        return None
    if node.type == "object_creation_expression":
        return type_name_from_node(source_bytes, node.child_by_field_name("type"))
    if node.type == "method_invocation":
        return call_root_type(source_bytes, node.child_by_field_name("object"), bindings)
    if node.type in {"identifier", "scoped_identifier", "field_access"}:
        text = node_text(source_bytes, node).strip()
        candidate = simple_name(text)
        if candidate in bindings:
            return bindings[candidate]
        return candidate if candidate[:1].isupper() else None
    return None


def add_signal(found, cap, signal):
    record = found[cap["id"]]
    if not record:
        record.update(
            {
                "id": cap["id"],
                "level": cap["level"],
                "maturity": cap["maturity"],
                "signals": [],
            }
        )
    elif LEVEL_STRENGTH.get(cap["level"], 0) > LEVEL_STRENGTH.get(record["level"], 0):
        record["level"] = cap["level"]
    if signal not in record["signals"]:
        record["signals"].append(signal)


def collect_signals(source_bytes, tree, capabilities):
    import_index, type_index, call_index, method_index = build_indexes(capabilities)
    bindings = variable_type_bindings(source_bytes, tree)
    found = defaultdict(dict)
    unresolved_dynamic = []

    for node in walk(tree.root_node):
        if node.type == "import_declaration":
            imported = import_name(source_bytes, node)
            for signal, caps in import_index.items():
                if import_matches(imported, signal):
                    for cap in caps:
                        add_signal(
                            found,
                            cap,
                            {"kind": "imports", "name": imported, "line": node_line(node)},
                        )

        if node.type in {"type_identifier", "scoped_type_identifier", "scoped_identifier", "field_access"}:
            type_name = simple_name(node_text(source_bytes, node).strip())
            for cap in type_index.get(type_name, []):
                add_signal(
                    found,
                    cap,
                    {"kind": "types", "name": type_name, "line": node_line(node)},
                )

        if node.type == "method_invocation":
            method_node = node.child_by_field_name("name")
            method = node_text(source_bytes, method_node).strip() if method_node is not None else None
            root_type = call_root_type(source_bytes, node.child_by_field_name("object"), bindings)
            if method and root_type:
                call_name = f"{root_type}.{method}"
                for cap in call_index.get(call_name, []):
                    add_signal(
                        found,
                        cap,
                        {"kind": "calls", "name": call_name, "line": node_line(node)},
                    )
            if method:
                for cap in method_index.get(method, []):
                    add_signal(
                        found,
                        cap,
                        {"kind": "methods", "name": method, "line": node_line(node)},
                    )
                if method in UNRESOLVED_DYNAMIC_METHODS and (
                    not root_type or root_type in UNINFORMATIVE_RECEIVER_TYPES
                ):
                    unresolved_dynamic.append(
                        {
                            "kind": "method_invocation",
                            "name": method,
                            "line": node_line(node),
                            "reason": "receiver type unresolved",
                        }
                    )

    return list(found.values()), sorted(
        unresolved_dynamic, key=lambda item: (item["line"], item["name"])
    )


def public_capabilities(found):
    capabilities = []
    for item in found:
        capabilities.append(
            {
                "id": item["id"],
                "level": item["level"],
                "signals": sorted(
                    item["signals"],
                    key=lambda signal: (signal["kind"], signal["name"], signal["line"]),
                ),
            }
        )
    return sorted(capabilities, key=lambda item: item["id"])


def extract_from_source(source, path, policy_path):
    capabilities, catalog_errors = load_catalog(policy_path)
    try:
        source_bytes, tree = parse_java(source)
    except (ImportError, OSError) as error:
        return {
            "path": path,
            "lang": "java",
            "capabilities": [],
            "unresolved_dynamic": [],
            "errors": catalog_errors,
            "parse_error": f"java analysis unavailable: {error}",
            "unreadable": False,
        }
    except SyntaxError as error:
        return {
            "path": path,
            "lang": "java",
            "capabilities": [],
            "unresolved_dynamic": [],
            "errors": catalog_errors,
            "parse_error": str(error),
            "unreadable": False,
        }

    found, unresolved_dynamic = collect_signals(source_bytes, tree, capabilities)
    return {
        "path": path,
        "lang": "java",
        "capabilities": public_capabilities(found),
        "unresolved_dynamic": unresolved_dynamic,
        "errors": catalog_errors,
        "parse_error": False,
        "unreadable": False,
    }


def extract_capabilities(path, policy_path):
    try:
        source = read_source(path)
    except UnicodeDecodeError as error:
        _, catalog_errors = load_catalog(policy_path)
        return {
            "path": path,
            "lang": "java",
            "capabilities": [],
            "unresolved_dynamic": [],
            "errors": catalog_errors,
            "parse_error": False,
            "unreadable": f"unreadable source: {error}",
        }
    return extract_from_source(source, path, policy_path)


def print_text(result):
    if result.get("parse_error"):
        print(f"parse_error: {result['parse_error']}")
    if result.get("unreadable"):
        print(f"unreadable: {result['unreadable']}")
    for capability in result["capabilities"]:
        signal_text = ",".join(
            f"{signal['kind']}:{signal['name']}:{signal['line']}"
            for signal in capability["signals"]
        )
        print(f"{capability['id']} level={capability['level']} signals={signal_text}")
    for item in result.get("unresolved_dynamic", []):
        print(f"unresolved_dynamic: {item['name']} line={item['line']}")


def main():
    parser = argparse.ArgumentParser(
        description="Extract deterministic sensitive Java capabilities from a source file."
    )
    parser.add_argument("source_file", help="Java source file to inspect")
    parser.add_argument("policy", nargs="?", default=DEFAULT_POLICY, help="Java capability catalog yaml")
    parser.add_argument("--json", action="store_true", help="print structured JSON")
    args = parser.parse_args()

    result = extract_capabilities(args.source_file, args.policy)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print_text(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
