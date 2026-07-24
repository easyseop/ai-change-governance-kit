#!/usr/bin/env bash
# run.sh 교차리뷰 회귀: 최종판정 조립, 분석실패, 대상 정책 override, 간접영향.
set -uo pipefail
KIT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
PASS=0; TOTAL=0

make_repo(){
  local repo="$1"
  mkdir -p "$repo/app" "$repo/custom/crown" "$repo/policies"
  git -C "$repo" init -q
  git -C "$repo" config user.email kit-test@example.invalid
  git -C "$repo" config user.name kit-test
  cp "$KIT/policies/"*.yaml "$repo/policies/"
  cat >"$repo/change-intent.yaml" <<'YAML'
change_intent:
  requirement_id: KIT-TEST
  purpose: run.sh adversarial verification
  author: test
  allowed_paths: ["app/**", "custom/**", "policies/**"]
  forbidden_paths: []
YAML
  printf 'def stable():\n    return True\n' >"$repo/app/service.py"
  git -C "$repo" add . && git -C "$repo" commit -qm base
}

run_case(){
  local name="$1" expected_rc="$2" expected_text="$3" runner="$4" repo="$5"; shift 5
  local output rc
  TOTAL=$((TOTAL + 1))
  output="$(ACGH_GATE_TIMEOUT_SECONDS=1 bash "$runner" HEAD~1..HEAD --repo "$repo" "$@" 2>&1)"; rc=$?
  if [ "$rc" = "$expected_rc" ] && printf '%s\n' "$output" | grep -q -- "$expected_text"; then
    echo "PASS $name"; PASS=$((PASS + 1))
  else
    echo "FAIL $name (exit=$rc expected=$expected_rc, missing=$expected_text)"
    printf '%s\n' "$output" | tail -12 | sed 's/^/  /'
  fi
}

run_pipe_latency_case(){
  local name="$1" runner="$2" repo="$3"
  local output rc started elapsed
  TOTAL=$((TOTAL + 1))
  started="$(date +%s)"
  output="$(ACGH_GATE_TIMEOUT_SECONDS=30 bash "$runner" HEAD~1..HEAD --repo "$repo" 2>&1)"; rc=$?
  elapsed=$(( $(date +%s) - started ))
  if [ "$rc" = 0 ] && [ "$elapsed" -lt 10 ]; then
    echo "PASS $name"; PASS=$((PASS + 1))
  else
    echo "FAIL $name (exit=$rc elapsed=${elapsed}s expected=<10s)"
    printf '%s\n' "$output" | tail -12 | sed 's/^/  /'
  fi
}

run_absent_case(){
  local name="$1" unexpected_text="$2" runner="$3" repo="$4"; shift 4
  local output
  TOTAL=$((TOTAL + 1))
  output="$(ACGH_GATE_TIMEOUT_SECONDS=1 bash "$runner" HEAD~1..HEAD --repo "$repo" "$@" 2>&1)"
  if ! printf '%s\n' "$output" | grep -q -- "$unexpected_text"; then
    echo "PASS $name"; PASS=$((PASS + 1))
  else
    echo "FAIL $name (unexpected=$unexpected_text)"
    printf '%s\n' "$output" | tail -12 | sed 's/^/  /'
  fi
}

run_expected_missing_case(){
  local name="$1" runner="$2" repo="$3" card="$4"
  local output rc
  TOTAL=$((TOTAL + 1))
  output="$(ACGH_GATE_TIMEOUT_SECONDS=1 bash "$runner" HEAD~1..HEAD --repo "$repo" --output "$card" 2>&1)"; rc=$?
  if [ "$rc" = 2 ] &&
     grep -q 'missing_expected:app/required_patch.py' "$card" &&
     printf '%s\n' "$output" | grep -q 'missing_expected' &&
     ! grep -q 'out_of_scope:' "$card"; then
    echo "PASS $name"; PASS=$((PASS + 1))
  else
    echo "FAIL $name (exit=$rc expected=2, card missing expected_paths evidence)"
    printf '%s\n' "$output" | tail -12 | sed 's/^/  /'
    [ -f "$card" ] && tail -20 "$card" | sed 's/^/  card: /'
  fi
}

