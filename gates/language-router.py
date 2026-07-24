#!/usr/bin/env python3
import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path, PurePosixPath

import yaml


def normalize_path(path):
    return str(PurePosixPath(path.replace("\\", "/")))


def run_git(args, repo="."):
    result = subprocess.run(
        ["git"] + args,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=repo,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result.stdout


def read_name_status(diff_input, repo):
    if os.path.exists(diff_input):
        with open(diff_input, "r", encoding="utf-8") as stream:
            return stream.read().splitlines()
    return run_git(["diff", "--name-status", diff_input], repo).splitlines()


def parse_changed_files(name_status_lines):
    files = []
    for line in name_status_lines:
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith(("R", "C")) and len(parts) >= 3:
            path = parts[2]
        elif len(parts) >= 2:
            path = parts[1]
        else:
            continue
        files.append(normalize_path(path))
    return sorted(files)


def load_policy(path):
    with open(path, "r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream) or {}

    extension_map = {}
    defaults = data.get("defaults") or {}
    layer_states = set(defaults.get("layer_states") or ["supported", "partial", "stub", "unsupported"])
    documented_layers = data.get("layers_documented") or [
        "inventory",
        "gov_level",
        "capabilities",
        "callgraph",
    ]
    adapters = data.get("adapters") or {}
    for adapter_name, adapter in adapters.items():
        layers = adapter.get("layers") if isinstance(adapter.get("layers"), dict) else {}
        normalized_layers = {}
        layer_errors = []
        for layer in documented_layers:
            state = layers.get(layer)
            if state == "supported":
                available = True
            else:
                available = False
            if state not in layer_states:
                layer_errors.append(f"{adapter_name}.{layer}: unavailable layer state {state!r}")
            normalized_layers[layer] = {
                "state": state,
                "available": available,
            }
        for extension in adapter.get("extensions") or []:
            extension_map[str(extension).lower()] = {
                "adapter": adapter_name,
                "status": adapter.get("status", "unsupported"),
                "layers": normalized_layers,
                "layer_errors": layer_errors,
                "parser": adapter.get("parser"),
                "parser_package": adapter.get("parser_package"),
                "grammar_package": adapter.get("grammar_package"),
                "pinned_versions": adapter.get("pinned_versions") or {},
            }
    return data, extension_map


def file_extension(path):
    suffixes = Path(path).suffixes
    if not suffixes:
        return ""
    return suffixes[-1].lower()


def parser_versions(route):
    versions = {
        "python": platform.python_version(),
    }
    pinned = route.get("pinned_versions") or {}
    for name, version in sorted(pinned.items()):
        versions[name] = str(version)
    return versions


def route_files(paths, extension_map):
    records = []
    unsupported = []
    stubbed = []
    supported = []
    for path in paths:
        extension = file_extension(path)
        route = extension_map.get(extension)
        if not route:
            record = {
                "path": path,
                "extension": extension,
                "adapter": "unsupported",
                "status": "unsupported",
                "deep_analysis": "not_available",
                "coverage": f"deep analysis unsupported for extension {extension or '<none>'}",
                "parser_versions": {"python": platform.python_version()},
            }
            unsupported.append(record)
        else:
            record = {
                "path": path,
                "extension": extension,
                "adapter": route["adapter"],
                "status": route["status"],
                "layers": route["layers"],
                "layer_errors": route["layer_errors"],
                "deep_analysis": (
                    "available"
                    if route["layers"] and all(
                        layer.get("available") for layer in route["layers"].values()
                    )
                    else "not_yet_available"
                ),
                "coverage": (
                    f"deep analysis routed to {route['adapter']}"
                    if route["layers"] and all(
                        layer.get("available") for layer in route["layers"].values()
                    )
                    else f"deep analysis adapter stubbed for extension {extension}"
                ),
                "parser_versions": parser_versions(route),
            }
            if record["deep_analysis"] == "available":
                supported.append(record)
            else:
                stubbed.append(record)
        records.append(record)

    return {
        "files": records,
        "supported": supported,
        "stubbed": stubbed,
        "unsupported": unsupported,
    }


def build_result(diff_input, policy, repo):
    _, extension_map = load_policy(policy)
    files = parse_changed_files(read_name_status(diff_input, repo))
    routed = route_files(files, extension_map)
    return {
        "gate": "language-router",
        "verdict": "pass",
        "changed_files": files,
        "routing_policy": policy,
        "coverage": {
            "supported": routed["supported"],
            "stubbed": routed["stubbed"],
            "unsupported": routed["unsupported"],
        },
        "files": routed["files"],
        "exit_code": 0,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Route changed files to language deep-analysis adapters."
    )
    parser.add_argument("diff_input", help="git diff ref (base..head) or name-status file")
    parser.add_argument(
        "--language-routing",
        default=None,
        help="language routing policy",
    )
    parser.add_argument("--repo", default=".", help="git repository to inspect")
    parser.add_argument("--json", action="store_true", help="print JSON")
    args = parser.parse_args()

    result = build_result(args.diff_input, args.language_routing, args.repo)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(yaml.safe_dump(result, allow_unicode=True, sort_keys=False), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
