#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path, PurePosixPath

import yaml


LEVEL_STRENGTH = {
    "free": 0,
    "watched": 1,
    "protected": 2,
    "frozen": 3,
}


def normalize_path(path):
    normalized = str(PurePosixPath(str(path).replace("\\", "/")))
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def load_yaml(path):
    with open(path, "r", encoding="utf-8") as stream:
        return yaml.safe_load(stream) or {}


def split_tokens(value):
    token = []
    tokens = []
    for char in value.lower():
        if char.isalnum():
            token.append(char)
        elif token:
            tokens.append("".join(token))
            token = []
    if token:
        tokens.append("".join(token))
    return tokens


def iter_repo_paths(repo):
    root = Path(repo)
    for current_root, dirs, files in os.walk(root):
        dirs[:] = sorted(name for name in dirs if name != ".git")
        for name in sorted(files):
            path = Path(current_root, name)
            yield normalize_path(path.relative_to(root))


def candidate_glob_for(path, matched_token):
    parts = normalize_path(path).split("/")
    for index, part in enumerate(parts):
        if matched_token in split_tokens(part):
            return "/".join(parts[: index + 1]) + "/**"
    return normalize_path(path)


def evidence_key(evidence):
    return (
        evidence.get("source", ""),
        evidence.get("rule_id", ""),
        evidence.get("matched", ""),
        evidence.get("path", ""),
        evidence.get("owner", ""),
    )


def evidence_identity(evidence):
    return {
        "source": evidence.get("source", ""),
        "rule_id": evidence.get("rule_id", ""),
    }


def candidate_fingerprint(path, level, evidence):
    normalized = {
        "path": path,
        "level": level,
        "evidence_rules": sorted(
            {json.dumps(evidence_identity(item), ensure_ascii=False, sort_keys=True) for item in evidence}
        ),
    }
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def add_candidate(candidates, path, level, reason, evidence):
    key = path
    existing = candidates.get(key)
    if existing is None:
        candidates[key] = {
            "path": path,
            "level": level,
            "reason": reason,
            "evidence": [evidence],
        }
        return

    existing["evidence"].append(evidence)
    existing["evidence"] = sorted(existing["evidence"], key=evidence_key)
    if LEVEL_STRENGTH.get(level, 0) > LEVEL_STRENGTH.get(existing["level"], 0):
        existing["level"] = level
        existing["reason"] = reason


def path_rule_candidates(repo, rules):
    candidates = {}
    for repo_path in iter_repo_paths(repo):
        path_tokens = set(split_tokens(repo_path))
        for rule in rules:
            tokens = [str(token).lower() for token in rule.get("tokens", [])]
            for token in sorted(tokens):
                if token not in path_tokens:
                    continue
                candidate_path = candidate_glob_for(repo_path, token)
                add_candidate(
                    candidates,
                    candidate_path,
                    rule.get("level", "protected"),
                    rule.get("reason", f"path token matched: {token}"),
                    {
                        "source": "path_naming",
                        "rule_id": str(rule.get("id", token)),
                        "matched": token,
                        "path": repo_path,
                    },
                )
    return candidates


def normalize_codeowners_pattern(pattern):
    pattern = normalize_path(pattern)
    if pattern.startswith("/"):
        pattern = pattern[1:]
    if pattern.endswith("/"):
        pattern = f"{pattern}**"
    if pattern.startswith("**/"):
        return pattern
    return pattern


def parse_codeowners(path):
    entries = []
    if not path or not os.path.exists(path):
        return entries
    with open(path, "r", encoding="utf-8") as stream:
        for raw_line in stream:
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            entries.append(
                {
                    "pattern": normalize_codeowners_pattern(parts[0]),
                    "owners": sorted(parts[1:]),
                }
            )
    return entries


def codeowner_candidates(codeowners_path, rules):
    candidates = {}
    for entry in parse_codeowners(codeowners_path):
        owners = set(entry["owners"])
        for rule in rules:
            matched_owners = sorted(owners & set(rule.get("owners", [])))
            for owner in matched_owners:
                add_candidate(
                    candidates,
                    entry["pattern"],
                    rule.get("level", "protected"),
                    rule.get("reason", f"CODEOWNERS matched: {owner}"),
                    {
                        "source": "codeowners",
                        "rule_id": str(rule.get("id", owner)),
                        "matched": entry["pattern"],
                        "path": entry["pattern"],
                        "owner": owner,
                    },
                )
    return candidates