validate_no_intent_card(){
  python3 - "$1" <<'PY'
import sys
import yaml

with open(sys.argv[1], encoding="utf-8") as stream:
    evidence = (yaml.safe_load(stream) or {}).get("change_evidence", {})

coverage = evidence.get("coverage_statement", {})
reasons = evidence.get("reasons", [])
changed_files = evidence.get("changed_files", [])
checked_gates = [item.get("gate") for item in coverage.get("checked", [])]
valid = (
    evidence.get("verdict") == "approval_required"
    and evidence.get("intent_check", {}).get("status") == "not_declared"
    and "check-change-intent" not in checked_gates
    and "check-sensitive-zones" in checked_gates
    and any("intent_not_declared" in item for item in coverage.get("not_checked", []))
    and any(reason.startswith("intent_not_declared:") for reason in reasons)
    and not any(item.get("in_allowed_paths") is True for item in changed_files)
)
raise SystemExit(0 if valid else 1)
PY
}

run_no_intent_case(){
  local name="$1" runner="$2" repo="$3" card="$4"
  local output rc
  TOTAL=$((TOTAL + 1))
  output="$(ACGH_GATE_TIMEOUT_SECONDS=1 /bin/bash "$runner" HEAD~1..HEAD --repo "$repo" --output "$card" 2>&1)"; rc=$?
  if [ "$rc" = 2 ] &&
     ! printf '%s\n' "$output" | grep -q 'unbound variable' &&
     printf '%s\n' "$output" | grep -q '의도 선언 누락 — 카드 게이트가 미선언으로 판정' &&
     printf '%s\n' "$output" | grep -q '\[2층\] 신규 위험 능력' &&
     printf '%s\n' "$output" | grep -q '\[3층\] sink 간접영향' &&
     printf '%s\n' "$output" | grep -q '\[메타\] 정책 자기무력화' &&
     [ -s "$card" ] &&
     validate_no_intent_card "$card"; then
    echo "PASS $name"; PASS=$((PASS + 1))
  else
    echo "FAIL $name (exit=$rc expected=2, missing no-crash/layer/honest-card evidence)"
    printf '%s\n' "$output" | tail -16 | sed 's/^/  /'
    [ -f "$card" ] && tail -20 "$card" | sed 's/^/  card: /'
  fi
}

run_language_policy_case(){
  local name="$1" runner="$2" repo="$3" card="$4"
  local output rc
  TOTAL=$((TOTAL + 1))
  output="$(ACGH_GATE_TIMEOUT_SECONDS=1 bash "$runner" HEAD~1..HEAD --repo "$repo" --policies "$KIT/policies" --output "$card" 2>&1)"; rc=$?
  if [ "$rc" = 0 ] &&
     printf '%s\n' "$output" | grep -q 'PASS' &&
     grep -Eq "^[[:space:]]*- ['\"]?java \.java layers not available: callgraph['\"]?[[:space:]]*$" "$card"; then
    echo "PASS $name"; PASS=$((PASS + 1))
  else
    echo "FAIL $name (exit=$rc expected=0, missing kit language routing coverage)"
    printf '%s\n' "$output" | tail -16 | sed 's/^/  /'
    [ -f "$card" ] && tail -30 "$card" | sed 's/^/  card: /'
  fi
}

run_language_policy_negative_case(){
  local name="$1" source_card="$2" rigged_card="$3"
  TOTAL=$((TOTAL + 1))
  sed 's/layers not available: callgraph/layers not available: callgraph, capabilities/' "$source_card" > "$rigged_card"
  if ! grep -Eq "^[[:space:]]*- ['\"]?java \.java layers not available: callgraph['\"]?[[:space:]]*$" "$rigged_card"; then
    echo "PASS $name"; PASS=$((PASS + 1))
  else
    echo "FAIL $name (exact layer assertion accepted a prefixed/suffixed regression)"
  fi
}

