#!/usr/bin/env bash
# =====================================================================
# run.sh — 변경 거버넌스 러너 (키트 진입점 · spine verify hook)
# =====================================================================
# 전 런타임 게이트를 결정적으로 순서 실행 → 감사카드 1장 + 최종 판정.
# 판정에 LLM 없음. 같은 입력이면 항상 같은 결과(결정론).
#
# ★조립 대상(누락 없이):
#   [1층] 의도이탈 + 민감경로 + @gov 함수      → generate-change-evidence 가 3축 조립 + 카드
#   [2층] 신규 위험능력                          → check-new-capabilities (카드 밖 — 여기서 명시 추가)
#   [3층] sink 간접영향                          → check-indirect-impact  (카드 밖 — 여기서 명시 추가)
#   [메타] 정책 자기무력화                        → check-policy-change    (카드 밖 — 여기서 명시 추가)
#   최종 = 네 결과의 가장 센 판정(차단 > 승인 > 통과).
#
# 사용:  ./run.sh <base>..<head> [--repo <대상repo>] [--policies <정책dir>] [--output <카드경로>]
# 종료코드: 0 통과 / 1 차단 / 2 승인필요
# =====================================================================
set -uo pipefail
KIT="$(cd "$(dirname "$0")" && pwd)"
G="$KIT/gates"
POL="$KIT/policies"
ZONES="$POL/sensitive-zones.yaml"
CAPS="$POL/sensitive-capabilities.yaml"
JAVA_CAPS="$POL/java-sensitive-capabilities.yaml"
ROUTING="$POL/approval-routing.yaml"
SINKS="$POL/sink-registry.yaml"
LANGUAGE_ROUTING="$POL/language-routing.yaml"
FRAMEWORK_ANNOTATIONS="$POL/framework-annotations.yaml"

RANGE="${1:?사용: ./run.sh <base>..<head> [--repo <repo>] [--policies <정책dir>] [--output <카드>]}"; shift
REPO="."; OUT="change-evidence.yaml"
while [ $# -gt 0 ]; do case "$1" in
  --repo)   REPO="$2"; shift 2;;
  --policies) POL="$2"; shift 2;;
  --output) OUT="$2"; shift 2;;
  *) echo "알 수 없는 인자: $1"; exit 64;;
esac; done

# base..head 범위여야 함수·능력·정책 게이트가 돈다(git 두 ref 필요).
HAS_RANGE=0; case "$RANGE" in *..*) HAS_RANGE=1;; esac
HAS_JAVA_CHANGE=0
if [ "$HAS_RANGE" = 1 ] && git -C "$REPO" diff --name-only "$RANGE" -- '*.java' 2>/dev/null | grep -q .; then
  HAS_JAVA_CHANGE=1
fi

if [ ! -d "$POL" ]; then
  echo "✗ 분석 실패: 정책 디렉토리 없음: $POL"
  echo "  tool_owner: change-governance-kit-owner"
  exit 2
fi
POL="$(cd "$POL" && pwd)"
ZONES="$POL/sensitive-zones.yaml"
CAPS="$POL/sensitive-capabilities.yaml"
JAVA_CAPS="$POL/java-sensitive-capabilities.yaml"
ROUTING="$POL/approval-routing.yaml"
SINKS="$POL/sink-registry.yaml"
LANGUAGE_ROUTING="$POL/language-routing.yaml"
FRAMEWORK_ANNOTATIONS="$POL/framework-annotations.yaml"
for required_policy in "$ZONES" "$CAPS" "$ROUTING" "$SINKS" "$LANGUAGE_ROUTING" "$FRAMEWORK_ANNOTATIONS"; do
  if [ ! -f "$required_policy" ]; then
    echo "✗ 분석 실패: 필수 정책 파일 없음: $required_policy"
    echo "  tool_owner: change-governance-kit-owner"
    exit 2
  fi
done
if [ "$HAS_JAVA_CHANGE" = 1 ] && [ ! -f "$JAVA_CAPS" ]; then
  echo "✗ 분석 실패: 필수 정책 파일 없음: $JAVA_CAPS"
  echo "  tool_owner: change-governance-kit-owner"
  exit 2
fi

cd "$REPO" || { echo "✗ 대상 repo 없음: $REPO"; exit 66; }
INTENT=""; [ -f change-intent.yaml ] && INTENT="change-intent.yaml"