def merge_candidates(*candidate_maps):
    merged = {}
    for candidate_map in candidate_maps:
        for candidate in candidate_map.values():
            for evidence in candidate["evidence"]:
                add_candidate(
                    merged,
                    candidate["path"],
                    candidate["level"],
                    candidate["reason"],
                    evidence,
                )
    return merged


def previous_statuses(path):
    if not path:
        return {}, {}
    data = load_yaml(path)
    if "bootstrap_sensitive_zones" in data:
        data = data.get("bootstrap_sensitive_zones") or {}
    accepted = {}
    rejected = {}
    for candidate in data.get("candidates", []):
        fingerprint = candidate.get("fingerprint")
        status = candidate.get("status")
        if not fingerprint:
            continue
        if status == "accepted":
            accepted[fingerprint] = candidate
        elif status == "rejected":
            rejected[fingerprint] = candidate
    return accepted, rejected


def finalize_candidates(candidate_map, previous_path=None):
    accepted, rejected = previous_statuses(previous_path)
    proposed = []
    suppressed = {"accepted": 0, "rejected": 0}

    for candidate in sorted(candidate_map.values(), key=lambda item: (item["path"], item["level"])):
        evidence = sorted(candidate["evidence"], key=evidence_key)
        fingerprint = candidate_fingerprint(candidate["path"], candidate["level"], evidence)
        if fingerprint in rejected:
            suppressed["rejected"] += 1
            continue
        if fingerprint in accepted:
            suppressed["accepted"] += 1
            continue
        proposed.append(
            {
                "path": candidate["path"],
                "level": candidate["level"],
                "reason": candidate["reason"],
                "status": "proposed",
                "fingerprint": fingerprint,
                "rejected_reason": None,
                "rejected_by": None,
                "evidence": evidence,
            }
        )
    return proposed, suppressed


def build_draft(repo, rules_path, codeowners_path=None, previous_path=None):
    rules = load_yaml(rules_path)
    path_candidates = path_rule_candidates(repo, rules.get("path_rules", []))
    effective_codeowners = codeowners_path
    if effective_codeowners is None:
        default_codeowners = os.path.join(repo, "CODEOWNERS")
        effective_codeowners = default_codeowners if os.path.exists(default_codeowners) else None
    owner_candidates = codeowner_candidates(effective_codeowners, rules.get("codeowner_rules", []))
    candidates, suppressed = finalize_candidates(
        merge_candidates(path_candidates, owner_candidates),
        previous_path,
    )
    return {
        "bootstrap_sensitive_zones": {
            "generated_by": "bootstrap-sensitive-zones",
            "source_repo": os.path.abspath(repo).replace("\\", "/"),
            "mode": "draft_only",
            "adoption_note": "Candidates are proposed only; apply them manually after review.",
            "summary": {
                "candidate_count": len(candidates),
                "suppressed_accepted": suppressed["accepted"],
                "suppressed_rejected": suppressed["rejected"],
                "codeowners_read": bool(effective_codeowners and os.path.exists(effective_codeowners)),
            },
            "candidates": candidates,
        }
    }


def print_text(result):
    print(yaml.safe_dump(result, allow_unicode=True, sort_keys=False))


def main():
    parser = argparse.ArgumentParser(
        description="Generate a draft sensitive-zones candidate list from path rules and CODEOWNERS."
    )
    parser.add_argument("repo", help="target repository path to scan")
    parser.add_argument("--rules", required=True, help="YAML file with path_rules/codeowner_rules")
    parser.add_argument("--codeowners", help="optional CODEOWNERS path")
    parser.add_argument("--previous", help="optional previous draft with accepted/rejected fingerprints")
    parser.add_argument("--json", action="store_true", help="print structured JSON")
    args = parser.parse_args()

    try:
        result = build_draft(args.repo, args.rules, args.codeowners, args.previous)
    except Exception as error:
        result = {
            "bootstrap_sensitive_zones": {
                "generated_by": "bootstrap-sensitive-zones",
                "source_repo": os.path.abspath(args.repo).replace("\\", "/"),
                "mode": "draft_only",
                "summary": {
                    "candidate_count": 0,
                    "suppressed_accepted": 0,
                    "suppressed_rejected": 0,
                    "codeowners_read": False,
                },
                "candidates": [],
                "errors": [str(error)],
            }
        }
        if args.json:
            print(json.dumps(result["bootstrap_sensitive_zones"], ensure_ascii=False, sort_keys=True, indent=2))
        else:
            print_text(result)
        return 1

    if args.json:
        print(json.dumps(result["bootstrap_sensitive_zones"], ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print_text(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