run_capability_failure_card_case(){
  local name="$1" runner="$2" repo="$3" expected_text="$4" card="$5"
  local output rc
  TOTAL=$((TOTAL + 1))
  output="$(ACGH_GATE_TIMEOUT_SECONDS=1 bash "$runner" HEAD~1..HEAD --repo "$repo" --output "$card" 2>&1)"; rc=$?
  if [ "$rc" = 2 ] &&
     printf '%s\n' "$output" | grep -q "$expected_text" &&
     grep -q 'capabilities analysis unavailable' "$card"; then
    echo "PASS $name"; PASS=$((PASS + 1))
  else
    echo "FAIL $name (exit=$rc expected=2, missing capability failure trace)"
    printf '%s\n' "$output" | tail -12 | sed 's/^/  /'
    [ -f "$card" ] && tail -20 "$card" | sed 's/^/  card: /'
  fi
}

run_capability_console_case(){
  local name="$1" runner="$2" repo="$3"
  local output rc
  TOTAL=$((TOTAL + 1))
  output="$(ACGH_GATE_TIMEOUT_SECONDS=1 bash "$runner" HEAD~1..HEAD --repo "$repo" 2>&1)"; rc=$?
  if [ "$rc" = 2 ] &&
     printf '%s\n' "$output" | grep -q 'protected: app/service.py::outbound_network level=protected'; then
    echo "PASS $name"; PASS=$((PASS + 1))
  else
    echo "FAIL $name (exit=$rc expected=2, missing capability path and level)"
    printf '%s\n' "$output" | tail -16 | sed 's/^/  /'
  fi
}

run_capability_parse_notice_case(){
  local name="$1" runner="$2" repo="$3"
  local output rc
  TOTAL=$((TOTAL + 1))
  output="$(ACGH_GATE_TIMEOUT_SECONDS=1 bash "$runner" HEAD~1..HEAD --repo "$repo" 2>&1)"; rc=$?
  if [ "$rc" = 2 ] &&
     printf '%s\n' "$output" | grep -q '능력 게이트 출력 파싱 불가 — 원문 앞 6줄' &&
     printf '%s\n' "$output" | grep -q 'diagnostic from capability gate' &&
     printf '%s\n' "$output" | grep -q 'APPROVAL_REQUIRED'; then
    echo "PASS $name"; PASS=$((PASS + 1))
  else
    echo "FAIL $name (exit=$rc expected=2, missing parse-failure notice/raw output/final verdict)"
    printf '%s\n' "$output" | tail -20 | sed 's/^/  /'
  fi
}

run_shadow_capability_console_case(){
  local name="$1" runner="$2" repo="$3"
  local output rc
  TOTAL=$((TOTAL + 1))
  output="$(ACGH_GATE_TIMEOUT_SECONDS=1 bash "$runner" HEAD~1..HEAD --repo "$repo" 2>&1)"; rc=$?
  if [ "$rc" = 0 ] &&
     printf '%s\n' "$output" | grep -q 'shadow: app/service.py::shadow_exec level=protected' &&
     printf '%s\n' "$output" | grep -q '최종 판정   : .*PASS'; then
    echo "PASS $name"; PASS=$((PASS + 1))
  else
    echo "FAIL $name (exit=$rc expected=0, missing shadow level or pass verdict)"
    printf '%s\n' "$output" | tail -20 | sed 's/^/  /'
  fi
}

run_invalid_card_trace_case(){
  local name="$1" runner="$2" repo="$3" card="$4"
  local output rc
  TOTAL=$((TOTAL + 1))
  output="$(ACGH_GATE_TIMEOUT_SECONDS=1 bash "$runner" HEAD~1..HEAD --repo "$repo" --output "$card" 2>&1)"; rc=$?
  if [ "$rc" = 2 ] &&
     printf '%s\n' "$output" | grep -q 'capabilities trace 주입 불가: 카드가 유효 YAML 아님' &&
     ! printf '%s\n' "$output" | grep -q 'yaml\.parser\|yaml\.scanner'; then
    echo "PASS $name"; PASS=$((PASS + 1))
  else
    echo "FAIL $name (exit=$rc expected=2, invalid-card trace handling regressed)"
    printf '%s\n' "$output" | tail -20 | sed 's/^/  /'
  fi
}

