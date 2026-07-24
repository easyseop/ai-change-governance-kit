#!/usr/bin/env python3
import argparse
import importlib.util
import json
import sys
from pathlib import Path, PurePosixPath


PASS = 0
APPROVAL_REQUIRED = 2
TOOL_OWNER = "tool_owner"

GATE_DIR = Path(__file__).resolve().parent


def load_gate_module(filename, module_name):
    spec = importlib.util.spec_from_file_location(module_name, GATE_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


map_gate = load_gate_module("map-diff-to-functions.py", "map_diff_to_functions_gate")
classify_gate = load_gate_module(
    "classify-python-function-changes.py", "classify_python_function_changes_gate"
)
callgraph_gate = load_gate_module("extract-callgraph.py", "extract_callgraph_gate")
java_callgraph_gate = load_gate_module("extract-java-callgraph.py", "extract_java_callgraph_gate")
sinks_gate = load_gate_module("extract-sinks.py", "extract_sinks_gate")
language_router_gate = load_gate_module("language-router.py", "language_router_gate")


def normalize_path(path):
    return str(PurePosixPath(str(path).replace("\\", "/")))


def module_name_for_path(path):
    pure = PurePosixPath(normalize_path(path))
    without_suffix = pure.with_suffix("")
    parts = list(without_suffix.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def full_function_name(path, local_name, language="python"):
    if not local_name or local_name == "<module>":
        return None
    if language == "java":
        return local_name
    module = module_name_for_path(path)
    return f"{module}.{local_name}" if module else local_name


def changed_candidates_from_map(mapping):
    candidates = []
    for file_record in mapping.get("files", []):
        path = normalize_path(file_record.get("path", ""))
        language = file_record.get("language") or ("java" if path.endswith(".java") else "python")
        if language not in {"python", "java"}:
            continue
        for function in file_record.get("touched_functions", []):
            function_id = full_function_name(path, function.get("name"), language)
            if not function_id:
                continue
            candidates.append(
                {
                    "function": function_id,
                    "path": path,
                    "name": function.get("name"),
                    "source": "map",
                    "language": language,
                }
            )
    return candidates


def changed_candidates_from_classification(classification):
    candidates = []
    for file_record in classification.get("files", []):
        path = normalize_path(file_record.get("path", ""))
        language = file_record.get("language") or ("java" if path.endswith(".java") else "python")
        if language not in {"python", "java"} or file_record.get("fallback"):
            continue
        for change in file_record.get("function_changes", []):
            function_id = full_function_name(path, change.get("name"), language)
            if not function_id:
                continue
            candidates.append(
                {
                    "function": function_id,
                    "path": path,
                    "name": change.get("name"),
                    "source": f"classify_{change.get('change_type')}",
                    "language": language,
                }
            )
    return candidates


def unique_candidates(candidates):
    unique = {}
    for candidate in candidates:
        key = (candidate["function"], candidate["path"], candidate["name"], candidate.get("language"))
        if key not in unique:
            unique[key] = dict(candidate)
            continue
        sources = unique[key]["source"].split(",") + [candidate["source"]]
        unique[key]["source"] = ",".join(sorted(set(sources)))
    return [unique[key] for key in sorted(unique)]


def adjacency_from_edges(edges):
    adjacency = {}
    for edge in edges:
        adjacency.setdefault(edge.get("caller"), set()).add(edge.get("callee"))
    return {
        caller: sorted(callees)
        for caller, callees in sorted(adjacency.items())
    }


def reachable_paths(adjacency, sink, max_hops):
    paths = {}
    queue = [(sink, [sink], 0)]
    seen = {(sink, 0)}
    while queue:
        current, path, depth = queue.pop(0)
        if depth >= max_hops:
            continue
        for callee in adjacency.get(current, []):
            next_path = path + [callee]
            next_depth = depth + 1
            if callee not in paths or len(next_path) < len(paths[callee]):
                paths[callee] = next_path
            state = (callee, next_depth)
            if state not in seen:
                seen.add(state)
                queue.append((callee, next_path, next_depth))
    return paths


def reachable_functions_from_sinks(sinks, adjacency):
    reachable = set()
    for sink in sinks:
        start = sink.get("function")
        paths = reachable_paths(adjacency, start, sink.get("hops", 1))
        reachable.update(paths)
        if start:
            reachable.add(start)
    return reachable


def sink_reachable_bodyless_functions(sinks, adjacency, bodyless_functions):
    return reachable_functions_from_sinks(sinks, adjacency).intersection(bodyless_functions)


def sink_relevant_deferred_records(java_deferred, sink_dead_ends, opaque_sink_dead_ends):
    relevant = []
    for item in java_deferred:
        dispatch_targets = set(item.get("dispatch_targets") or [])
        if opaque_sink_dead_ends:
            relevant.append(item)
            continue
        if dispatch_targets and dispatch_targets.intersection(sink_dead_ends):
            relevant.append(item)
            continue
        if not dispatch_targets and sink_dead_ends:
            relevant.append(item)
    return relevant


def path_has_inferred_edge(path, inferred_edges):
    return any((caller, callee) in inferred_edges for caller, callee in zip(path, path[1:]))


def finding_for_sink(sink, changed, path, inferred_edges=None):
    finding = {
        "sink_id": sink.get("id"),
        "sink_function": sink.get("function"),
        "changed_function": changed.get("function"),
        "path": path,
        "hops": len(path) - 1,
        "reviewer": sink.get("owner") or TOOL_OWNER,
        "reason": sink.get("reason"),
        "maturity": sink.get("maturity", "shadow"),
    }
    if inferred_edges and path_has_inferred_edge(path, inferred_edges):
        finding["inferred"] = True
    return finding


def dedupe_findings(findings):
    unique = {}
    for finding in findings:
        key = (
            finding.get("sink_id"),
            finding.get("changed_function"),
            tuple(finding.get("path") or []),
        )
        unique[key] = finding
    return [
        unique[key]
        for key in sorted(
            unique,
            key=lambda item: (
                item[0] or "",
                item[1] or "",
                item[2],
            ),
        )
    ]


def fail_closed(reason, detail=None):
    record = {
        "reason": reason,
        "reviewer": TOOL_OWNER,
    }
    if detail:
        record["detail"] = detail
    return record


def language_for_path(path):
    if path.endswith(".py"):
        return "python"
    if path.endswith(".java"):
        return "java"
    return None


def required_languages_from_routing(routing):
    required = {}
    for path in routing.get("changed_files", []):
        language = language_for_path(normalize_path(path))
        if language:
            required.setdefault(language, []).append(normalize_path(path))
    return {
        language: sorted(paths)
        for language, paths in sorted(required.items())
    }


def check_indirect_impact(
    rev_range,
    sensitive_zones,
    sink_registry,
    repo=".",
    language_routing="policies/language-routing.yaml",
):
    routing = language_router_gate.build_result(rev_range, language_routing, repo)
    active_languages = sorted(
        {
            record.get("adapter")
            for record in routing.get("files", [])
            if record.get("adapter") in {"python", "java"}
        }
    )
    required_languages = required_languages_from_routing(routing)
    mapping = map_gate.map_diff_to_functions(rev_range, repo)
    classification = classify_gate.classify_python_function_changes(rev_range, repo)
    callgraphs = []
    if "python" in active_languages:
        callgraphs.append(("extract-callgraph", callgraph_gate.extract_callgraph(repo)))
    if "java" in active_languages:
        callgraphs.append(("extract-java-callgraph", java_callgraph_gate.extract_callgraph(repo)))
    sinks = sinks_gate.extract_sinks(repo, sensitive_zones, sink_registry, active_languages)

    errors = []
    fail_closed_records = []
    for gate_name, result in (
        ("map-diff-to-functions", mapping),
        ("classify-python-function-changes", classification),
    ):
        if result.get("error"):
            errors.append({"gate": gate_name, "error": result["error"]})
            fail_closed_records.append(fail_closed(f"{gate_name} failed", result["error"]))

    for gate_name, graph in callgraphs:
        for error in graph.get("errors", []):
            errors.append({"gate": gate_name, **error})
        if graph.get("errors"):
            fail_closed_records.append(fail_closed(f"{gate_name} reported errors"))
    for error in sinks.get("errors", []):
        errors.append({"gate": "extract-sinks", **error})
    if sinks.get("errors"):
        fail_closed_records.append(fail_closed("extract-sinks reported errors"))

    routing_coverage = []
    for language, paths in required_languages.items():
        if language in active_languages:
            continue
        detail = f"{language}: {', '.join(paths)}"
        errors.append(
            {
                "gate": "language-router",
                "error": "language_routing_missing_adapter",
                "language": language,
                "paths": paths,
            }
        )
        routing_coverage.append(
            {
                "caller": ",".join(paths),
                "kind": "language_routing_missing_adapter",
                "name": language,
            }
        )
        fail_closed_records.append(
            fail_closed("language routing omitted changed supported files", detail)
        )

    changed_functions = unique_candidates(
        changed_candidates_from_map(mapping)
        + changed_candidates_from_classification(classification)
    )
    changed_by_id = {candidate["function"]: candidate for candidate in changed_functions}
    adjacency = adjacency_from_edges(
        [edge for _, graph in callgraphs for edge in graph.get("edges", [])]
    )
    java_graph = next((graph for name, graph in callgraphs if name == "extract-java-callgraph"), {})
    java_deferred = [
        item
        for item in java_graph.get("coverage", {}).get("unevaluated", [])
        if item.get("kind") in {"anonymous_class", "lambda_dispatch", "method_reference"}
    ]
    java_bodyless = {
        node.get("id")
        for node in java_graph.get("nodes", [])
        if node.get("bodyless") and node.get("id")
    }
    java_opaque_dead_ends = {
        item.get("caller")
        for item in java_graph.get("coverage", {}).get("unevaluated", [])
        if item.get("kind") == "unresolved" and item.get("caller")
    }
    sink_dead_ends = sink_reachable_bodyless_functions(
        sinks.get("sinks", []), adjacency, java_bodyless
    )
    opaque_sink_dead_ends = reachable_functions_from_sinks(
        sinks.get("sinks", []), adjacency
    ).intersection(java_opaque_dead_ends)
    inferred_edges = set()
    if opaque_sink_dead_ends:
        lambda_bodyless = sorted(function for function in java_bodyless if adjacency.get(function))
        for caller in sorted(opaque_sink_dead_ends):
            for callee in lambda_bodyless:
                adjacency.setdefault(caller, [])
                if callee not in adjacency[caller]:
                    adjacency[caller].append(callee)
                    adjacency[caller].sort()
                    inferred_edges.add((caller, callee))
    relevant_java_deferred = sink_relevant_deferred_records(
        java_deferred, sink_dead_ends, opaque_sink_dead_ends
    )
    if relevant_java_deferred:
        errors.extend({"gate": "extract-java-callgraph", **item} for item in relevant_java_deferred)
        relevant_dead_ends = sink_dead_ends.union(opaque_sink_dead_ends)
        fail_closed_records.append(
            fail_closed(
                "extract-java-callgraph reported sink-relevant deferred dispatch",
                f"{len(relevant_java_deferred)} deferred dispatch; dead_ends={','.join(sorted(relevant_dead_ends))}",
            )
        )

    enforcing_findings = []
    shadow_findings = []
    for sink in sinks.get("sinks", []):
        reachable = reachable_paths(adjacency, sink.get("function"), sink.get("hops", 1))
        for function_id in sorted(changed_by_id):
            if function_id == sink.get("function") or function_id not in reachable:
                continue
            finding = finding_for_sink(
                sink,
                changed_by_id[function_id],
                reachable[function_id],
                inferred_edges,
            )
            if sink.get("maturity", "shadow") == "shadow":
                shadow_findings.append(finding)
            else:
                enforcing_findings.append(finding)

    indirect_impact = dedupe_findings(enforcing_findings)
    shadow_hits = dedupe_findings(shadow_findings)
    reviewer_required = sorted(
        {
            finding.get("reviewer")
            for finding in indirect_impact
            if finding.get("reviewer")
        }
        | {
            record.get("reviewer")
            for record in fail_closed_records
            if record.get("reviewer")
        }
    )

    if indirect_impact or fail_closed_records:
        verdict = "approval_required"
        exit_code = APPROVAL_REQUIRED
    else:
        verdict = "pass"
        exit_code = PASS

    return {
        "gate": "check-indirect-impact",
        "verdict": verdict,
        "languages": active_languages,
        "changed_functions": changed_functions,
        "indirect_impact": indirect_impact,
        "shadow_hits": shadow_hits,
        "coverage": {
            "unevaluated": sorted(
                [item for _, graph in callgraphs for item in graph.get("coverage", {}).get("unevaluated", [])]
                + routing_coverage,
                key=lambda item: json.dumps(item, sort_keys=True),
            ),
        },
        "fail_closed": fail_closed_records,
        "errors": errors,
        "reviewer_required": reviewer_required,
        "exit_code": exit_code,
    }


def print_text(result):
    if result["verdict"] == "approval_required":
        print("APPROVAL_REQUIRED: indirect sink impact requires review.")
    elif result["shadow_hits"]:
        print("PASS: indirect sink impact observed in shadow mode.")
    else:
        print("PASS: no indirect sink impact detected.")
    for finding in result.get("indirect_impact", []):
        print(
            f"indirect_impact {finding['sink_id']} "
            f"{' -> '.join(finding['path'])} reviewer={finding['reviewer']}"
        )
    for finding in result.get("shadow_hits", []):
        print(
            f"shadow {finding['sink_id']} "
            f"{' -> '.join(finding['path'])} reviewer={finding['reviewer']}"
        )
    for record in result.get("fail_closed", []):
        print(f"fail_closed {record['reason']}")


def main():
    parser = argparse.ArgumentParser(
        description="Check changed functions for indirect impact on registered sinks."
    )
    parser.add_argument("diff_input", help="git diff ref range, for example <base>..<head>")
    parser.add_argument(
        "--repo",
        default=".",
        help="git repository to inspect; working tree should be the head version",
    )
    parser.add_argument(
        "--sensitive-zones",
        default="policies/sensitive-zones.yaml",
        help="sensitive-zones policy path",
    )
    parser.add_argument(
        "--sink-registry",
        default="policies/sink-registry.yaml",
        help="optional sink-registry policy path",
    )
    parser.add_argument(
        "--language-routing",
        default="policies/language-routing.yaml",
        help="language adapter routing policy path",
    )
    parser.add_argument("--json", action="store_true", help="print structured JSON")
    args = parser.parse_args()

    try:
        result = check_indirect_impact(
            args.diff_input,
            args.sensitive_zones,
            args.sink_registry,
            args.repo,
            args.language_routing,
        )
    except Exception as error:
        result = {
            "gate": "check-indirect-impact",
            "verdict": "approval_required",
            "changed_functions": [],
            "indirect_impact": [],
            "shadow_hits": [],
            "coverage": {"unevaluated": []},
            "fail_closed": [fail_closed("check-indirect-impact failed", str(error))],
            "errors": [{"error": str(error)}],
            "reviewer_required": [TOOL_OWNER],
            "exit_code": APPROVAL_REQUIRED,
        }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print_text(result)
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
