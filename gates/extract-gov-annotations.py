#!/usr/bin/env python3
import argparse
import ast
import json
import sys


LEVEL_RANK = {"watched": 1, "protected": 2, "frozen": 3}
DEFAULT_INVALID_LEVEL = "protected"


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


def is_gov_decorator(node):
    name = decorator_name(node)
    return name == "gov" or name.endswith(".gov")


def node_type(node):
    if isinstance(node, ast.AsyncFunctionDef):
        return "async_function"
    if isinstance(node, ast.FunctionDef):
        return "function"
    return "class"


def strongest_level(levels):
    valid = [level for level in levels if level in LEVEL_RANK]
    if not valid:
        return None
    return max(valid, key=lambda level: LEVEL_RANK[level])


def literal_string(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def literal_bool(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name) and node.id in ("true", "false"):
        return node.id == "true"
    return None


def empty_annotation(line, errors=None):
    return {
        "level": None,
        "reason": None,
        "owner": None,
        "sink": False,
        "line": line,
        "errors": list(errors or []),
        "unresolved": False,
    }


def parse_gov_call(decorator):
    call = decorator if isinstance(decorator, ast.Call) else None
    line = getattr(decorator, "lineno", None)
    parsed = empty_annotation(line)

    if call is None:
        parsed["errors"].append("invalid_syntax")
        parsed["unresolved"] = True
        parsed["level"] = DEFAULT_INVALID_LEVEL
        return parsed

    if call.args:
        parsed["errors"].append("invalid_syntax")
        parsed["unresolved"] = True

    seen_fields = set()
    level_values = []
    level_field_seen = False
    sink_values = []
    for keyword in call.keywords:
        if keyword.arg is None:
            parsed["errors"].append("invalid_syntax")
            parsed["unresolved"] = True
            continue

        field = keyword.arg
        if field == "level":
            level_field_seen = True
        value = literal_bool(keyword.value) if field == "sink" else literal_string(keyword.value)
        if value is None:
            parsed["errors"].append("unresolved")
            parsed["unresolved"] = True
            continue

        if field in seen_fields:
            parsed["errors"].append("duplicate")
        seen_fields.add(field)

        if field == "level":
            if value not in LEVEL_RANK:
                parsed["errors"].append("invalid_level")
            else:
                level_values.append(value)
        elif field == "reason":
            if parsed["reason"] is None:
                parsed["reason"] = value
        elif field == "owner":
            if parsed["owner"] is None:
                parsed["owner"] = value
        elif field == "sink":
            sink_values.append(value)
        else:
            parsed["errors"].append("unknown_field")

    parsed["level"] = strongest_level(level_values)
    parsed["sink"] = any(sink_values)
    if parsed["level"] is None:
        if not level_field_seen:
            parsed["errors"].append("missing_level")
        parsed["level"] = DEFAULT_INVALID_LEVEL

    if not parsed["reason"]:
        parsed["errors"].append("missing_reason")

    return parsed


def merge_gov_annotations(annotations):
    if not annotations:
        return None

    merged = empty_annotation(annotations[0]["line"])
    merged["errors"] = []
    for annotation in annotations:
        merged["errors"].extend(annotation["errors"])
        merged["unresolved"] = merged["unresolved"] or annotation["unresolved"]
        if annotation["line"] is not None:
            merged["line"] = (
                annotation["line"]
                if merged["line"] is None
                else min(merged["line"], annotation["line"])
            )
        annotation_is_stronger = (
            strongest_level([merged["level"], annotation["level"]]) == annotation["level"]
        )
        if annotation.get("sink"):
            merged["sink"] = True
        if annotation_is_stronger:
            merged["level"] = annotation["level"]
            if annotation["reason"]:
                merged["reason"] = annotation["reason"]
            if annotation["owner"]:
                merged["owner"] = annotation["owner"]
        else:
            if annotation["reason"] and not merged["reason"]:
                merged["reason"] = annotation["reason"]
            if annotation["owner"] and not merged["owner"]:
                merged["owner"] = annotation["owner"]

    if len(annotations) > 1:
        merged["errors"].append("duplicate")
    merged["errors"] = sorted(set(merged["errors"]))
    return merged


def gov_for_node(node):
    annotations = [
        parse_gov_call(decorator)
        for decorator in node.decorator_list
        if is_gov_decorator(decorator)
    ]
    return merge_gov_annotations(annotations)


def dict_string_value(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def parse_module_gov_value(value, line):
    parsed = empty_annotation(line)
    if not isinstance(value, ast.Dict):
        parsed["errors"].append("invalid_module_gov")
        parsed["level"] = DEFAULT_INVALID_LEVEL
        return parsed

    seen_fields = set()
    level_values = []
    level_field_seen = False
    for key_node, value_node in zip(value.keys, value.values):
        key = dict_string_value(key_node)
        val = dict_string_value(value_node)
        if key is None or val is None:
            parsed["errors"].append("invalid_module_gov")
            parsed["unresolved"] = True
            continue
        if key == "level":
            level_field_seen = True
        if key in seen_fields:
            parsed["errors"].append("invalid_module_gov")
        seen_fields.add(key)
        if key == "level":
            if val in LEVEL_RANK:
                level_values.append(val)
            else:
                parsed["errors"].append("invalid_level")
        elif key == "reason":
            if parsed["reason"] is None:
                parsed["reason"] = val
        elif key == "owner":
            if parsed["owner"] is None:
                parsed["owner"] = val
        else:
            parsed["errors"].append("unknown_field")

    parsed["level"] = strongest_level(level_values)
    if parsed["level"] is None:
        if not level_field_seen:
            parsed["errors"].append("missing_level")
        parsed["level"] = DEFAULT_INVALID_LEVEL
    if not parsed["reason"]:
        parsed["errors"].append("missing_reason")
    parsed["errors"] = sorted(set(parsed["errors"]))
    return parsed


def target_contains_module_gov(target):
    if isinstance(target, ast.Name):
        return target.id == "__gov__"
    if isinstance(target, (ast.Tuple, ast.List)):
        return any(target_contains_module_gov(element) for element in target.elts)
    return False


def is_formal_module_gov_statement(statement):
    if isinstance(statement, ast.Assign):
        return (
            len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and statement.targets[0].id == "__gov__"
        )
    if isinstance(statement, ast.AnnAssign):
        return (
            statement.value is not None
            and isinstance(statement.target, ast.Name)
            and statement.target.id == "__gov__"
        )
    return False


def module_gov(tree):
    assignments = []
    formal_statement_ids = set()
    invalid_binding_lines = []
    for statement in tree.body:
        if is_formal_module_gov_statement(statement):
            formal_statement_ids.add(id(statement))
            assignments.append(parse_module_gov_value(statement.value, statement.lineno))

    for node in ast.walk(tree):
        if id(node) in formal_statement_ids:
            continue
        if isinstance(node, ast.Assign):
            if any(target_contains_module_gov(target) for target in node.targets):
                invalid_binding_lines.append(node.lineno)
        elif isinstance(node, ast.AnnAssign):
            if target_contains_module_gov(node.target):
                invalid_binding_lines.append(node.lineno)
        elif isinstance(node, ast.AugAssign):
            if target_contains_module_gov(node.target):
                invalid_binding_lines.append(node.lineno)
        elif isinstance(node, ast.NamedExpr):
            if target_contains_module_gov(node.target):
                invalid_binding_lines.append(node.lineno)

    for line in invalid_binding_lines:
        invalid = empty_annotation(line, ["invalid_module_gov"])
        invalid["level"] = DEFAULT_INVALID_LEVEL
        assignments.append(invalid)

    if not assignments:
        return None
    merged = merge_gov_annotations(assignments)
    if len(assignments) > 1 or invalid_binding_lines:
        merged["errors"].append("invalid_module_gov")
        merged["errors"] = sorted(set(merged["errors"]))
    return merged


class GovVisitor(ast.NodeVisitor):
    def __init__(self, module_annotation):
        self.annotations = []
        self.parents = []
        self.level_stack = [module_annotation["level"]] if module_annotation else []
        self.counts_by_name = {}

    def normalized_name(self, name):
        if not self.parents:
            return name
        return ".".join(self.parents + [name])

    def next_order_key(self, name):
        order_key = self.counts_by_name.get(name, 0)
        self.counts_by_name[name] = order_key + 1
        return order_key

    def visit_scoped_node(self, node):
        name = self.normalized_name(node.name)
        order_key = self.next_order_key(name)
        own_gov = gov_for_node(node)
        inherited_levels = list(self.level_stack)
        own_level = own_gov["level"] if own_gov else None
        effective_level = strongest_level(inherited_levels + [own_level])

        if own_gov or effective_level:
            self.annotations.append(
                {
                    "name": name,
                    "def_line": node.lineno,
                    "gov_line": own_gov["line"] if own_gov else None,
                    "order_key": order_key,
                    "decorators": [decorator_name(decorator) for decorator in node.decorator_list],
                    "level": own_level,
                    "reason": own_gov["reason"] if own_gov else None,
                    "owner": own_gov["owner"] if own_gov else None,
                    "sink": own_gov["sink"] if own_gov else False,
                    "effective_level": effective_level,
                    "errors": own_gov["errors"] if own_gov else [],
                    "unresolved": own_gov["unresolved"] if own_gov else False,
                    "type": node_type(node),
                }
            )

        self.parents.append(node.name)
        if effective_level:
            self.level_stack.append(effective_level)
        self.generic_visit(node)
        if effective_level:
            self.level_stack.pop()
        self.parents.pop()

    def visit_ClassDef(self, node):
        self.visit_scoped_node(node)

    def visit_FunctionDef(self, node):
        self.visit_scoped_node(node)

    def visit_AsyncFunctionDef(self, node):
        self.visit_scoped_node(node)


def public_module(module_annotation):
    if not module_annotation:
        return None
    return {
        "level": module_annotation["level"],
        "reason": module_annotation["reason"],
        "line": module_annotation["line"],
        "errors": module_annotation["errors"],
    }


def parse_source(source, source_path):
    try:
        tree = ast.parse(source, filename=source_path)
    except SyntaxError as error:
        return {
            "path": source_path,
            "module": None,
            "annotations": [],
            "parse_error": f"{error.msg} at line {error.lineno}, column {error.offset}",
            "unreadable": False,
        }

    module_annotation = module_gov(tree)
    visitor = GovVisitor(module_annotation)
    visitor.visit(tree)
    return {
        "path": source_path,
        "module": public_module(module_annotation),
        "annotations": visitor.annotations,
        "parse_error": False,
        "unreadable": False,
    }


def read_source(path):
    if path == "-":
        return sys.stdin.read()
    with open(path, "r", encoding="utf-8") as stream:
        return stream.read()


def extract_gov_annotations(path):
    try:
        source = read_source(path)
    except UnicodeDecodeError as error:
        return {
            "path": path,
            "module": None,
            "annotations": [],
            "parse_error": False,
            "unreadable": f"unreadable source: {error}",
        }
    return parse_source(source, "stdin" if path == "-" else path)


def print_text(result):
    if result["parse_error"]:
        print(f"parse_error: {result['parse_error']}")
    if result["unreadable"]:
        print(f"unreadable: {result['unreadable']}")
    if result["module"]:
        print(
            f"module level={result['module']['level']} "
            f"line={result['module']['line']} errors={','.join(result['module']['errors'])}"
        )
    for annotation in result["annotations"]:
        print(
            f"{annotation['type']} {annotation['name']} "
            f"level={annotation['level']} effective={annotation['effective_level']} "
            f"line={annotation['def_line']} order_key={annotation['order_key']}"
        )


def main():
    parser = argparse.ArgumentParser(
        description="Extract deterministic @gov and __gov__ annotations from Python source."
    )
    parser.add_argument("source_file", nargs="?", default="-", help="Python source file, or '-' for stdin")
    parser.add_argument("--json", action="store_true", help="print structured JSON")
    args = parser.parse_args()

    result = extract_gov_annotations(args.source_file)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print_text(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