run_missing_language_policy_preflight_case(){
  local name="$1" runner="$2" repo="$3" policies="$4" card="$5"
  local output rc
  TOTAL=$((TOTAL + 1))
  output="$(ACGH_GATE_TIMEOUT_SECONDS=1 bash "$runner" HEAD~1..HEAD --repo "$repo" --policies "$policies" --output "$card" 2>&1)"; rc=$?
  if [ "$rc" = 2 ] &&
     printf '%s\n' "$output" | grep -q '필수 정책 파일 없음: .*language-routing.yaml' &&
     { [ ! -f "$card" ] || ! grep -q 'verdict: blocked' "$card"; }; then
    echo "PASS $name"; PASS=$((PASS + 1))
  else
    echo "FAIL $name (exit=$rc expected=2, expected missing language-routing preflight without blocked card)"
    printf '%s\n' "$output" | tail -16 | sed 's/^/  /'
    [ -f "$card" ] && tail -30 "$card" | sed 's/^/  card: /'
  fi
}

run_missing_required_policy_preflight_case(){
  local name="$1" runner="$2" repo="$3" policies="$4" missing_policy="$5" card="$6"
  local output rc
  TOTAL=$((TOTAL + 1))
  output="$(ACGH_GATE_TIMEOUT_SECONDS=1 bash "$runner" HEAD~1..HEAD --repo "$repo" --policies "$policies" --output "$card" 2>&1)"; rc=$?
  if [ "$rc" = 2 ] &&
     printf '%s\n' "$output" | grep -q "필수 정책 파일 없음: .*${missing_policy}" &&
     { [ ! -f "$card" ] || ! grep -q 'verdict: blocked' "$card"; }; then
    echo "PASS $name"; PASS=$((PASS + 1))
  else
    echo "FAIL $name (exit=$rc expected=2, expected missing ${missing_policy} preflight without blocked card)"
    printf '%s\n' "$output" | tail -16 | sed 's/^/  /'
    [ -f "$card" ] && tail -30 "$card" | sed 's/^/  card: /'
  fi
}

run_java_framework_case(){
  local name="$1" runner="$2" repo="$3" card="$4"
  local output rc
  TOTAL=$((TOTAL + 1))
  output="$(ACGH_GATE_TIMEOUT_SECONDS=3 bash "$runner" HEAD~1..HEAD --repo "$repo" --output "$card" 2>&1)"; rc=$?
  if [ "$rc" = 2 ] &&
     grep -q 'function_protected:app/AuthController.java::AuthController.resetPassword:base' "$card" &&
     printf '%s\n' "$output" | grep -q 'APPROVAL_REQUIRED'; then
    echo "PASS $name"; PASS=$((PASS + 1))
  else
    echo "FAIL $name (exit=$rc expected=2, missing Java framework protected evidence)"
    printf '%s\n' "$output" | tail -16 | sed 's/^/  /'
    [ -f "$card" ] && tail -30 "$card" | sed 's/^/  card: /'
  fi
}

# 실제 차단이 승인요구보다 강하게 조립되는지 + 대상 repo 정책 override.
repo="$WORK/frozen"; make_repo "$repo"
cat >"$repo/policies/sensitive-zones.yaml" <<'YAML'
policy_version: test
defaults:
  block_levels: [frozen]
  approve_levels: [protected]
  warn_levels: [watched]
  unlisted_level: free
zones:
  - path: "custom/crown/**"
    level: frozen
    reason: target repository crown jewel
    required_approval: [human-owner]
YAML
printf 'def settle():\n    return 0\n' >"$repo/custom/crown/ledger.py"
git -C "$repo" add . && git -C "$repo" commit -qm frozen
run_case target-policy-frozen 1 'BLOCKED' "$KIT/run.sh" "$repo" --policies "$repo/policies"

