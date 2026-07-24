#!/usr/bin/env python3
import argparse
import ast
import json
import sys


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


def inventory_item(node, name):
    decorators = [decorator_name(decorator) for decorator in node.decorator_list]
    return {
        "type": node_type(node),
        "name": name,
        "start_line": node.lineno,
        "end_line": getattr(node, "end_lineno", node.lineno),
        "signature_start_line": (
            min([decorator.lineno for decorator in node.decorator_list] + [node.lineno])
        ),
        "signature": name,
        "decorators": decorators,
        "annotations": decorators,
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
            "lang": "python",
            "items": [],
            "parse_error": f"{error.msg} at line {error.lineno}, column {error.offset}",
        }

    visitor = InventoryVisitor()
    visitor.visit(tree)
    return {
        "source": source_path,
        "lang": "python",
        "items": visitor.items,
        "parse_error": None,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Extract a deterministic Python function/class inventory."
    )
    parser.add_argument("source_file", help="Python source file to inspect")
    parser.add_argument("--json", action="store_true", help="print structured JSON")
    args = parser.parse_args()

    with open(args.source_file, "r", encoding="utf-8") as stream:
        result = extract_inventory(stream.read(), args.source_file)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        if result["parse_error"]:
            print(f"parse_error: {result['parse_error']}")
        for item in result["items"]:
            decorators = ",".join(item["decorators"])
            print(
                f"{item['type']} {item['name']} "
                f"{item['start_line']}-{item['end_line']} decorators={decorators}"
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
