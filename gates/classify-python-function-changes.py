#!/usr/bin/env python3
import argparse
import ast
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from pathlib import PurePosixPath


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
    stdout = result.stdout.decode("utf-8")
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


def parse_name_status(output):
    records = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith(("R", "C")) and len(parts) >= 3:
            path = normalize_path(parts[2])
        elif len(parts) >= 2:
            path = normalize_path(parts[1])
        else:
            continue
        records.append({"path": path, "status": status})
    return records


def source_at_ref(ref, path, repo):
    return run_git(["show", f"{ref}:{path}"], repo)


def decorator_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = decorator_name(node.value)
        if parent:
            return f"{parent}.{node.attr}"
        return node.attr
    if isinstance(node, ast.Call):
        return decorator_name(node.func)
    if isinstance(node, ast.Subscript):
        return decorator_name(node.value)
    return ast.unparse(node)


def node_type(node):
    if isinstance(node, ast.AsyncFunctionDef):
        return "async_function"
    if isinstance(node, ast.FunctionDef):
        return "function"
    return "class"


def signature_dump(node):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return repr(
            (
                type(node).__name__,
                ast.dump(node.args, include_attributes=False),
                [ast.dump(decorator, include_attributes=False) for decorator in node.decorator_list],
                ast.dump(node.returns, include_attributes=False) if node.returns else None,
                node.type_comment,
            )
        )
    return repr(
        (
            type(node).__name__,
            [ast.dump(base, include_attributes=False) for base in node.bases],
            [ast.dump(keyword, include_attributes=False) for keyword in node.keywords],
            [ast.dump(decorator, include_attributes=False) for decorator in node.decorator_list],
        )
    )


def body_dump(node):
    return repr([ast.dump(statement, include_attributes=False) for statement in node.body])


def inventory_item(node, name):
    decorators = [decorator_name(decorator) for decorator in node.decorator_list]
    return {
        "type": node_type(node),
        "name": name,
        "start_line": node.lineno,
        "end_line": getattr(node, "end_lineno", node.lineno),
        "decorators": decorators,
        "_decorator_key": tuple(sorted(decorators)),
        "_signature_dump": signature_dump(node),
        "_body_dump": body_dump(node),
    }


class InventoryVisitor(ast.NodeVisitor):
    def __init__(self):
        self.items = []
        self.parents = []

    def normalized_name(self, name):
        if not self.parents:
            return name
        return ".".join(self.parents + [name])

    def visit_scoped_node(self, node):
        name = self.normalized_name(node.name)
        self.items.append(inventory_item(node, name))
        self.parents.append(node.name)
        self.generic_visit(node)
        self.parents.pop()

    def visit_ClassDef(self, node):
        self.visit_scoped_node(node)

    def visit_FunctionDef(self, node):
        self.visit_scoped_node(node)

    def visit_AsyncFunctionDef(self, node):
        self.visit_scoped_node(node)


def extract_inventory(source, source_path):
    try:
        tree = ast.parse(source, filename=source_path)
    except SyntaxError as error:
        return {
            "source": source_path,
            "items": [],
            "parse_error": f"{error.msg} at line {error.lineno}, column {error.offset}",
        }

    visitor = InventoryVisitor()
    visitor.visit(tree)
    assign_occurrence_keys(visitor.items)
    return {
        "source": source_path,
        "items": visitor.items,
        "parse_error": None,
    }