# 신규 능력은 카드가 pass여도 최종 승인요구여야 한다.
repo="$WORK/capability"; make_repo "$repo"
printf 'import requests\n\ndef fetch(url):\n    return requests.get(url)\n' >"$repo/app/service.py"
git -C "$repo" add . && git -C "$repo" commit -qm capability
run_case new-capability 2 'APPROVAL_REQUIRED' "$KIT/run.sh" "$repo"
run_capability_console_case capability-console-path-and-level "$KIT/run.sh" "$repo"

# 정상 exit 와 JSON 을 내더라도 stderr 혼입으로 파싱할 수 없으면 원문과 실패 사실을 고지한다.
parse_notice="$WORK/kit-capability-parse-notice"; cp -R "$KIT" "$parse_notice"
cat >"$parse_notice/gates/check-new-capabilities.py" <<'PY'
import json
import sys

print("diagnostic from capability gate", file=sys.stderr)
print(json.dumps({"verdict": "approval_required", "new_capabilities": []}))
raise SystemExit(2)
PY
run_capability_parse_notice_case capability-output-parse-notice "$parse_notice/run.sh" "$repo"

# shadow finding 은 판정을 바꾸지 않되 실제 정책 level 을 콘솔에 보존한다.
shadow_console="$WORK/kit-shadow-console"; cp -R "$KIT" "$shadow_console"
cat >"$shadow_console/gates/check-new-capabilities.py" <<'PY'
import json

print(json.dumps({
    "verdict": "pass",
    "new_capabilities": [],
    "warned_capabilities": [],
    "shadow_capabilities": [{
        "path": "app/service.py",
        "id": "shadow_exec",
        "level": "protected",
    }],
    "fail_closed": [],
    "errors": [],
}))
PY
run_shadow_capability_console_case capability-shadow-level "$shadow_console/run.sh" "$repo"

# 대상 repo 에 policies/ 가 없어도 킷 동봉 정책을 절대경로로 배선해야 한다.
repo="$WORK/language-policy-kit-default"; make_repo "$repo"
rm -rf "$repo/.git" "$repo/policies"
git -C "$repo" init -q
git -C "$repo" config user.email kit-test@example.invalid
git -C "$repo" config user.name kit-test
git -C "$repo" add app change-intent.yaml && git -C "$repo" commit -qm base-without-target-policies
printf 'class AccountService {}\n' >"$repo/app/AccountService.java"
git -C "$repo" add -A && git -C "$repo" commit -qm language-policy-kit-default
run_language_policy_case language-routing-kit-policy-default "$KIT/run.sh" "$repo" "$WORK/language-policy-card.yaml"
run_language_policy_negative_case language-routing-exact-layer-negative "$WORK/language-policy-card.yaml" "$WORK/language-policy-rigged-card.yaml"

# 레거시 override 정책 디렉터리에 language-routing 이 없으면 차단 카드가 아니라 분석 실패로 닫아야 한다.
repo="$WORK/language-policy-legacy-override"; make_repo "$repo"
legacy_policies="$repo/policies"
rm "$legacy_policies/language-routing.yaml"
printf '\n# harmless legacy override change\n' >>"$repo/app/service.py"
git -C "$repo" add app/service.py && git -C "$repo" commit -qm language-policy-legacy-override
run_missing_language_policy_preflight_case language-routing-legacy-override-preflight "$KIT/run.sh" "$repo" "$legacy_policies" "$WORK/language-policy-legacy-card.yaml"

# 언어 비의존 필수 정책은 분석실패 exit 2 + blocked 카드 미생성을 보장해야 한다.
for required_policy in sensitive-zones.yaml sensitive-capabilities.yaml approval-routing.yaml framework-annotations.yaml; do
  repo="$WORK/preflight-${required_policy%.yaml}"; make_repo "$repo"
  missing_policies="$repo/policies"
  rm "$missing_policies/$required_policy"
  printf '\n# harmless preflight change\n' >>"$repo/app/service.py"
  git -C "$repo" add app/service.py && git -C "$repo" commit -qm "missing-$required_policy"
  run_missing_required_policy_preflight_case "required-policy-${required_policy%.yaml}-preflight" "$KIT/run.sh" "$repo" "$missing_policies" "$required_policy" "$WORK/preflight-${required_policy%.yaml}-card.yaml"
