#!/usr/bin/env bash
# =====================================================================
# selftest.sh — 킷 자체검증 (배포지에서 "게이트가 살아있나" 실증)
# =====================================================================
# 번들된 테스트 스위트 + 뮤테이션 점검을 돌려, 이 킷의 게이트가 제대로
# 판정하고(회귀 없음) 시험이 죽지 않았음을 배포 환경에서 직접 확인한다.
# ★게이트가 서로를 co-located import 하므로, 여기서 통과 = 게이트 전량이 온전히
#   실린 것의 실증(누락 시 import 깨져 FAIL).
#
# 사용:  ./selftest.sh          (전체)
#        ./selftest.sh --quick  (러너 스위트만, 뮤테이션 생략)
# =====================================================================
set -uo pipefail
KIT="$(cd "$(dirname "$0")" && pwd)"
QUICK=0; [ "${1:-}" = "--quick" ] && QUICK=1

# 번들 테스트는 개발 저장소 레이아웃(.harness/gates)을 기대하므로, 킷 안에서
# 그 경로를 게이트로 심볼릭 연결해 테스트 하네스가 킷 게이트를 보게 한다.
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/.harness"
ln -s "$KIT/gates" "$WORK/.harness/gates"
ln -s "$KIT/policies" "$WORK/policies"
ln -s "$KIT/templates" "$WORK/templates"
cp -r "$KIT/tests" "$WORK/tests"

# name-status 파일 입력 케이스도 광역 의도 판정에서 저장소 기준 디렉터리를
# 조회한다. 격리 루트를 git 저장소로 만들어 변경 파일 하나만을 저장소 전체로
# 오인하는 fallback(예: app/** = 100%)을 피하고 실제 실행 환경을 재현한다.
git -C "$WORK" init -q
git -C "$WORK" config user.email kit-selftest@example.invalid
git -C "$WORK" config user.name kit-selftest
git -C "$WORK" add .
git -C "$WORK" commit -qm selftest-baseline

echo "▶ 킷 자체검증 (게이트 전량 co-located 무결성 포함)"
cd "$WORK"
rc=0
echo "── 러너 스위트 (tests/run-tests.sh) ──"
bash tests/run-tests.sh || rc=1
echo "── 킷 진입점 적대검증 (tests/run-entrypoint-tests.sh) ──"
bash "$KIT/tests/run-entrypoint-tests.sh" || rc=1
if [ "$QUICK" = 0 ] && [ -f tests/mutation-check.sh ]; then
  echo "── 뮤테이션 점검 (tests/mutation-check.sh) ──"
  bash tests/mutation-check.sh || rc=1
fi
if [ "$rc" = 0 ]; then echo "✓ selftest PASS — 킷 게이트 무결·시험 살아있음"; else echo "✗ selftest FAIL"; fi
exit $rc
