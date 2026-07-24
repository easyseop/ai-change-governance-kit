#!/usr/bin/env python3
import argparse
import importlib.util
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path, PurePosixPath


GATE_DIR = Path(__file__).resolve().parent
TYPE_DECLARATIONS = {
    "class_declaration",
    "interface_declaration",
    "enum_declaration",
    "record_declaration",
}
CALLABLE_DECLARATIONS = {"method_declaration", "constructor_declaration"}
REFLECTIVE_METHODS = {"forName", "getMethod", "getDeclaredMethod", "invoke", "newInstance"}


def load_gate_module(filename, module_name):
    spec = importlib.util.spec_from_file_location(module_name, GATE_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


java_inventory_gate = load_gate_module(
    "extract-java-inventory.py", "extract_java_inventory_for_callgraph"
)


def normalize_path(path):
    return str(PurePosixPath(str(path).replace("\\", "/")))


def node_text(source_bytes, node):
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def node_line(node):
    return node.start_point.row + 1


def simple_name(value):
    return str(value).split(".")[-1]


def named_text(source_bytes, node, field):
    child = node.child_by_field_name(field)
    return node_text(source_bytes, child).strip() if child is not None else None


def walk(node):
    yield node
    for child in node.named_children:
        yield from walk(child)


def java_files(repo):
    for root, dirnames, filenames in os.walk(repo):
        dirnames[:] = sorted(name for name in dirnames if name != ".git")
        for filename in sorted(filenames):
            if filename.endswith(".java"):
                absolute = Path(root) / filename
                yield normalize_path(os.path.relpath(str(absolute), repo)), absolute


def type_names(source_bytes, node):
    if node is None:
        return []
    return sorted(
        {
            simple_name(node_text(source_bytes, child).strip())
            for child in walk(node)
            if child.type in {"type_identifier", "scoped_type_identifier"}
        }
    )


def declaration_name(source_bytes, node):
    name = named_text(source_bytes, node, "name")
    return name or "<anonymous>"


def declaration_supertypes(source_bytes, node):
    names = []
    for field in ("superclass", "interfaces"):
        names.extend(type_names(source_bytes, node.child_by_field_name(field)))
    for child in node.children:
        if child.type in {"extends_interfaces", "super_interfaces", "superclass"}:
            names.extend(type_names(source_bytes, child))
    return sorted(set(names))


def declared_type(source_bytes, node):
    type_node = node.child_by_field_name("type")
    names = type_names(source_bytes, type_node)
    if names:
        return names[-1]
    if type_node is not None:
        return simple_name(node_text(source_bytes, type_node).strip())
    return None


def variable_names(source_bytes, node):
    names = []
    name = node.child_by_field_name("name")
    if name is not None:
        names.append(node_text(source_bytes, name).strip())
    for child in node.named_children:
        if child.type == "variable_declarator":
            child_name = child.child_by_field_name("name")
            if child_name is not None:
                names.append(node_text(source_bytes, child_name).strip())
    return sorted(set(names))


def collect_bindings(source_bytes, node):
    bindings = {}
    for child in walk(node):
        if child.type not in {
            "field_declaration",
            "formal_parameter",
            "spread_parameter",
            "local_variable_declaration",
        }:
            continue
        type_name = declared_type(source_bytes, child)
        if not type_name:
            continue
        for name in variable_names(source_bytes, child):
            bindings[name] = type_name
    return bindings


def has_anonymous_class_body(node):
    return any(child.type == "class_body" for child in node.named_children)


def has_static_modifier(source_bytes, node):
    return any(
        node_text(source_bytes, modifier).strip() == "static"
        for child in node.children
        if child.type == "modifiers"
        for modifier in child.children
    )


def class_direct_field_bindings(source_bytes, node):
    bindings = {}
    body = node.child_by_field_name("body")
    if body is None:
        return bindings

    def visit_fields(subtree):
        for child in subtree.named_children:
            if child.type in {"field_declaration", "constant_declaration"}:
                type_name = declared_type(source_bytes, child)
                if not type_name:
                    continue
                for name in variable_names(source_bytes, child):
                    bindings[name] = type_name
            elif child.type in {"enum_body_declarations", "class_body_declarations"}:
                visit_fields(child)

    visit_fields(body)
    return bindings


def nested_in_deferred_body(node, stop_node):
    current = node.parent
    while current is not None and current != stop_node:
        if current.type == "lambda_expression":
            return True
        if current.type == "class_body" and current.parent is not None:
            if current.parent.type == "object_creation_expression":
                return True
        current = current.parent
    return False


def parse_source(path, absolute):
    try:
        source = absolute.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return None, None, {"error": "unreadable", "path": path, "message": str(error)}
    source_bytes = source.encode("utf-8")
    try:
        parser = java_inventory_gate.make_java_parser()
    except (ImportError, OSError) as error:
        return None, None, {
            "error": "java_analysis_unavailable",
            "path": path,
            "message": str(error),
        }
    tree = parser.parse(source_bytes)
    if tree.root_node.has_error:
        return None, None, {"error": "parse_error", "path": path, "message": "syntax error"}
    return source_bytes, tree, None


def collect_declarations(path, source_bytes, tree):
    types = []
    methods = []

    def visit(node, type_stack, enclosing_bindings):
        if node.type in TYPE_DECLARATIONS:
            local = declaration_name(source_bytes, node)
            qualified = ".".join(type_stack + [local])
            types.append(
                {
                    "name": qualified,
                    "simple": local,
                    "kind": node.type,
                    "supers": declaration_supertypes(source_bytes, node),
                    "path": path,
                }
            )
            class_bindings = dict(enclosing_bindings)
            class_bindings.update(class_direct_field_bindings(source_bytes, node))
            for child in node.named_children:
                visit(child, type_stack + [local], class_bindings)
            return
        if node.type in CALLABLE_DECLARATIONS and type_stack:
            local = "<init>" if node.type == "constructor_declaration" else declaration_name(source_bytes, node)
            owner = ".".join(type_stack)
            methods.append(
                {
                    "id": f"{owner}.{local}",
                    "path": path,
                    "name": local,
                    "owner": owner,
                    "line": node_line(node),
                    "type": "constructor" if local == "<init>" else "method",
                    "bodyless": node.type == "method_declaration"
                    and node.child_by_field_name("body") is None,
                    "tree_node": node,
                    "bindings": {
                        **enclosing_bindings,
                        **collect_bindings(source_bytes, node),
                    },
                }
            )
        if node.type == "object_creation_expression" and has_anonymous_class_body(node):
            return
        for child in node.named_children:
            visit(child, type_stack, enclosing_bindings)

    visit(tree.root_node, [], {})
    return types, methods


def type_relationships(types):
    by_simple = defaultdict(set)
    direct = defaultdict(set)
    parents = defaultdict(set)
    for item in types:
        by_simple[item["simple"]].add(item["name"])
    for item in types:
        for parent in item["supers"]:
            direct[parent].add(item["name"])
            parents[item["name"]].update(by_simple.get(parent, {parent}))

    changed = True
    while changed:
        changed = False
        for parent in list(direct):
            expanded = set(direct[parent])
            for child in list(direct[parent]):
                expanded.update(direct.get(simple_name(child), set()))
            if not expanded.issubset(direct[parent]):
                direct[parent].update(expanded)
                changed = True
    changed = True
    while changed:
        changed = False
        for child in list(parents):
            expanded = set(parents[child])
            for parent in list(parents[child]):
                expanded.update(parents.get(parent, set()))
            if not expanded.issubset(parents[child]):
                parents[child].update(expanded)
                changed = True
    return by_simple, direct, parents


def receiver_type(source_bytes, object_node, bindings, current_owner):
    if object_node is None:
        return simple_name(current_owner)
    if object_node.type == "object_creation_expression":
        names = type_names(source_bytes, object_node.child_by_field_name("type"))
        return names[-1] if names else None
    text = node_text(source_bytes, object_node).strip()
    root = re.split(r"[.(]", text, maxsplit=1)[0]
    if root == "this" and text.startswith("this."):
        field = text.split(".", 2)[1]
        if field in bindings:
            return bindings[field]
    if root in {"this", "super"}:
        return simple_name(current_owner)
    if root in bindings:
        return bindings[root]
    if root[:1].isupper():
        return simple_name(root)
    qualified = simple_name(text)
    if qualified[:1].isupper():
        return qualified
    return None


def method_targets(
    method_name,
    owner_type,
    methods_by_owner,
    implementations,
    ancestors,
    type_names_by_simple,
    interface_names,
):
    owners = set(type_names_by_simple.get(owner_type, set()))
    owners.update(implementations.get(owner_type, set()))
    for owner in list(owners):
        owners.update(ancestors.get(owner, set()))
    targets = set()
    for owner in owners:
        targets.update(methods_by_owner.get((owner, method_name), set()))
    return sorted(targets)


def functional_method_targets(
    owner_type,
    methods_by_owner,
    method_details,
    type_names_by_simple,
    interface_names,
):
    owners = sorted(type_names_by_simple.get(owner_type, set()))
    targets = []
    for owner in owners:
        if owner not in interface_names:
            continue
        methods = sorted(
            method_id
            for (method_owner, method_name), method_ids in methods_by_owner.items()
            if method_owner == owner and method_name != "<init>"
            for method_id in method_ids
            if method_details.get(method_id, {}).get("bodyless")
        )
        if len(methods) == 1:
            targets.extend(methods)
    return sorted(set(targets))


def method_parameter_types(source_bytes, method_node):
    params = next(
        (child for child in method_node.named_children if child.type == "formal_parameters"),
        None,
    )
    if params is None:
        return []
    types = []
    for child in params.named_children:
        if child.type not in {"formal_parameter", "spread_parameter"}:
            continue
        types.append(declared_type(source_bytes, child))
    return types


def argument_index(node):
    parent = node.parent
    if parent is None or parent.type != "argument_list":
        return None
    index = 0
    for child in parent.named_children:
        if child.start_byte == node.start_byte and child.end_byte == node.end_byte:
            return index
        index += 1
    return None


def invocation_parameter_type(
    source_bytes,
    node,
    bindings,
    current_owner,
    methods_by_owner,
    method_details,
    implementations,
    ancestors,
    type_names_by_simple,
    interface_names,
):
    index = argument_index(node)
    argument_list = node.parent
    invocation = argument_list.parent if argument_list is not None else None
    if index is None or invocation is None or invocation.type != "method_invocation":
        return None
    name = named_text(source_bytes, invocation, "name") or "<dynamic>"
    owner_type = receiver_type(
        source_bytes,
        invocation.child_by_field_name("object"),
        bindings,
        current_owner,
    )
    targets = method_targets(
        name,
        owner_type,
        methods_by_owner,
        implementations,
        ancestors,
        type_names_by_simple,
        interface_names,
    ) if owner_type else []
    parameter_types = set()
    for target in targets:
        method = method_details.get(target)
        if method is None:
            continue
        params = method_parameter_types(source_bytes, method["tree_node"])
        if index < len(params) and params[index]:
            parameter_types.add(params[index])
    if len(parameter_types) == 1:
        return sorted(parameter_types)[0]
    return None


def assigned_target_type(source_bytes, node, bindings):
    parent = node.parent
    if parent is None:
        return None
    if parent.type == "assignment_expression":
        left = parent.named_children[0] if parent.named_children else None
        if left is None:
            return None
        text = node_text(source_bytes, left).strip()
        if text in bindings:
            return bindings[text]
        if text.startswith("this."):
            field = text.split(".", 2)[1]
            return bindings.get(field)
    if parent.type == "variable_declarator":
        declaration = parent.parent
        if declaration is not None:
            return declared_type(source_bytes, declaration)
    return None


def method_reference_parts(source_bytes, node):
    named = node.named_children
    if len(named) < 2:
        return None, None
    qualifier = named[0]
    name = named[-1]
    return qualifier, node_text(source_bytes, name).strip()


def referenced_method_targets(
    source_bytes,
    node,
    bindings,
    current_owner,
    methods_by_owner,
    implementations,
    ancestors,
    type_names_by_simple,
    interface_names,
):
    qualifier, name = method_reference_parts(source_bytes, node)
    if not name:
        return []
    owner_type = receiver_type(source_bytes, qualifier, bindings, current_owner)
    return method_targets(
        name,
        owner_type,
        methods_by_owner,
        implementations,
        ancestors,
        type_names_by_simple,
        interface_names,
    ) if owner_type else []


def subtree_call_targets(
    source_bytes,
    subtree,
    bindings,
    current_owner,
    methods_by_owner,
    method_details,
    implementations,
    ancestors,
    type_names_by_simple,
    interface_names,
):
    targets = set()
    unresolved = set()
    for node in walk(subtree):
        if node is not subtree and nested_in_deferred_body(node, subtree):
            if node.type == "lambda_expression":
                unresolved.add(("lambda_dispatch", "lambda", node_line(node), ()))
            elif node.type == "object_creation_expression" and has_anonymous_class_body(node):
                names = type_names(source_bytes, node.child_by_field_name("type"))
                owner_type = names[-1] if names else "<unknown>"
                unresolved.add(("anonymous_class", f"new {owner_type}", node_line(node), ()))
            elif node.type == "method_reference":
                unresolved.add(("method_reference", node_text(source_bytes, node).strip(), node_line(node), ()))
            continue
        if node is not subtree and node.type == "lambda_expression":
            unresolved.add(("lambda_dispatch", "lambda", node_line(node), ()))
            continue
        if node.type == "method_invocation":
            name = named_text(source_bytes, node, "name") or "<dynamic>"
            object_node = node.child_by_field_name("object")
            owner_type = receiver_type(source_bytes, object_node, bindings, current_owner)
            resolved = method_targets(
                name,
                owner_type,
                methods_by_owner,
                implementations,
                ancestors,
                type_names_by_simple,
                interface_names,
            ) if owner_type else []
            call_text = node_text(source_bytes, node).split("(", 1)[0].strip()
            if resolved:
                targets.update(resolved)
            else:
                kind = "dynamic" if name in REFLECTIVE_METHODS else "unresolved"
                unresolved.add((kind, call_text or name, node_line(node), ()))
        elif node.type == "object_creation_expression":
            names = type_names(source_bytes, node.child_by_field_name("type"))
            owner_type = names[-1] if names else None
            if has_anonymous_class_body(node):
                unresolved.add(("anonymous_class", f"new {owner_type or '<unknown>'}", node_line(node), ()))
                continue
            resolved = method_targets(
                "<init>",
                owner_type,
                methods_by_owner,
                implementations,
                ancestors,
                type_names_by_simple,
                interface_names,
            ) if owner_type else []
            if not resolved and owner_type and not has_anonymous_class_body(node):
                unresolved.add(("unresolved", f"new {owner_type}", node_line(node), ()))
        elif node.type == "method_reference":
            resolved = referenced_method_targets(
                source_bytes,
                node,
                bindings,
                current_owner,
                methods_by_owner,
                implementations,
                ancestors,
                type_names_by_simple,
                interface_names,
            )
            if resolved:
                targets.update(resolved)
            else:
                unresolved.add(("method_reference", node_text(source_bytes, node).strip(), node_line(node), ()))
    return targets, unresolved


def collect_calls(
    source_bytes,
    method,
    methods_by_owner,
    method_details,
    implementations,
    ancestors,
    type_names_by_simple,
    interface_names,
):
    edges = set()
    unresolved = set()
    body = method["tree_node"].child_by_field_name("body")
    if body is None:
        return edges, unresolved

    for node in walk(body):
        if nested_in_deferred_body(node, body):
            continue
        if node.type == "method_invocation":
            name = named_text(source_bytes, node, "name") or "<dynamic>"
            object_node = node.child_by_field_name("object")
            owner_type = receiver_type(
                source_bytes, object_node, method["bindings"], method["owner"]
            )
            targets = method_targets(
                name,
                owner_type,
                methods_by_owner,
                implementations,
                ancestors,
                type_names_by_simple,
                interface_names,
            ) if owner_type else []
            call_text = node_text(source_bytes, node).split("(", 1)[0].strip()
            if targets:
                for target in targets:
                    edges.add((method["id"], target, node_line(node), call_text))
            else:
                kind = "dynamic" if name in REFLECTIVE_METHODS else "unresolved"
                unresolved.add((method["id"], kind, call_text or name, node_line(node), ()))

        elif node.type == "object_creation_expression":
            names = type_names(source_bytes, node.child_by_field_name("type"))
            owner_type = names[-1] if names else None
            targets = method_targets(
                "<init>",
                owner_type,
                methods_by_owner,
                implementations,
                ancestors,
                type_names_by_simple,
                interface_names,
            ) if owner_type else []
            for target in targets:
                edges.add((method["id"], target, node_line(node), f"new {owner_type}"))
            anonymous_body = next(
                (child for child in node.named_children if child.type == "class_body"),
                None,
            )
            if anonymous_body is not None:
                dispatch_targets = functional_method_targets(
                    owner_type,
                    methods_by_owner,
                    method_details,
                    type_names_by_simple,
                    interface_names,
                ) if owner_type else []
                for child in anonymous_body.named_children:
                    if child.type in CALLABLE_DECLARATIONS:
                        body_targets, body_unresolved = subtree_call_targets(
                            source_bytes,
                            child.child_by_field_name("body") or child,
                            method["bindings"],
                            method["owner"],
                            methods_by_owner,
                            method_details,
                            implementations,
                            ancestors,
                            type_names_by_simple,
                            interface_names,
                        )
                        for dispatch_target in dispatch_targets:
                            for target in body_targets:
                                edges.add((dispatch_target, target, node_line(child), f"new {owner_type}"))
                            for kind, name, line, targets in body_unresolved:
                                unresolved.add((dispatch_target, kind, name, line, targets))
                    elif child.type in {"field_declaration", "constant_declaration", "static_initializer", "block"}:
                        for kind, name, line, targets in deferred_nodes(source_bytes, child):
                            unresolved.add((method["id"], kind, name, line, targets))
                if not dispatch_targets:
                    unresolved.add(
                        (method["id"], "anonymous_class", f"new {owner_type}", node_line(node), ())
                    )
            elif owner_type and not targets:
                unresolved.add(
                    (method["id"], "unresolved", f"new {owner_type}", node_line(node), ())
                )
        elif node.type == "lambda_expression":
            owner_type = assigned_target_type(source_bytes, node, method["bindings"])
            owner_from_invocation = False
            if not owner_type:
                owner_type = invocation_parameter_type(
                    source_bytes,
                    node,
                    method["bindings"],
                    method["owner"],
                    methods_by_owner,
                    method_details,
                    implementations,
                    ancestors,
                    type_names_by_simple,
                    interface_names,
                )
                owner_from_invocation = owner_type is not None
            dispatch_targets = functional_method_targets(
                owner_type,
                methods_by_owner,
                method_details,
                type_names_by_simple,
                interface_names,
            ) if owner_type else []
            body_targets, body_unresolved = subtree_call_targets(
                source_bytes,
                node,
                method["bindings"],
                method["owner"],
                methods_by_owner,
                method_details,
                implementations,
                ancestors,
                type_names_by_simple,
                interface_names,
            )
            if dispatch_targets:
                for dispatch_target in dispatch_targets:
                    for target in body_targets:
                        edges.add((dispatch_target, target, node_line(node), "lambda"))
                    for kind, name, line, targets in body_unresolved:
                        unresolved.add((dispatch_target, kind, name, line, targets))
                if owner_from_invocation:
                    unresolved.add(
                        (
                            method["id"],
                            "lambda_dispatch",
                            "lambda",
                            node_line(node),
                            tuple(dispatch_targets),
                        )
                    )
            else:
                unresolved.add((method["id"], "lambda_dispatch", "lambda", node_line(node), tuple(dispatch_targets)))
        elif node.type == "method_reference":
            owner_type = assigned_target_type(source_bytes, node, method["bindings"])
            owner_from_invocation = False
            if not owner_type:
                owner_type = invocation_parameter_type(
                    source_bytes,
                    node,
                    method["bindings"],
                    method["owner"],
                    methods_by_owner,
                    method_details,
                    implementations,
                    ancestors,
                    type_names_by_simple,
                    interface_names,
                )
                owner_from_invocation = owner_type is not None
            dispatch_targets = functional_method_targets(
                owner_type,
                methods_by_owner,
                method_details,
                type_names_by_simple,
                interface_names,
            ) if owner_type else []
            referenced_targets = referenced_method_targets(
                source_bytes,
                node,
                method["bindings"],
                method["owner"],
                methods_by_owner,
                implementations,
                ancestors,
                type_names_by_simple,
                interface_names,
            )
            if dispatch_targets and referenced_targets:
                for dispatch_target in dispatch_targets:
                    for target in referenced_targets:
                        edges.add((dispatch_target, target, node_line(node), node_text(source_bytes, node).strip()))
                if owner_from_invocation:
                    unresolved.add(
                        (
                            method["id"],
                            "method_reference",
                            node_text(source_bytes, node).strip(),
                            node_line(node),
                            tuple(dispatch_targets),
                        )
                    )
            else:
                unresolved.add((method["id"], "method_reference", node_text(source_bytes, node).strip(), node_line(node), tuple(dispatch_targets)))
    return edges, unresolved


def collect_initializer_deferred(
    source_bytes,
    tree,
    methods_by_owner,
    method_details,
    type_names_by_simple,
    interface_names,
):
    unresolved = set()

    def visit(node, type_stack):
        if node.type in TYPE_DECLARATIONS:
            local = declaration_name(source_bytes, node)
            owner = ".".join(type_stack + [local])
            body = node.child_by_field_name("body")
            if body is not None:
                scan_initializer_members(body, owner, class_direct_field_bindings(source_bytes, node))
                for child in body.named_children:
                    visit(child, type_stack + [local])
            return
        for child in node.named_children:
            visit(child, type_stack)

    def scan_initializer_members(body, owner, class_bindings):
        for child in body.named_children:
            if child.type == "field_declaration":
                caller = f"{owner}.<clinit>" if has_static_modifier(source_bytes, child) else f"{owner}.<init>"
                scan_deferred(child, caller, declaration_dispatch_targets(child), {})
            elif child.type == "constant_declaration":
                scan_deferred(child, f"{owner}.<clinit>", declaration_dispatch_targets(child), {})
            elif child.type == "static_initializer":
                scan_deferred(child, f"{owner}.<clinit>", (), class_bindings)
            elif child.type == "block":
                scan_deferred(child, f"{owner}.<init>", (), class_bindings)
            elif child.type == "enum_constant":
                constant_body = next(
                    (item for item in child.named_children if item.type == "class_body"),
                    None,
                )
                if constant_body is not None:
                    scan_initializer_members(constant_body, owner, class_bindings)
            elif child.type in {"enum_body_declarations", "class_body_declarations"}:
                scan_initializer_members(child, owner, class_bindings)

    def declaration_dispatch_targets(node):
        owner_type = declared_type(source_bytes, node)
        return tuple(
            functional_method_targets(
                owner_type,
                methods_by_owner,
                method_details,
                type_names_by_simple,
                interface_names,
            )
        ) if owner_type else ()

    def scan_deferred(subtree, caller, dispatch_targets=(), bindings=None):
        for kind, name, line, targets in deferred_nodes(
            source_bytes,
            subtree,
            bindings or {},
            methods_by_owner,
            method_details,
            type_names_by_simple,
            interface_names,
        ):
            unresolved.add((caller, kind, name, line, targets or dispatch_targets))

    visit(tree.root_node, [])
    return unresolved


def deferred_dispatch_targets(
    source_bytes,
    node,
    bindings,
    methods_by_owner,
    method_details,
    type_names_by_simple,
    interface_names,
):
    owner_type = assigned_target_type(source_bytes, node, bindings)
    return tuple(
        functional_method_targets(
            owner_type,
            methods_by_owner,
            method_details,
            type_names_by_simple,
            interface_names,
        )
    ) if owner_type else ()


def deferred_nodes(
    source_bytes,
    subtree,
    bindings=None,
    methods_by_owner=None,
    method_details=None,
    type_names_by_simple=None,
    interface_names=None,
):
    bindings = bindings or {}
    methods_by_owner = methods_by_owner or {}
    method_details = method_details or {}
    type_names_by_simple = type_names_by_simple or {}
    interface_names = interface_names or set()
    found = set()
    for child in walk(subtree):
        if child is not subtree and nested_in_deferred_body(child, subtree):
            continue
        if child.type == "lambda_expression":
            found.add(
                (
                    "lambda_dispatch",
                    "lambda",
                    node_line(child),
                    deferred_dispatch_targets(
                        source_bytes,
                        child,
                        bindings,
                        methods_by_owner,
                        method_details,
                        type_names_by_simple,
                        interface_names,
                    ),
                )
            )
        elif child.type == "object_creation_expression" and has_anonymous_class_body(child):
            names = type_names(source_bytes, child.child_by_field_name("type"))
            owner_type = names[-1] if names else "<unknown>"
            found.add(
                (
                    "anonymous_class",
                    f"new {owner_type}",
                    node_line(child),
                    deferred_dispatch_targets(
                        source_bytes,
                        child,
                        bindings,
                        methods_by_owner,
                        method_details,
                        type_names_by_simple,
                        interface_names,
                    ),
                )
            )
        elif child.type == "method_reference":
            found.add(
                (
                    "method_reference",
                    node_text(source_bytes, child).strip(),
                    node_line(child),
                    deferred_dispatch_targets(
                        source_bytes,
                        child,
                        bindings,
                        methods_by_owner,
                        method_details,
                        type_names_by_simple,
                        interface_names,
                    ),
                )
            )
    return found


def extract_callgraph(repo):
    repo = os.path.abspath(repo)
    parsed = []
    errors = []
    all_types = []
    all_methods = []
    for path, absolute in java_files(repo):
        source_bytes, tree, error = parse_source(path, absolute)
        if error:
            errors.append(error)
            continue
        types, methods = collect_declarations(path, source_bytes, tree)
        parsed.append((source_bytes, tree, methods))
        all_types.extend(types)
        all_methods.extend(methods)

    type_names_by_simple, implementations, ancestors = type_relationships(all_types)
    interface_names = {
        item["name"] for item in all_types if item["kind"] == "interface_declaration"
    }
    methods_by_owner = defaultdict(set)
    method_details = {}
    for method in all_methods:
        methods_by_owner[(method["owner"], method["name"])].add(method["id"])
        method_details[method["id"]] = method

    edges = set()
    unresolved = set()
    for source_bytes, tree, methods in parsed:
        unresolved.update(
            collect_initializer_deferred(
                source_bytes,
                tree,
                methods_by_owner,
                method_details,
                type_names_by_simple,
                interface_names,
            )
        )
        for method in methods:
            found_edges, found_unresolved = collect_calls(
                source_bytes,
                method,
                methods_by_owner,
                method_details,
                implementations,
                ancestors,
                type_names_by_simple,
                interface_names,
            )
            edges.update(found_edges)
            unresolved.update(found_unresolved)

    unresolved_records = [
        {
            "caller": caller,
            "kind": kind,
            "name": name,
            "line": line,
            "dispatch_targets": list(dispatch_targets),
        }
        for caller, kind, name, line, dispatch_targets in sorted(
            unresolved, key=lambda item: (item[0], item[3], item[1], item[2], item[4])
        )
    ]
    return {
        "gate": "extract-java-callgraph",
        "repo": repo,
        "lang": "java",
        "nodes": [
            {
                "id": item["id"],
                "path": item["path"],
                "name": item["name"],
                "line": item["line"],
                "type": item["type"],
                "bodyless": item.get("bodyless", False),
            }
            for item in sorted(
                all_methods, key=lambda item: (item["id"], item["line"], item["path"])
            )
        ],
        "edges": [
            {"caller": caller, "callee": callee, "line": line, "call": call}
            for caller, callee, line, call in sorted(
                edges, key=lambda item: (item[0], item[1], item[2], item[3])
            )
        ],
        "unresolved_calls": unresolved_records,
        "coverage": {"unevaluated": unresolved_records},
        "errors": sorted(
            errors,
            key=lambda item: (item.get("path", ""), item.get("error", ""), item.get("line", 0)),
        ),
    }


def print_text(result):
    for edge in result["edges"]:
        print(f"{edge['caller']} -> {edge['callee']} line={edge['line']} call={edge['call']}")
    for item in result["unresolved_calls"]:
        print(
            f"unresolved: {item['caller']} {item['kind']} "
            f"{item['name']} line={item['line']}"
        )
    for error in result["errors"]:
        print(f"error: {error['path']} {error['error']} {error.get('message', '')}".rstrip())


def main():
    parser = argparse.ArgumentParser(
        description="Extract a deterministic conservative Java intra-repository call graph."
    )
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