done

# Java 카탈로그는 Java 변경에만 필수다. 순수 Python 변경은 정책을 지연 로드해 통과한다.
repo="$WORK/preflight-java-policy-python"; make_repo "$repo"
rm "$repo/policies/java-sensitive-capabilities.yaml"
printf '\n# harmless Python-only change\n' >>"$repo/app/service.py"
git -C "$repo" add app/service.py && git -C "$repo" commit -qm missing-java-policy-python-only
run_case java-policy-lazy-python-pass 0 'PASS' "$KIT/run.sh" "$repo" --policies "$repo/policies" --output "$WORK/preflight-java-python-card.yaml"

repo="$WORK/preflight-java-policy-java"; make_repo "$repo"
rm "$repo/policies/java-sensitive-capabilities.yaml"
printf 'class Changed {}\n' >"$repo/app/Changed.java"
git -C "$repo" add app/Changed.java && git -C "$repo" commit -qm missing-java-policy-java-change
run_missing_required_policy_preflight_case required-policy-java-sensitive-capabilities-preflight "$KIT/run.sh" "$repo" "$repo/policies" "java-sensitive-capabilities.yaml" "$WORK/preflight-java-card.yaml"

# Spring security annotation changes are wired through framework-annotations.yaml into the L1 function gate.
repo="$WORK/java-framework"; make_repo "$repo"
cat >"$repo/app/AuthController.java" <<'JAVA'
package app;

class AuthController {
  @PreAuthorize("hasRole('ADMIN')")
  public void resetPassword() {
    audit("base");
  }
}
JAVA
git -C "$repo" add app/AuthController.java && git -C "$repo" commit -qm java-framework-base
cat >"$repo/app/AuthController.java" <<'JAVA'
package app;

class AuthController {
  public void resetPassword() {
    audit("head");
  }
}
JAVA
git -C "$repo" add app/AuthController.java && git -C "$repo" commit -qm java-framework-head
run_java_framework_case java-framework-preauthorize-approval "$KIT/run.sh" "$repo" "$WORK/java-framework-card.yaml"

# Java 신규 위험 능력은 별도 java-sensitive-capabilities.yaml 로만 평가되고 2층에서 승인요구된다.
repo="$WORK/java-capability"; make_repo "$repo"
cat >"$repo/app/Job.java" <<'JAVA'
package app;

class Job {
  void run() {
    int value = 1;
  }
}
JAVA
git -C "$repo" add app/Job.java && git -C "$repo" commit -qm java-capability-base
cat >"$repo/app/Job.java" <<'JAVA'
package app;

class Job {
  void run(String cmd) throws Exception {
    Runtime.getRuntime().exec(cmd);
  }
}
JAVA
git -C "$repo" add app/Job.java && git -C "$repo" commit -qm java-capability-head
run_case java-capability-approval 2 'command_exec' "$KIT/run.sh" "$repo"

# 선언한 필수 변경 파일이 diff 에 없으면 부재 탐지가 승인요구하고 카드에 증거를 남긴다.
repo="$WORK/expected-missing"; make_repo "$repo"
python3 - "$repo/change-intent.yaml" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
p.write_text(p.read_text() + "  expected_paths: [app/required_patch.py]\n")
PY
git -C "$repo" add change-intent.yaml && git -C "$repo" commit -qm expected-declaration
printf '\n# changed a different file\n' >>"$repo/app/service.py"
git -C "$repo" add app/service.py && git -C "$repo" commit -qm expected-missing
run_expected_missing_case expected-path-missing-approval "$KIT/run.sh" "$repo" "$WORK/expected-missing-card.yaml"

# intent 미제공은 bash 3.2에서도 빈 배열 크래시 없이 정직한 차단 카드를 만들고 나머지 층을 실행한다.
repo="$WORK/no-intent"; make_repo "$repo"
rm "$repo/change-intent.yaml"
git -C "$repo" add -A && git -C "$repo" commit -qm no-intent-base
printf '\n# harmless change\n' >>"$repo/app/service.py"
git -C "$repo" add app/service.py && git -C "$repo" commit -qm no-intent-change
run_no_intent_case no-intent-bash32-approval "$KIT/run.sh" "$repo" "$WORK/no-intent-card.yaml"