warn_change_intent_shape(){
  [ -n "$INTENT" ] || return 0
  python3 - "$INTENT" <<'PY'
import sys
try:
    import yaml
    with open(sys.argv[1], "r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream) or {}
except Exception:
    sys.exit(0)

change_intent = data.get("change_intent")
change_intent_is_dict = isinstance(change_intent, dict)
allowed = change_intent.get("allowed_paths") if change_intent_is_dict else None
top_level_keys = {"allowed_paths", "forbidden_paths", "requirement_id"}
has_top_level_intent_keys = bool(top_level_keys.intersection(data))

if has_top_level_intent_keys and (not change_intent_is_dict or not allowed):
    print("⚠ change-intent.yaml: allowed_paths 가 top-level 에 있음 — 'change_intent:' 아래 중첩이 정식 스키마(현재 빈 선언으로 읽혀 전 변경이 승인요구됨). 예: policies/change-intent.example.yaml")
elif change_intent_is_dict and not allowed:
    print("⚠ change-intent.yaml: allowed_paths 가 비어있음 — 전 변경이 out_of_scope(승인요구)로 처리됨")
PY
}

warn_output_location(){
  case "$OUT" in
    /*) return 0;;
    *) echo "⚠ 감사카드가 대상 repo 안에 생성됨($OUT) — 커밋에 섞이면 다음 diff 오염. --output <외부경로> 또는 .gitignore 등록 권장";;
  esac
}

hr(){ printf '─%.0s' $(seq 1 66); echo; }
GATE_TIMEOUT_SECONDS="${ACGH_GATE_TIMEOUT_SECONDS:-60}"
case "$GATE_TIMEOUT_SECONDS" in
  ''|*[!0-9]*|0) echo "✗ 분석 실패: ACGH_GATE_TIMEOUT_SECONDS는 양의 정수여야 함"; exit 2;;
esac
ANALYSIS_FAILURES=()
RUN_OUTPUT=""; RUN_EXIT=0; RUN_FAILED=0

# 정상 판정 exit와 실행 실패를 분리한다. 실패는 승인요구로 정규화하되,
# 다른 게이트의 실제 차단(exit 1)이 있으면 최종 우선순위에서 차단이 이긴다.
run_gate(){
  local gate="$1" allowed="$2" script="$3"; shift 3
  local tmp pid watcher rc timed_out=0
  tmp="$(mktemp)"
  if [ ! -f "$script" ]; then
    RUN_OUTPUT="게이트 파일 없음: $script"; RUN_EXIT=2; RUN_FAILED=1
    ANALYSIS_FAILURES+=("$gate: gate_missing")
    rm -f "$tmp"
    return
  fi

  python3 "$script" "$@" >"$tmp" 2>&1 & pid=$!
  (
    sleep "$GATE_TIMEOUT_SECONDS"
    if kill -0 "$pid" 2>/dev/null; then
      printf 'timeout' >"$tmp.timeout"
      kill -TERM "$pid" 2>/dev/null || true
      sleep 1
      kill -KILL "$pid" 2>/dev/null || true
    fi
  ) >/dev/null 2>&1 & watcher=$!
  wait "$pid"; rc=$?
  kill "$watcher" 2>/dev/null || true
  wait "$watcher" 2>/dev/null || true
  [ -f "$tmp.timeout" ] && timed_out=1
  RUN_OUTPUT="$(cat "$tmp")"
  rm -f "$tmp" "$tmp.timeout"

  RUN_FAILED=0; RUN_EXIT="$rc"
  if [ "$timed_out" = 1 ]; then
    RUN_FAILED=1; ANALYSIS_FAILURES+=("$gate: timeout")
  elif printf '%s\n' "$RUN_OUTPUT" | grep -q 'Traceback (most recent call last)'; then
    RUN_FAILED=1; ANALYSIS_FAILURES+=("$gate: traceback")
  elif ! case " $allowed " in *" $rc "*) true;; *) false;; esac; then
    RUN_FAILED=1; ANALYSIS_FAILURES+=("$gate: abnormal_exit_$rc")
  fi
  [ "$RUN_FAILED" = 1 ] && RUN_EXIT=2
}

show_analysis_failure(){
  local gate="$1"
  echo "    ✗ 분석 실패: $gate (fail-closed → approval_required)"
  echo "      tool_owner: change-governance-kit-owner"
  [ -n "$RUN_OUTPUT" ] && printf '%s\n' "$RUN_OUTPUT" | head -6 | sed 's/^/      /'
}

append_capability_trace(){
  local card="$1" capability_json="$2" run_failed="$3"
  python3 - "$card" "$capability_json" "$run_failed" <<'PY'
import json
import sys
from pathlib import Path

import yaml

card_path, result_path, run_failed = sys.argv[1:]
notes = []
result = {}
if run_failed == "1":
    notes.append("capabilities analysis unavailable: check-new-capabilities execution failed")
else:
    try:
        result = json.loads(Path(result_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        result = {}
    for item in result.get("fail_closed", []):
        notes.append(
            f"capabilities analysis unavailable for {item.get('path', '<unknown>')}: "
            f"{item.get('reason', 'analysis failed')}"
        )
    for item in result.get("errors", []):
        path = item.get("path", "<unknown>")
        side = item.get("side")
        detail = item.get("error") or item.get("reason") or "analysis error"
        side_text = f" ({side})" if side else ""
        notes.append(f"capabilities analysis unavailable for {path}{side_text}: {detail}")

if result:
    verdict = result.get("verdict", "approval_required")
    if verdict == "approval_required":
        print("    APPROVAL_REQUIRED: 신규 민감 능력 또는 분석 오류가 감지되었습니다.")
    elif result.get("warned_capabilities"):
        print("    PASS: watched 신규 능력이 감지되었습니다.")
    else:
        print("    PASS: 신규 민감 능력이 감지되지 않았습니다.")

    rendered = []
    for key, level in (
        ("new_capabilities", "protected"),
        ("warned_capabilities", "watched"),
        ("shadow_capabilities", "shadow"),
    ):
        for item in result.get(key, []):
            rendered.append(
                f"{level}: {item.get('path', '<unknown>')}::{item.get('id', '<unknown>')} "
                f"level={item.get('level', level)}"
            )
    for item in result.get("fail_closed", []):
        rendered.append(
            f"fail_closed: {item.get('path', '<unknown>')} {item.get('reason', 'analysis failed')}"
        )
    for line in rendered[:8]:
        print(f"    {line}")
    if len(rendered) > 8:
        print(f"    … 외 {len(rendered) - 8}건")
else:
    print("    능력 게이트 출력 파싱 불가 — 원문 앞 6줄:")
    try:
        raw_lines = Path(result_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        raw_lines = []
    for line in raw_lines[:6]:
        print(f"    {line}")

try:
    with open(card_path, "r", encoding="utf-8") as stream:
        card = yaml.safe_load(stream) or {}
except Exception:
    print("    capabilities trace 주입 불가: 카드가 유효 YAML 아님")
    raise SystemExit(0)
if not isinstance(card, dict) or not isinstance(card.get("change_evidence"), dict):
    print("    capabilities trace 주입 불가: 카드가 유효 YAML 아님")
    raise SystemExit(0)

if notes:
    evidence = card.setdefault("change_evidence", {})
    coverage = evidence.setdefault("coverage_statement", {})
    not_checked = coverage.setdefault("not_checked", [])
    coverage["not_checked"] = sorted(set(not_checked + notes))
    with open(card_path, "w", encoding="utf-8") as stream:
        yaml.safe_dump(card, stream, allow_unicode=True, sort_keys=False)
PY
}
echo "════════════════════════════════════════════════════════════════"
echo "  변경 감사카드 · AI Change Governance Kit"
echo "════════════════════════════════════════════════════════════════"
echo "  대상 repo : $(basename "$(pwd)")"
echo "  변경 범위 : $RANGE"
[ -n "$INTENT" ] || echo "  (의도 선언 누락 — 카드 게이트가 미선언으로 판정)"
warn_change_intent_shape
warn_output_location
hr

# ── 감사카드 + 3축(의도·민감경로·@gov 함수) 판정 ────────────────────
INTENT_ARGS=(); [ -n "$INTENT" ] && INTENT_ARGS=(--change-intent "$INTENT")
run_gate "generate-change-evidence" "0 1 2" "$G/generate-change-evidence.py" "$RANGE" \
  --sensitive-zones "$ZONES" --approval-routing "$ROUTING" \
  --language-routing "$LANGUAGE_ROUTING" \
  --framework-annotations "$FRAMEWORK_ANNOTATIONS" \
  ${INTENT_ARGS[@]+"${INTENT_ARGS[@]}"} --repo .
CARD="$RUN_OUTPUT"
ge_exit="$RUN_EXIT"; ge_failed="$RUN_FAILED"
printf '%s\n' "$CARD" > "$OUT"
echo "▸ [1층] 의도이탈·민감경로·@gov 함수 (감사카드 3축)"
if [ "$ge_failed" = 1 ]; then show_analysis_failure "generate-change-evidence"; else
  printf '%s\n' "$CARD" | grep -E 'verdict:|status:|frozen_touched|protected_touched|out_of_scope|missing_expected|forbidden_touched|reviewer_required' | sed 's/^/    /' | head -20
fi

# ── [2층] 신규 위험 능력 (감사카드에 미포함 → 여기서 명시 조립) ─────
cap_exit=0
echo "▸ [2층] 신규 위험 능력 (외부호출·암복호·실행 등 신규 도입?)"
if [ "$HAS_RANGE" = 1 ]; then
  run_gate "check-new-capabilities" "0 2" "$G/check-new-capabilities.py" "$RANGE" "$CAPS" --java-policy "$JAVA_CAPS" --repo . --json
  CAP_OUT="$RUN_OUTPUT"; cap_exit="$RUN_EXIT"
  cap_failed="$RUN_FAILED"
  if [ "$RUN_FAILED" = 1 ]; then show_analysis_failure "check-new-capabilities"; fi
  cap_trace_json="$(mktemp)"
  printf '%s\n' "$CAP_OUT" > "$cap_trace_json"
  append_capability_trace "$OUT" "$cap_trace_json" "$cap_failed"
  rm -f "$cap_trace_json"
else
  cap_exit=2; ANALYSIS_FAILURES+=("check-new-capabilities: range_required")
  echo "    ✗ 분석 실패: base..head 범위 아님 (능력 층 미실행)"
  echo "      tool_owner: change-governance-kit-owner"
fi

# ── [3층] sink 간접영향 (감사카드에 미포함 → 여기서 명시 조립) ──────
indirect_exit=0
echo "▸ [3층] sink 간접영향 (등록 sink 의 N홉 의존함수 변경?)"
if [ "$HAS_RANGE" = 1 ]; then
  run_gate "check-indirect-impact" "0 2" "$G/check-indirect-impact.py" "$RANGE" \
    --sensitive-zones "$ZONES" --sink-registry "$SINKS" \
    --language-routing "$LANGUAGE_ROUTING" --repo .
  INDIRECT_OUT="$RUN_OUTPUT"; indirect_exit="$RUN_EXIT"
  if [ "$RUN_FAILED" = 1 ]; then show_analysis_failure "check-indirect-impact"; else
    printf '%s\n' "$INDIRECT_OUT" | head -8 | sed 's/^/    /'
  fi
else
  indirect_exit=2; ANALYSIS_FAILURES+=("check-indirect-impact: range_required")
  echo "    ✗ 분석 실패: base..head 범위 아님 (간접영향 층 미실행)"
  echo "      tool_owner: change-governance-kit-owner"
fi

# ── [메타] 정책 자기무력화 (감사카드에 미포함 → 여기서 명시 조립) ───
pol_exit=0
echo "▸ [메타] 정책 자기무력화 (게이트/정책 완화·집행우회?)"
if [ "$HAS_RANGE" = 1 ]; then
  run_gate "check-policy-change" "0 2" "$G/check-policy-change.py" "$RANGE" --repo .
  POL_OUT="$RUN_OUTPUT"; pol_exit="$RUN_EXIT"
  if [ "$RUN_FAILED" = 1 ]; then show_analysis_failure "check-policy-change"; else
    printf '%s\n' "$POL_OUT" | head -6 | sed 's/^/    /'
  fi
else
  pol_exit=2; ANALYSIS_FAILURES+=("check-policy-change: range_required")
  echo "    ✗ 분석 실패: base..head 범위 아님 (정책변경 층 미실행)"
  echo "      tool_owner: change-governance-kit-owner"
fi

# ── 최종 판정 = 가장 센 것 (차단 1 > 승인 2 > 통과 0) ────────────────
final=0; label="🟢 PASS (통과)"
if [ "$ge_exit" = 1 ] || [ "$cap_exit" = 1 ] || [ "$indirect_exit" = 1 ] || [ "$pol_exit" = 1 ]; then
  final=1; label="🔴 BLOCKED (차단)"
elif [ "$ge_exit" = 2 ] || [ "$cap_exit" = 2 ] || [ "$indirect_exit" = 2 ] || [ "$pol_exit" = 2 ]; then
  final=2; label="🟠 APPROVAL_REQUIRED (승인필요)"
fi

hr
echo "  게이트 판정 : 카드3축=$ge_exit · 능력=$cap_exit · 간접영향=$indirect_exit · 정책=$pol_exit  (0통과/1차단/2승인)"
[ "${#ANALYSIS_FAILURES[@]}" -gt 0 ] && echo "  분석 실패   : ${ANALYSIS_FAILURES[*]+${ANALYSIS_FAILURES[*]}}"
echo "  최종 판정   : $label   (exit $final)"
echo "  감사카드    : $OUT"
echo "════════════════════════════════════════════════════════════════"
exit $final
