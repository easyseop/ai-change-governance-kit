#!/usr/bin/env python3
import argparse
import hashlib
import importlib
import json
import re
import sys


DECLARATION_TYPES = {
    "class_declaration",
    "interface_declaration",
    "enum_declaration",
    "record_declaration",
}
CALLABLE_TYPES = {"method_declaration", "constructor_declaration"}


def make_java_parser():
    from tree_sitter import Language, Parser

    module = importlib.import_module("tree_sitter_java")
    language = Language(module.language())
    try:
        return Parser(language)
    except TypeError:
        parser = Parser()
        parser.language = language
        return parser


def node_text(source_bytes, node):
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def normalize_space(value):
    return re.sub(r"\s+", " ", value).strip()


def node_line(node):
    return node.start_point.row + 1


def node_end_line(node):
    return node.end_point.row + 1


def declaration_line(node):
    name = node.child_by_field_name("name")
    if name is not None:
        return node_line(name)
    return node_line(node)


def named_child_text(source_bytes, node, field_name):
    child = node.child_by_field_name(field_name)
    if child is None:
        return None
    return node_text(source_bytes, child)


def declaration_name(source_bytes, node):
    value = named_child_text(source_bytes, node, "name")
    if value:
        return value
    for child in node.named_children:
        if child.type == "identifier":
            return node_text(source_bytes, child)
    return "<anonymous>"


def annotation_name(source_bytes, node):
    text = node_text(source_bytes, node).strip()
    if text.startswith("@"):
        text = text[1:]
    return normalize_space(text.split("(", 1)[0])


def modifier_annotations(source_bytes, node):
    annotations = []
    modifiers = node.child_by_field_name("modifiers")
    if modifiers is None:
        for child in node.named_children:
            if child.type == "modifiers":
                modifiers = child
                break
    if modifiers is None:
        return annotations
    for child in modifiers.named_children:
        if child.type.endswith("annotation"):
            annotations.append(annotation_name(source_bytes, child))
    return annotations


def signature_start_line(node):
    modifiers = node.child_by_field_name("modifiers")
    if modifiers is None:
        for child in node.named_children:
            if child.type == "modifiers":
                modifiers = child
                break
    if modifiers is None or not modifiers.named_children:
        return node_line(node)
    return min(node_line(child) for child in modifiers.named_children)


def signature_text(source_bytes, node):
    body = node.child_by_field_name("body")
    end_byte = body.start_byte if body is not None else node.end_byte
    return normalize_space(source_bytes[node.start_byte:end_byte].decode("utf-8", errors="replace"))


def declaration_type(node):
    if node.type in DECLARATION_TYPES:
        return "class"
    if node.type == "constructor_declaration":
        return "constructor"
    return "method"


def normalized_name(class_stack, local_name, item_type):
    if item_type == "class":
        return ".".join(class_stack + [local_name]) if class_stack else local_name
    if item_type == "constructor":
        return ".".join(class_stack + ["<init>"]) if class_stack else "<init>"
    return ".".join(class_stack + [local_name]) if class_stack else local_name


def inventory_item(source_bytes, node, class_stack):
    item_type = declaration_type(node)
    local_name = declaration_name(source_bytes, node)
    name = normalized_name(class_stack, local_name, item_type)
    annotations = modifier_annotations(source_bytes, node)
    return {
        "type": item_type,
        "name": name,
        "start_line": declaration_line(node),
        "end_line": node_end_line(node),
        "signature_start_line": signature_start_line(node),
        "signature": signature_text(source_bytes, node),
        "annotations": annotations,
    }


def visit(source_bytes, node, class_stack, items):
    if node.type in DECLARATION_TYPES:
        item = inventory_item(source_bytes, node, class_stack)
        items.append(item)
        next_stack = class_stack + [declaration_name(source_bytes, node)]
        for child in node.named_children:
            visit(source_bytes, child, next_stack, items)
        return

    if node.type in CALLABLE_TYPES:
        items.append(inventory_item(source_bytes, node, class_stack))

    for child in node.named_children:
        visit(source_bytes, child, class_stack, items)


def extract_inventory(source, source_path):
    source_bytes = source.encode("utf-8")
    try:
        parser = make_java_parser()
    except (ImportError, OSError) as error:
        return {
            "source": source_path,
            "lang": "java",
            "items": [],
            "parse_error": f"java analysis unavailable: {error}",
        }
    tree = parser.parse(source_bytes)
    root = tree.root_node
    if root.has_error:
        return {
            "source": source_path,
            "lang": "java",
            "items": [],
            "parse_error": "syntax error",
        }

    items = []
    visit(source_bytes, root, [], items)
    return {
        "source": source_path,
        "lang": "java",
        "items": sorted(
            items,
            key=lambda item: (
                item["start_line"],
                item["end_line"],
                item["name"],
                item["signature"],
            ),
        ),
        "parse_error": None,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Extract a deterministic Java class/method inventory."
    )
    parser.add_argument("source_file", help="Java source file to inspect")
    parser.add_argument("--json", action="store_true", help="print structured JSON")
    args = parser.parse_args()

    try:
        with open(args.source_file, "r", encoding="utf-8") as stream:
            result = extract_inventory(stream.read(), args.source_file)
    except UnicodeDecodeError as error:
        result = {
            "source": args.source_file,
            "lang": "java",
            "items": [],
            "parse_error": f"unreadable source: {error}",
        }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        if result["parse_error"]:
            print(f"parse_error: {result['parse_error']}")
        for item in result["items"]:
            print(
                f"{item['type']} {item['name']} "
                f"{item['start_line']}-{item['end_line']} "
                f"signature_start={item['signature_start_line']} "
                f"annotations={','.join(item['annotations'])}"
            )
        print(f"md5={hashlib.md5(json.dumps(result, sort_keys=True).encode()).hexdigest()}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
