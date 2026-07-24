#!/usr/bin/env python3
import argparse
import hashlib
import importlib
import json
import sys


SAMPLES = {
    "java": b"class AccountService { void transfer() { } }\n",
    "javascript": b"function transfer() { return 1; }\n",
    "typescript": b"type Amount = number;\nfunction transfer(x: Amount): Amount { return x; }\n",
    "tsx": b"type Props = { amount: number };\nconst View = (p: Props) => <div>{p.amount}</div>;\n",
}

LANGUAGE_MODULES = {
    "java": "tree_sitter_java",
    "javascript": "tree_sitter_javascript",
    "typescript": "tree_sitter_typescript",
    "tsx": "tree_sitter_typescript",
}


def package_version(module_name):
    try:
        from importlib import metadata

        return metadata.version(module_name.replace("_", "-"))
    except Exception:
        module = importlib.import_module(module_name)
        return getattr(module, "__version__", "unknown")


def make_language(module, language_name):
    from tree_sitter import Language

    if language_name == "typescript":
        capsule = module.language_typescript()
    elif language_name == "tsx":
        capsule = module.language_tsx()
    else:
        capsule = module.language()
    return Language(capsule)


def parse_sample(language_name, source):
    from tree_sitter import Parser

    module = importlib.import_module(LANGUAGE_MODULES[language_name])
    language = make_language(module, language_name)
    try:
        parser = Parser(language)
    except TypeError:
        parser = Parser()
        parser.language = language
    tree = parser.parse(source)
    root = tree.root_node
    return {
        "language": language_name,
        "root_type": root.type,
        "has_error": root.has_error,
        "tree_md5": hashlib.md5(str(root).encode("utf-8")).hexdigest(),
    }


def build_result():
    errors = []
    languages = []
    versions = {}
    try:
        import tree_sitter  # noqa: F401

        versions["tree-sitter"] = package_version("tree_sitter")
    except Exception as error:
        return {
            "gate": "check-tree-sitter-languages",
            "verdict": "approval_required",
            "languages": [],
            "versions": versions,
            "errors": [{"error": "missing_dependency", "detail": str(error)}],
            "exit_code": 2,
        }

    for language_name, source in SAMPLES.items():
        module_name = LANGUAGE_MODULES[language_name]
        try:
            versions[module_name.replace("_", "-")] = package_version(module_name)
            parsed = parse_sample(language_name, source)
            languages.append(parsed)
            if parsed["has_error"]:
                errors.append({"error": "parse_error", "language": language_name})
        except Exception as error:
            errors.append(
                {
                    "error": "language_load_failed",
                    "language": language_name,
                    "detail": str(error),
                }
            )

    exit_code = 2 if errors else 0
    return {
        "gate": "check-tree-sitter-languages",
        "verdict": "approval_required" if errors else "pass",
        "languages": languages,
        "versions": dict(sorted(versions.items())),
        "errors": errors,
        "exit_code": exit_code,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Verify pinned tree-sitter language packages can parse samples."
    )
    parser.add_argument("--json", action="store_true", help="print JSON")
    args = parser.parse_args()

    result = build_result()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        for item in result["languages"]:
            print(f"{item['language']}: {item['root_type']} md5={item['tree_md5']}")
        for error in result["errors"]:
            print(f"error: {error}")
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
