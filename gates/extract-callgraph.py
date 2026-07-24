#!/usr/bin/env python3
import argparse
import ast
import builtins
import json
import os
import sys
from pathlib import Path, PurePosixPath


BUILTIN_NAMES = set(dir(builtins))


def normalize_path(path):
    return str(PurePosixPath(str(path).replace("\\", "/")))


def module_name_for_path(path):
    pure = PurePosixPath(normalize_path(path))
    without_suffix = pure.with_suffix("")
    parts = list(without_suffix.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def full_function_name(path, local_name):
    module = module_name_for_path(path)
    return f"{module}.{local_name}" if module else local_name


def iter_python_files(repo):
    for root, dirnames, filenames in os.walk(repo):
        dirnames[:] = sorted(name for name in dirnames if name != ".git")
        for filename in sorted(filenames):
            if filename.endswith(".py"):
                absolute = Path(root) / filename
                yield normalize_path(os.path.relpath(absolute, repo)), absolute


def node_type(node):
    if isinstance(node, ast.AsyncFunctionDef):
        return "async_function"
    return "function"


class DefinitionVisitor(ast.NodeVisitor):
    def __init__(self, path):
        self.path = path
        self.parents = []
        self.nodes = []
        self.class_names = set()

    def scoped_name(self, name):
        if not self.parents:
            return name
        return ".".join(self.parents + [name])

    def visit_ClassDef(self, node):
        self.class_names.add(self.scoped_name(node.name))
        self.parents.append(node.name)
        self.generic_visit(node)
        self.parents.pop()

    def visit_FunctionDef(self, node):
        self.visit_function(node)

    def visit_AsyncFunctionDef(self, node):
        self.visit_function(node)

    def visit_function(self, node):
        local_name = self.scoped_name(node.name)
        self.nodes.append(
            {
                "id": full_function_name(self.path, local_name),
                "path": self.path,
                "name": local_name,
                "line": node.lineno,
                "type": node_type(node),
            }
        )
        self.parents.append(node.name)
        self.generic_visit(node)
        self.parents.pop()


def dotted_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = dotted_name(node.value)
        if parent:
            return f"{parent}.{node.attr}"
    return None


def literal_string(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def build_import_bindings(tree):
    bindings = {}
    star_imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                bindings[alias.asname or root] = alias.name if alias.asname else root
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                if alias.name == "*":
                    star_imports.append({"module": module, "line": node.lineno})
                    continue
                target = f"{module}.{alias.name}" if module else alias.name
                bindings[alias.asname or alias.name] = target
        elif isinstance(node, ast.Assign):
            value_name = resolve_name(node.value, bindings)
            if not value_name:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = value_name
    return bindings, star_imports


def resolve_getattr(func, bindings):
    if not isinstance(func, ast.Call):
        return None
    if not isinstance(func.func, ast.Name) or func.func.id != "getattr":
        return None
    if len(func.args) < 2:
        return None
    attr = literal_string(func.args[1])
    base = resolve_name(func.args[0], bindings)
    if attr and base:
        return f"{base}.{attr}"
    return None


def resolve_name(node, bindings):
    getattr_name = resolve_getattr(node, bindings)
    if getattr_name:
        return getattr_name
    name = dotted_name(node)
    if not name:
        return None
    root, _, rest = name.partition(".")
    if root in bindings:
        resolved = bindings[root]
        return f"{resolved}.{rest}" if rest else resolved
    return name


class CallVisitor(ast.NodeVisitor):
    def __init__(self, path, module, definitions, module_locals, class_names, bindings):
        self.path = path
        self.module = module
        self.definitions = definitions
        self.module_locals = module_locals
        self.class_names = class_names
        self.bindings = bindings
        self.parents = []
        self.edges = set()
        self.unresolved = set()

    def current_function(self):
        if not self.parents:
            return None
        return full_function_name(self.path, ".".join(self.parents))

    def scoped_name(self, name):
        if not self.parents:
            return name
        return ".".join(self.parents + [name])

    def visit_ClassDef(self, node):
        self.parents.append(node.name)
        self.generic_visit(node)
        self.parents.pop()

    def visit_FunctionDef(self, node):
        self.visit_function(node)

    def visit_AsyncFunctionDef(self, node):
        self.visit_function(node)

    def visit_function(self, node):
        self.parents.append(node.name)
        for child in node.body:
            self.visit(child)
        self.parents.pop()

    def visit_Call(self, node):
        caller = self.current_function()
        if caller:
            self.record_call(caller, node)
        self.generic_visit(node)

    def record_call(self, caller, node):
        raw_name = dotted_name(node.func) or "<dynamic>"
        resolved = resolve_name(node.func, self.bindings)
        callee = self.resolve_repo_function(resolved)
        if callee:
            self.edges.add((caller, callee, node.lineno, resolved))
        elif raw_name not in BUILTIN_NAMES:
            if raw_name == "<dynamic>" and isinstance(node.func, ast.Call):
                nested_name = dotted_name(node.func.func)
                if nested_name:
                    raw_name = f"{nested_name}(...)"
            kind = "dynamic" if raw_name == "<dynamic>" or isinstance(node.func, ast.Call) else "unresolved"
            self.unresolved.add((caller, kind, resolved or raw_name, node.lineno))

    def resolve_repo_function(self, name):
        if not name:
            return None
        candidates = []
        if name in self.definitions:
            candidates.append(name)
        if "." not in name:
            for local in self.visible_local_names(name):
                candidate = full_function_name(self.path, local)
                if candidate in self.definitions:
                    candidates.append(candidate)
        if name in self.module_locals:
            candidates.extend(self.module_locals[name])
        return sorted(set(candidates))[0] if candidates else None

    def visible_local_names(self, name):
        names = [name]
        for index in range(len(self.parents), 0, -1):
            prefix = ".".join(self.parents[:index])
            if prefix in self.class_names:
                continue
            names.append(f"{prefix}.{name}")
        return names


def collect_sources(repo):
    sources = {}
    errors = []
    for relative_path, absolute_path in iter_python_files(repo):
        try:
            sources[relative_path] = absolute_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            errors.append({"error": "unreadable", "path": relative_path, "message": str(error)})
        except OSError as error:
            errors.append({"error": "unreadable", "path": relative_path, "message": str(error)})
    return sources, errors


def parse_modules(sources):
    modules = {}
    errors = []
    for path in sorted(sources):
        try:
            tree = ast.parse(sources[path], filename=path)
        except SyntaxError as error:
            errors.append(
                {
                    "error": "parse_error",
                    "path": path,
                    "message": f"{error.msg} at line {error.lineno}, column {error.offset}",
                }
            )
            continue
        visitor = DefinitionVisitor(path)
        visitor.visit(tree)
        bindings, star_imports = build_import_bindings(tree)
        for item in star_imports:
            errors.append({"error": "star_import", "path": path, **item})
        modules[path] = {
            "tree": tree,
            "nodes": visitor.nodes,
            "class_names": visitor.class_names,
            "bindings": bindings,
        }
    return modules, errors


def build_module_locals(nodes):
    module_locals = {}
    for node in nodes:
        if "." in node["name"]:
            continue
        module = module_name_for_path(node["path"])
        local = node["name"].split(".")[-1]
        module_locals.setdefault(f"{module}.{local}", []).append(node["id"])
    return module_locals


def extract_callgraph(repo):
    repo = os.path.abspath(repo)
    sources, read_errors = collect_sources(repo)
    modules, parse_errors = parse_modules(sources)
    nodes = [node for path in sorted(modules) for node in modules[path]["nodes"]]
    definitions = {node["id"] for node in nodes}
    module_locals = build_module_locals(nodes)
    edges = set()
    unresolved = set()

    for path in sorted(modules):
        module = modules[path]
        visitor = CallVisitor(
            path,
            module_name_for_path(path),
            definitions,
            module_locals,
            module["class_names"],
            module["bindings"],
        )
        visitor.visit(module["tree"])
        edges.update(visitor.edges)
        unresolved.update(visitor.unresolved)

    return {
        "gate": "extract-callgraph",
        "repo": repo,
        "nodes": sorted(nodes, key=lambda item: (item["id"], item["line"], item["path"])),
        "edges": [
            {"caller": caller, "callee": callee, "line": line, "call": call}
            for caller, callee, line, call in sorted(edges, key=lambda item: (item[0], item[1], item[2], item[3]))
        ],
        "unresolved_calls": [
            {"caller": caller, "kind": kind, "name": name, "line": line}
            for caller, kind, name, line in sorted(unresolved, key=lambda item: (item[0], item[3], item[1], item[2]))
        ],
        "coverage": {
            "unevaluated": [
                {"caller": caller, "kind": kind, "name": name, "line": line}
                for caller, kind, name, line in sorted(unresolved, key=lambda item: (item[0], item[3], item[1], item[2]))
            ]
        },
        "errors": sorted(read_errors + parse_errors, key=lambda item: (item.get("path", ""), item.get("error", ""), item.get("line", 0))),
    }


def print_text(result):
    for edge in result["edges"]:
        print(f"{edge['caller']} -> {edge['callee']} line={edge['line']} call={edge['call']}")
    for item in result["unresolved_calls"]:
        print(f"unresolved {item['caller']} line={item['line']} name={item['name']}")
    for error in result["errors"]:
        print(f"error {error}")


def main():
    parser = argparse.ArgumentParser(description="Extract a deterministic intra-repo Python callgraph.")
    parser.add_argument("repo", nargs="?", default=".", help="repository root to inspect")
    parser.add_argument("--json", action="store_true", help="print structured JSON")
    args = parser.parse_args()

    result = extract_callgraph(args.repo)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print_text(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