def load_java_inventory_module():
    default_path = Path(__file__).resolve().parent / "extract-java-inventory.py"
    override_path = os.environ.get("ACGH_JAVA_INVENTORY_PATH")
    if override_path and os.environ.get("ACGH_ALLOW_TEST_OVERRIDES") == "1":
        path = Path(override_path)
    else:
        path = default_path
    if not path.exists():
        raise ImportError(f"java inventory gate missing: {path}")
    spec = importlib.util.spec_from_file_location("extract_java_inventory_gate", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"java inventory gate could not be loaded: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def override_coverage():
    override_path = os.environ.get("ACGH_JAVA_INVENTORY_PATH")
    if override_path and os.environ.get("ACGH_ALLOW_TEST_OVERRIDES") != "1":
        return [
            "ignored ACGH_JAVA_INVENTORY_PATH override because ACGH_ALLOW_TEST_OVERRIDES=1 was not set"
        ]
    return []


def java_match_signature(signature):
    remaining = signature
    while remaining.startswith("@"):
        depth = 0
        index = 0
        while index < len(remaining):
            char = remaining[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth = max(0, depth - 1)
            elif char.isspace() and depth == 0:
                break
            index += 1
        remaining = remaining[index:].strip()
    return remaining


def augment_java_inventory(source, inventory):
    lines = source.splitlines()
    for item in inventory["items"]:
        annotations = item.get("annotations", [])
        item["decorators"] = annotations
        item["_decorator_key"] = tuple(sorted(annotations))
        item["_match_base"] = (item["name"], java_match_signature(item["signature"]))
        item["_signature_dump"] = item["signature"]
        start = max(0, item["start_line"] - 1)
        end = max(start, item["end_line"])
        item["_body_dump"] = "\n".join(lines[start:end])
    assign_occurrence_keys(inventory["items"])
    return inventory


def extract_inventory_for_language(source, source_path, language):
    if language == "java":
        try:
            module = load_java_inventory_module()
            return augment_java_inventory(source, module.extract_inventory(source, source_path))
        except (ImportError, OSError) as error:
            return {
                "source": source_path,
                "lang": "java",
                "items": [],
                "parse_error": f"java analysis unavailable: {error}",
            }
    return extract_inventory(source, source_path)


def assign_occurrence_keys(items):
    counts = {}
    for item in items:
        base_key = item.get("_match_base", (item["name"], item["_decorator_key"]))
        occurrence = counts.get(base_key, 0)
        counts[base_key] = occurrence + 1
        item["_match_key"] = (*base_key, occurrence)


def public_item(item):
    return {
        "name": item["name"],
        "type": item["type"],
        "start_line": item["start_line"],
        "end_line": item["end_line"],
        "decorators": item.get("decorators", item.get("annotations", [])),
    }


def classified_record(change_type, item, before_item=None, after_item=None):
    record = public_item(item)
    record["change_type"] = change_type
    record["signature_changed"] = False
    record["body_changed"] = False

    if change_type == "modified":
        record["before_start_line"] = before_item["start_line"]
        record["before_end_line"] = before_item["end_line"]
        record["after_start_line"] = after_item["start_line"]
        record["after_end_line"] = after_item["end_line"]
        record["signature_changed"] = (
            before_item["_signature_dump"] != after_item["_signature_dump"]
        )
        record["body_changed"] = before_item["_body_dump"] != after_item["_body_dump"]

    return record


def fallback_file(path, status, reason, parse_error=None):
    language = "python" if path.endswith(".py") else "java" if path.endswith(".java") else "unsupported"
    return {
        "path": path,
        "status": status,
        "language": language,
        "parse_error": parse_error,
        "fallback": True,
        "fallback_reason": reason,
        "function_changes": [
            {
                "name": "<file>",
                "type": "file",
                "change_type": "file_fallback",
                "signature_changed": False,
                "body_changed": False,
            }
        ],
    }


def classify_inventory_changes(before_inventory, after_inventory):
    before_by_key = {item["_match_key"]: item for item in before_inventory["items"]}
    after_by_key = {item["_match_key"]: item for item in after_inventory["items"]}
    changes = []

    for key in sorted(before_by_key):
        if key not in after_by_key:
            changes.append(classified_record("deleted", before_by_key[key]))

    for key in sorted(after_by_key):
        if key not in before_by_key:
            changes.append(classified_record("added", after_by_key[key]))
            continue

        before_item = before_by_key[key]
        after_item = after_by_key[key]
        signature_changed = (
            before_item["_signature_dump"] != after_item["_signature_dump"]
        )
        body_changed = before_item["_body_dump"] != after_item["_body_dump"]
        if signature_changed or body_changed:
            changes.append(
                classified_record(
                    "modified",
                    after_item,
                    before_item=before_item,
                    after_item=after_item,
                )
            )

    return sorted(
        changes,
        key=lambda item: (
            item["name"],
            item["change_type"],
            item.get("after_start_line", item["start_line"]),
            item["start_line"],
        ),
    )


def classify_file(path, status, base, head, repo):
    language = "python" if path.endswith(".py") else "java" if path.endswith(".java") else "unsupported"
    if language == "unsupported":
        return fallback_file(path, status, "unsupported_language")

    if status.startswith("A") or status.startswith("D"):
        return fallback_file(path, status, "file_added_or_deleted")

    if status.startswith(("R", "C")):
        return fallback_file(path, status, "renamed_or_copied")

    try:
        before_inventory = extract_inventory_for_language(
            source_at_ref(base, path, repo), path, language
        )
    except (RuntimeError, UnicodeDecodeError, UnicodeEncodeError, ImportError, OSError) as error:
        return fallback_file(
            path,
            status,
            "unreadable",
            {"before": str(error), "after": None},
        )
    if before_inventory["parse_error"]:
        return fallback_file(
            path,
            status,
            "parse_error",
            {"before": before_inventory["parse_error"], "after": None},
        )

    try:
        after_inventory = extract_inventory_for_language(
            source_at_ref(head, path, repo), path, language
        )
    except (RuntimeError, UnicodeDecodeError, UnicodeEncodeError, ImportError, OSError) as error:
        return fallback_file(
            path,
            status,
            "unreadable",
            {"before": None, "after": str(error)},
        )
    if after_inventory["parse_error"]:
        return fallback_file(
            path,
            status,
            "parse_error",
            {"before": None, "after": after_inventory["parse_error"]},
        )

    return {
        "path": path,
        "status": status,
        "language": language,
        "parse_error": None,
        "fallback": False,
        "fallback_reason": None,
        "function_changes": classify_inventory_changes(before_inventory, after_inventory),
    }


def classify_python_function_changes(rev_range, repo="."):
    base, head = split_rev_range(rev_range)
    base_commit = run_git(["rev-parse", base], repo).strip()
    head_commit = run_git(["rev-parse", head], repo).strip()
    status_records = parse_name_status(
        run_git(["diff", "--name-status", "--no-renames", rev_range], repo)
    )
    files = [
        classify_file(record["path"], record["status"], base, head, repo)
        for record in sorted(status_records, key=lambda item: item["path"])
    ]
    return {
        "gate": "classify-python-function-changes",
        "base_commit": base_commit,
        "head_commit": head_commit,
        "files": files,
        "coverage": override_coverage(),
    }


def print_text(result):
    for file_record in result["files"]:
        print(f"{file_record['status']} {file_record['path']}")
        if file_record["fallback"]:
            print(f"  fallback: {file_record['fallback_reason']}")
        for change in file_record["function_changes"]:
            detail = change["change_type"]
            if change["change_type"] == "modified":
                detail = (
                    f"modified signature_changed={change['signature_changed']} "
                    f"body_changed={change['body_changed']}"
                )
            print(f"  {change['type']} {change['name']} {detail}")


def main():
    parser = argparse.ArgumentParser(
        description="Classify Python function/class changes as added, modified, or deleted."
    )
    parser.add_argument("diff_input", help="git diff ref range, for example <base>..<head>")
    parser.add_argument("--repo", default=".", help="git repository to inspect")
    parser.add_argument("--json", action="store_true", help="print structured JSON")
    args = parser.parse_args()

    try:
        result = classify_python_function_changes(args.diff_input, args.repo)
    except Exception as error:
        result = {
            "gate": "classify-python-function-changes",
            "error": str(error),
            "files": [],
        }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    elif "error" in result:
        print(f"ERROR: {result['error']}")
    else:
        print_text(result)

    return 0


if __name__ == "__main__":
    sys.exit(main())
