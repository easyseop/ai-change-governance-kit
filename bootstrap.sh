#!/usr/bin/env bash
# =====================================================================
# bootstrap.sh — 온보딩 씨딩 도구 (spine hook 아님 · per-change 게이트 아님)
# =====================================================================
# 새 조직/저장소 도입 시 민감경로·민감함수 후보를 자동 스캔해 draft 를 낸다.
# ★출력은 draft_only — 자동 적용 아님. 사람이 검토 후 policies/ 에 반영한다.
#
# 사용:
#   ./bootstrap.sh zones     <대상repo> --rules <rules.yaml> [--codeowners <path>]
#   ./bootstrap.sh functions <대상repo> [--tables <tables.yaml>]
# =====================================================================
set -uo pipefail
KIT="$(cd "$(dirname "$0")" && pwd)"
G="$KIT/gates"
CAPS="$KIT/policies/sensitive-capabilities.yaml"

MODE="${1:?사용: ./bootstrap.sh zones|functions <repo> ...}"; shift
case "$MODE" in
  zones)     exec python3 "$G/bootstrap-sensitive-zones.py" "$@" ;;
  functions) exec python3 "$G/bootstrap-sensitive-functions.py" "$1" "$CAPS" "${@:2}" ;;
  *) echo "알 수 없는 모드: $MODE (zones|functions)"; exit 64 ;;
esac
