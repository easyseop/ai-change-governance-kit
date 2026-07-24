#!/usr/bin/env python3
import argparse
import ast
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from pathlib import PurePosixPath


HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


def normalize_path(path):
    return str(PurePosixPath(path.replace("\\", "/")))


def run_git(args, repo=".", errors="strict"):
    result = subprocess.run(
        ["git", *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=repo,
    )
    stdout = result.stdout.decode("utf-8", errors=errors)
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
    records = {}
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
        records[path] = status
    return records


def parse_diff_hunks(diff_output):
    hunks_by_path = {}
    current_path = None

    for line in diff_output.splitlines():
        if line.startswith("diff --git "):
            current_path = None
            parts = line.split()
            if len(parts) >= 4:
                path = parts[3]
                if path.startswith("b/"):
                    current_path = normalize_path(path[2:])
            continue

        match = HUNK_RE.match(line)
        if not match or current_path is None:
            continue

        old_start = int(match.group("old_start"))
        old_count = int(match.group("old_count") or "1")
        new_start = int(match.group("new_start"))
        new_count = int(match.group("new_count") or "1")
        changed_lines = list(range(new_start, new_start + new_count))
        anchor_lines = changed_lines or [
            line_number for line_number in (new_start - 1, new_start) if line_number > 0
        ]

        hunks_by_path.setdefault(current_path, []).append(
            {
                "old_start": old_start,
                "old_lines": old_count,
                "new_start": new_start,
                "new_lines": new_count,
                "changed_lines": changed_lines,
                "anchor_lines": anchor_lines,
            }
        )

    return hunks_by_path


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


def mapping_start_line(node):
    if not getattr(node, "decorator_list", None):
        return node.lineno
    return min(decorator.lineno for decorator in node.decorator_list)


def inventory_item(node, name):
    return {
        "type": node_type(node),
        "name": name,
        "start_line": node.lineno,
        "end_line": getattr(node, "end_lineno", node.lineno),
        "decorator_start_line": mapping_start_line(node),
        "decorators": [decorator_name(decorator) for decorator in node.decorator_list],
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


def extract_java_inventory(source, source_path):
    try:
        module = load_java_inventory_module()
        return module.extract_inventory(source, source_path)
    except (ImportError, OSError) as error:
        return {
            "source": source_path,
            "lang": "java",
            "items": [],
            "parse_error": f"java analysis unavailable: {error}",
        }


def source_at_ref(ref, path, repo):
    return run_git(["show", f"{ref}:{path}"], repo)


def item_start_line(item):
    return item.get(
        "decorator_start_line",
        item.get("signature_start_line", item.get("start_line")),
    )


def line_touches_item(line_number, item):
    return item_start_line(item) <= line_number <= item["end_line"]


def touched_functions(hunk, inventory):
    touched = []
    for item in inventory["items"]:
        if any(line_touches_item(line_number, item) for line_number in hunk["anchor_lines"]):
            touched.append(
                {
                    "name": item["name"],
                    "type": item["type"],
                    "start_line": item["start_line"],
                    "end_line": item["end_line"],
                    "decorator_start_line": item_start_line(item),
                    "signature_start_line": item_start_line(item),
                }
            )

    if not touched:
        return [{"name": "<module>", "type": "module"}]
    return touched


def unique_functions(records):
    unique = []
    seen = set()
    for record in records:
        key = (
            record.get("name"),
            record.get("type"),
            record.get("start_line"),
            record.get("end_line"),
            record.get("decorator_start_line"),
            record.get("signature_start_line"),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique


def map_diff_to_functions(rev_range, repo="."):
    base, head = split_rev_range(rev_range)
    base_commit = run_git(["rev-parse", base], repo).strip()
    head_commit = run_git(["rev-parse", head], repo).strip()
    status_by_path = parse_name_status(run_git(["diff", "--name-status", rev_range], repo))
    hunks_by_path = parse_diff_hunks(
        run_git(["diff", "--unified=0", rev_range], repo, errors="surrogateescape")
    )

    files = []
    for path in sorted(status_by_path):
        language = "python" if path.endswith(".py") else "java" if path.endswith(".java") else "unsupported"
        file_record = {
            "path": path,
            "status": status_by_path[path],
            "language": language,
            "parse_error": None,
            "hunks": [],
            "touched_functions": [],
        }

        if language == "unsupported" or status_by_path[path].startswith("D"):
            for hunk in hunks_by_path.get(path, []):
                mapped_hunk = dict(hunk)
                mapped_hunk["touched_functions"] = [{"name": "<module>", "type": "module"}]
                file_record["hunks"].append(mapped_hunk)
            file_record["touched_functions"] = [{"name": "<module>", "type": "module"}]
            files.append(file_record)
            continue

        try:
            source = source_at_ref(head, path, repo)
            if language == "java":
                inventory = extract_java_inventory(source, path)
            else:
                inventory = extract_inventory(source, path)
        except (RuntimeError, UnicodeDecodeError, UnicodeEncodeError, ImportError, OSError) as error:
            inventory = {
                "source": path,
                "items": [],
                "parse_error": f"unreadable source: {error}",
            }
        file_record["parse_error"] = inventory["parse_error"]
        touched_for_file = []
        for hunk in hunks_by_path.get(path, []):
            mapped_hunk = dict(hunk)
            mapped_hunk["touched_functions"] = touched_functions(hunk, inventory)
            touched_for_file.extend(mapped_hunk["touched_functions"])
            file_record["hunks"].append(mapped_hunk)
        file_record["touched_functions"] = unique_functions(touched_for_file)
        files.append(file_record)

    return {
        "gate": "map-diff-to-functions",
        "base_commit": base_commit,
        "head_commit": head_commit,
        "files": files,
        "coverage": override_coverage(),
    }


def print_text(result):
    for file_record in result["files"]:
        print(f"{file_record['status']} {file_record['path']}")
        for function in file_record["touched_functions"]:
            print(f"  {function['type']} {function['name']}")


def main():
    parser = argparse.ArgumentParser(
        description="Map git diff hunks to Python functions/classes using after-version lines."
    )
    parser.add_argument("diff_input", help="git diff ref range, for example <base>..<head>")
    parser.add_argument("--repo", default=".", help="git repository to inspect")
    parser.add_argument("--json", action="store_true", help="print structured JSON")
    args = parser.parse_args()

    try:
        result = map_diff_to_functions(args.diff_input, args.repo)
    except Exception as error:
        result = {
            "gate": "map-diff-to-functions",
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