# sink 의 직접 의존함수 수정은 3층에서 승인요구로 최종판정에 반영된다.
repo="$WORK/indirect"; make_repo "$repo"
cat >"$repo/app/service.py" <<'PY'
def check_permission(user):
    return bool(user)

def download_report(user):
    return check_permission(user)
PY
cat >"$repo/policies/sink-registry.yaml" <<'YAML'
policy_version: test
defaults:
  maturity: shadow
  hops: 1
sinks:
  - id: report_download
    function: app.service.download_report
    reason: report export boundary
    owner: security-reviewer
    maturity: enforcing
YAML
git -C "$repo" add . && git -C "$repo" commit -qm indirect-base
python3 - "$repo/app/service.py" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
p.write_text(p.read_text().replace("return bool(user)", "return user is not None"))
PY
git -C "$repo" add . && git -C "$repo" commit -qm indirect-change
run_case indirect-impact-approval 2 'indirect sink impact requires review' "$KIT/run.sh" "$repo" --policies "$repo/policies"

# 대상 repo 가 동명 퇴화 라우팅 정책을 심어도 킷 정책이 명시 배선되어 Java L3가 꺼지지 않는다.
repo="$WORK/java-indirect-routing"; make_repo "$repo"
cat >"$repo/app/Flow.java" <<'JAVA'
class Flow {
  void sink() { helper(); }
  void helper() { int value = 1; }
}
JAVA
cat >"$repo/policies/sink-registry.yaml" <<'YAML'
defaults: {maturity: enforcing, hops: 1}
sinks:
  - {id: java_sink, function: Flow.sink, reason: Java routing guard, owner: java-reviewer}
YAML
cat >"$repo/policies/language-routing.yaml" <<'YAML'
adapters: {}
YAML
git -C "$repo" add . && git -C "$repo" commit -qm java-indirect-base
python3 - "$repo/app/Flow.java" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
p.write_text(p.read_text().replace("value = 1", "value = 2"))
PY
git -C "$repo" add app/Flow.java && git -C "$repo" commit -qm java-indirect-change
java_route_kit="$WORK/kit-java-routing"; cp -R "$KIT" "$java_route_kit"
cp "$repo/policies/sink-registry.yaml" "$java_route_kit/policies/sink-registry.yaml"
run_case java-indirect-target-routing-cannot-disable 2 'java_sink' "$java_route_kit/run.sh" "$repo"

# --policies override 의 language-routing 과 sink-registry 를 같은 디렉터리에서 사용한다.
override="$WORK/java-indirect-override-policies"; cp -R "$KIT/policies" "$override"
cp "$repo/policies/sink-registry.yaml" "$override/sink-registry.yaml"
run_case java-indirect-routing-override 2 'java_sink' "$KIT/run.sh" "$repo" --policies "$override"

# --policies 대상에 sink-registry 가 없으면 조용히 통과하지 않고 fail-safe 한다.
rm "$repo/policies/sink-registry.yaml"
run_missing_required_policy_preflight_case missing-sink-registry-preflight "$KIT/run.sh" "$repo" "$repo/policies" "sink-registry.yaml" "$WORK/missing-sink-registry-card.yaml"

# 빠른 게이트의 파이프 EOF가 watcher timeout 잔여시간에 묶이지 않아야 한다.
repo="$WORK/pipe-latency"; make_repo "$repo"
printf '\n# harmless change\n' >>"$repo/app/service.py"
git -C "$repo" add . && git -C "$repo" commit -qm changed
run_pipe_latency_case pipe-capture-no-timeout-delay "$KIT/run.sh" "$repo"

# 사용성 안내: change-intent 스키마 오기와 감사카드 repo 내부 출력을 알려야 하며 판정은 바꾸지 않는다.
repo="$WORK/intent-top-level"; make_repo "$repo"
cat >"$repo/change-intent.yaml" <<'YAML'
requirement_id: KIT-TEST
allowed_paths: ["app/**"]
forbidden_paths: []
YAML
printf '\n# harmless change\n' >>"$repo/app/service.py"
git -C "$repo" add . && git -C "$repo" commit -qm top-level-intent
run_case intent-schema-top-level-warning 2 'allowed_paths 가 top-level' "$KIT/run.sh" "$repo" --output "$WORK/top-level-card.yaml"

