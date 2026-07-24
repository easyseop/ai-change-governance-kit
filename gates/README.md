# .harness/gates/ — 게이트 (Codex 구현 영역)

> 이 폴더는 **Codex 가 채운다.** Claude 는 비워둔다(상호견제).
> 구현 명세·수용기준은 `../../TASKS.md`, 역할은 `../../AGENTS.md`.

MVP-0 에서 만들 게이트:

| 파일 | 태스크 | 한 줄 |
|---|---|---|
| `check-change-intent.py` | TASK-001 | diff 가 change-intent 의도 범위 밖/forbidden 을 건드렸나 |
| `check-sensitive-zones.py` | TASK-002 | diff 가 민감 경로(frozen/protected/watched)를 건드렸나 |
| `generate-change-evidence.py` | TASK-003 | 위 결과+routing 으로 변경 감사카드 생성 |
| `extract-python-inventory.py` | TASK-005 | Python 함수/클래스 인벤토리 추출 |
| `extract-java-inventory.py` | TASK-030 | Java 클래스/메서드/생성자 인벤토리 추출 |
| `map-diff-to-functions.py` | TASK-006 | git diff 헝크를 after 버전 함수/클래스에 매핑 |
| `classify-python-function-changes.py` | TASK-007 | before/after 함수 변경을 added/modified/deleted 로 분류 |
| `extract-gov-annotations.py` | TASK-008 | Python `@gov`/`__gov__` 주석을 추출·검증 |
| `check-function-gov-level.py` | TASK-009/TASK-031 | 변경 함수와 base/head Python `@gov`·Java `@Gov`/framework effective level 을 판정 |
| `bootstrap-sensitive-zones.py` | TASK-014 | 경로 규칙과 CODEOWNERS 로 sensitive-zones 초안 후보를 생성 |
| `extract-sinks.py` | TASK-022 | MVP-2 간접영향 추적 대상 sink 목록을 등록·검증 |
| `extract-callgraph.py` | TASK-023 | repo 내 Python 함수 호출 엣지와 미해소 호출 coverage 를 산출 |
| `language-router.py` | TASK-029 | 변경 파일을 확장자별 deep-analysis 어댑터로 분배하고 미지원 coverage 를 노출 |
| `check-tree-sitter-languages.py` | TASK-029 | pinned tree-sitter Java/JS/TS 문법 로드·파싱 스모크 테스트 |

종료코드 약속: **0 통과 / 1 차단 / 2 승인필요**. 결정적(추정·LLM 금지).