repo="$WORK/intent-valid"; make_repo "$repo"
printf '\n# harmless change\n' >>"$repo/app/service.py"
git -C "$repo" add . && git -C "$repo" commit -qm valid-intent
run_absent_case intent-schema-valid-no-warning 'allowed_paths 가 top-level' "$KIT/run.sh" "$repo" --output "$WORK/valid-card.yaml"

repo="$WORK/output-warning"; make_repo "$repo"
printf '\n# harmless change\n' >>"$repo/app/service.py"
git -C "$repo" add . && git -C "$repo" commit -qm default-output
run_case default-output-warning 0 '감사카드가 대상 repo 안에 생성됨' "$KIT/run.sh" "$repo"

# 정책 규칙 삭제는 메타 게이트가 승인요구로 올린다.
repo="$WORK/policy"; make_repo "$repo"
python3 - "$repo/policies/sensitive-zones.yaml" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
text = p.read_text()
marker = text.find("zones:")
p.write_text(text[:marker] + "zones: []\n")
PY
git -C "$repo" add . && git -C "$repo" commit -qm loosen
run_case policy-loosening 2 'APPROVAL_REQUIRED' "$KIT/run.sh" "$repo"

# 게이트 삭제와 timeout은 정상 판정 exit로 위장되지 않아야 한다.
broken="$WORK/kit-missing"; cp -R "$KIT" "$broken"; rm "$broken/gates/check-new-capabilities.py"
repo="$WORK/missing"; make_repo "$repo"
printf 'def changed():\n    return 1\n' >>"$repo/app/service.py"
git -C "$repo" add . && git -C "$repo" commit -qm changed
run_capability_failure_card_case missing-gate-card-trace "$broken/run.sh" "$repo" '분석 실패: check-new-capabilities' "$WORK/missing-gate-card.yaml"

slow="$WORK/kit-timeout"; cp -R "$KIT" "$slow"
cat >"$slow/gates/check-new-capabilities.py" <<'PY'
import time
time.sleep(5)
PY
run_capability_failure_card_case gate-timeout-card-trace "$slow/run.sh" "$repo" 'timeout' "$WORK/timeout-card.yaml"

# evidence 게이트 예외로 카드가 YAML 이 아니어도 capabilities fail-closed 처리와 최종 판정은 유지한다.
invalid="$WORK/kit-invalid-card"; cp -R "$KIT" "$invalid"
cat >"$invalid/gates/generate-change-evidence.py" <<'PY'
raise RuntimeError("forced evidence failure")
PY
cat >"$invalid/gates/check-new-capabilities.py" <<'PY'
import json
import sys

print(json.dumps({
    "verdict": "approval_required",
    "new_capabilities": [],
    "warned_capabilities": [],
    "shadow_capabilities": [],
    "fail_closed": [{"path": "app/service.py", "reason": "forced capability failure"}],
    "errors": [],
}))
raise SystemExit(2)
PY
run_invalid_card_trace_case evidence-exception-capability-fail-closed "$invalid/run.sh" "$repo" "$WORK/invalid-card.yaml"

# 콜론이 든 예외 원문은 YAML ScannerError 를 유발해 try/except 가 실제로 필요하다.
invalid_scanner="$WORK/kit-invalid-card-scanner"; cp -R "$invalid" "$invalid_scanner"
cat >"$invalid_scanner/gates/generate-change-evidence.py" <<'PY'
raise RuntimeError("boom: bad value: x")
PY
run_invalid_card_trace_case evidence-scanner-error-capability-fail-closed "$invalid_scanner/run.sh" "$repo" "$WORK/invalid-scanner-card.yaml"

echo "Entrypoint summary: $PASS/$TOTAL PASS"
[ "$PASS" = "$TOTAL" ]
