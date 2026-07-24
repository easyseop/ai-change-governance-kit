# AI Change Governance Kit

> AI·개발자가 만든 코드 변경(diff)을 **결정적으로**(LLM·추정 없음) 검사해, **위험한 변경만 사람 승인으로** 올려주는 배포 킷.
> 개발 저장소를 통째로 clone 하지 않고 **게이트 + 정책 + manifest** 만으로 동작하는 자립형 스냅샷.
> 상태: MVP-0·1·1.5·2 + MVP-3(Java J0~J3 · Java L3 partial) 반영.

> 📘 **처음이신가요?** 더 친절한 단계별 도입·문제해결 가이드는 개발 저장소의 **`docs/kit-onboarding.md`** 를 보세요. 이 문서는 게이트·계약 레퍼런스입니다.

---

## 이 킷이 안전한 이유

- **여러분의 코드를 실행하지 않습니다.** 소스를 정적으로(구문·경로·정책만) 읽을 뿐 import·실행하지 않아 검사 자체가 부작용을 내지 않습니다.
- **판정에 LLM·추정 없음.** 같은 입력 → 항상 같은 결과.
- **자동 차단은 금전 직접영향(frozen) 하나뿐.** 나머지는 전부 "사람이 한 번 보라"(승인요구)가 상한입니다.

---

## 30초 시작

```bash
python3 -m pip install -r requirements.txt        # 1) 의존성 (최초 1회)
./selftest.sh --quick                             # 2) 킷 살아있나 확인
./run.sh main..feature --repo <대상repo> \
     --policies <정책dir> --output card.yaml       # 3) 변경 검사
echo $?    # 4) 종료코드로 판단 · card.yaml 로 근거
```

---

## 결과 읽는 법 ★가장 중요

게이트는 **네 가지 상태**로 판정하고, 종료코드는 그 상태를 마지막에 한 번 변환합니다.

| 상태 | 종료코드 | 뜻 | 무엇을 하나 |
|---|---:|---|---|
| **pass** | `0` | 정책 위반 **미탐지** (※"안전 인증"이 아니라 "못 찾음") | 통과. 아래 coverage("못 본 것") 감안. |
| **approval_required** | `2` | 사람이 봐야 함 (민감경로·신규능력·간접영향·의도이탈·정책완화) | 담당 리뷰어가 감사카드 보고 승인/반려. |
| **blocked** | `1` | frozen(금전 직접영향) 접촉 | 원칙적으로 막힘. 필요 시 정책 소유자 예외. |
| **분석 실패(fail-closed)** | `2` | 게이트 크래시·타임아웃·정책 파일 부재 = **검사를 못 함** | "통과" 착각 금지. 원인(도구·환경) 고쳐 재실행. 카드에 `tool_owner` 표시. |

분석 실패는 게이트 파일 부재·Python Traceback·타임아웃(`ACGH_GATE_TIMEOUT_SECONDS`로 조정)·계약 밖 종료코드를 포함하며, **정상 판정으로 흡수하지 않고** 최소 승인요구(exit 2)로 닫습니다.

### 🔴 정직한 한계 — exit code 2와 1이 뭉개질 수 있음

단순 CI는 흔히 "0이면 성공, 아니면 실패"로만 처리합니다. 그러면 이 킷의 핵심(**승인요구(2)** 는 차단이 아니라 *사람에게 올리는 것*)이 **차단(1)과 똑같이 "실패"로 뭉개집니다.**

- **exit code는 힌트, 감사카드의 `verdict`가 정본입니다.** CI에서 종료코드만 보지 말고 `--output` 카드의 `verdict`를 읽으세요.
- 근본 해결 = 결과 파일 정본화(`ADR-002 acgh-result`, 설계 확정·구현 대기). 그때까지 `manifest.yaml`의 `verdict_mapping`은 draft이며, 승인요구를 fail로 낮추면 lossy로 표시하고 카드를 증거로 첨부해야 합니다.
- **분석 실패를 "승인요구"로 약화하지 마세요** — 검사를 못 한 것과 승인이 필요한 것은 위험도가 다릅니다.

---

## 사용법

```bash
# 1) 변경 검사 (핵심)
./run.sh <base>..<head> --repo <대상repo> --output card.yaml
#    대상 repo 가 자체 정책을 운영하면:
./run.sh <base>..<head> --repo <대상repo> --policies <대상repo>/policies --output card.yaml

# 2) 온보딩 씨딩 (민감경로·함수 후보 draft — 자동적용 아님, 사람 검토)
./bootstrap.sh zones     <repo> --rules <rules.yaml>
./bootstrap.sh functions <repo>

# 3) 자체검증 (게이트 무결·시험 살아있음 — 배포지에서 실증)
./selftest.sh            # 러너 + 뮤테이션
./selftest.sh --quick    # 러너만

# 4) 개발본과 재동기화 (MVP 완료마다)
./sync-from-dev.sh
```

- **`<base>..<head>`** = 검사할 git 범위(base ≠ head). 예: `main..feature`, `HEAD~1..HEAD`.
- **`--output <경로>`** = 감사카드 저장 위치. **지정 권장** — 안 하면 대상 repo 안에 `change-evidence.yaml`이 생겨 `git add -A` 시 다음 diff를 오염시킵니다(러너가 감지 시 `⚠` 안내). 대상 repo `.gitignore`에 등록해도 됩니다.

---

## 정책 세팅

`--policies` 디렉터리는 아래 **7개 파일을 모두** 포함해야 합니다:

```
sensitive-zones.yaml               민감 경로(frozen/protected/watched)
sensitive-capabilities.yaml        Python 위험 능력 카탈로그
java-sensitive-capabilities.yaml   Java 위험 능력 카탈로그
approval-routing.yaml              영역 → 리뷰어 매핑
sink-registry.yaml                 간접영향 추적 sink
language-routing.yaml              확장자 → 분석기
framework-annotations.yaml         Spring/Jakarta 어노테이션 → 민감도
```
킷을 업그레이드할 때는 킷 동봉 `policies/language-routing.yaml`을 override 디렉터리에 복사한 뒤 실행하세요.

### `change-intent.yaml` (대상 repo가 제공)

변경 주체가 diff 전에 "무엇을 어디까지 바꿀지" 선언합니다. **없으면 의도층이 fail-closed(차단)** 됩니다(의도 선언 강제). 킷은 `policies/change-intent.template.yaml`·`change-intent.example.yaml`을 동봉합니다.

```yaml
change_intent:                    # ✓ 반드시 이 키 아래 중첩!
  allowed_paths:   ["src/**"]     # 밖 변경 → 의도이탈(승인요구)
  forbidden_paths: ["**/auth/**"] # 안 변경 → 차단
  expected_paths:  ["src/vendor/patch.py"]   # 반드시 바뀌어야 할 파일
```

- ⚠️ **`allowed_paths`를 top-level에 두면** 빈 선언으로 읽혀 **모든 변경이 승인요구**가 됩니다(흔한 함정). 반드시 `change_intent:` 아래로.
- **`expected_paths`**(선택): 선언 파일이 diff에 **없으면** 패치 유실 가능성으로 승인요구(카드 `intent_check.missing_expected`에 기록). 정확한 확인엔 **리터럴 경로 권장**(글롭은 하위 파일 하나만 바뀌어도 충족되는 거친 보증).
- **glob 문법**: `**`(다단계)·`*`(단일단계)·구분자 `/`·repo-root 상대. 킷 내장 매처로 고정돼 라이브러리별 차이로 판정이 흔들리지 않습니다.

---

## 이 판정이 "본 것"과 "못 본 것" (coverage)

pass가 "안전 보증"이 아닌 이유. 감사카드 `coverage_statement`에 매번 명시됩니다.

- **본 것** — 실제 실행된 게이트(경로·의도·함수레벨·능력·간접영향·정책 중 돌아간 것).
- **못 본 것** — 런타임 실행경로·미등록 민감 로직·cross-commit 누적·비-Python 정밀분석·완전 동적 난독.

정책을 덜 채우면 "본 것"이 줄어듭니다. 씨딩을 성실히 하는 만큼 커버리지가 늘어납니다.

---

## 게이트 전량 (21종 — 누락 없음)

`run.sh`가 아래 **판정 게이트**를 빠짐없이 조립합니다.

| 게이트 | 층 | 역할 | 판정 |
|---|---|---|---|
| `check-change-intent` | L1 | 의도 이탈(allowed/forbidden)·필수 변경 파일 부재(expected_paths) | 0/1/2 |
| `check-sensitive-zones` | L1 | 민감경로(frozen/protected/watched) | 0/1/2 |
| `check-function-gov-level` | L1 | Python `@gov` 및 Java `@Gov`/Spring 어노테이션 함수 레벨 | 0/1/2 |
| `check-new-capabilities` | L2 | 신규 위험능력(외부호출·암복호·실행) | 0/2 |
| `check-indirect-impact` | L3 | 등록 sink의 N홉 의존함수 변경 | 0/2 |
| `check-policy-change` | meta | 정책 자기무력화·집행우회 | 0/2 |
| `generate-change-evidence` | 집계 | L1 3축 조립 + 감사카드 생성 | 0/1/2 |

**추출 의존 게이트(9종)** — 판정 게이트가 내부 import(같은 폴더 필수):
`extract-python-inventory` · `map-diff-to-functions` · `classify-python-function-changes` · `extract-gov-annotations` · `extract-python-capabilities` · `extract-java-capabilities` · `extract-java-callgraph` · `extract-sinks` · `extract-callgraph`

**오프라인 도구(2종)** — `bootstrap.sh`용: `bootstrap-sensitive-zones` · `bootstrap-sensitive-functions`

**언어 라우팅/Java 인벤토리(3종)**: `language-router` · `check-tree-sitter-languages` · `extract-java-inventory`

→ 판정 7 + 추출 9 + 도구 2 + 언어 3 = **21종**. `manifest.yaml gates:`에 명시. Tree-sitter 스모크 재현엔 `pip install -r requirements.txt` 선행.

> **왜 한 폴더에 다 있어야 하나**: 게이트들은 서로를 같은 디렉터리에서 import 합니다(`Path(__file__).parent`). 하나라도 빠지면 import가 깨지므로 `sync-from-dev.sh`가 dev 게이트 수 == kit 게이트 수를 검증합니다.

---

## 알려진 한계 (정직)

1. **exit code 2단계 손실** — 단순 CI가 승인요구(2)와 차단(1)을 뭉갤 수 있음. 근본 해결 = ADR-002 결과파일 정본화(구현 대기). 그때까지 카드 `verdict`가 정본.
2. **감사카드 verdict ≠ 최종 verdict 일 수 있음** — 카드(`generate-change-evidence`)는 L1 3축만 반영. 킷 최종판정은 능력·간접영향·정책 게이트까지 조립하므로, **카드가 pass여도 최종이 승인요구**일 수 있습니다(능력 탐지 시). 최종판정은 `run.sh` 콘솔 출력·종료코드 기준. (ADR-002가 카드를 통합하면 해소)
3. **Java L3는 partial** — 보수적 전방폐쇄에서 sink가 외부 호출을 하나라도 만나면 지연 dispatch가 승인요구로 승격돼 **소음**이 생길 수 있습니다(O-14). 카드의 `inferred: true`는 실제 호출 관측이 아니라 **보수 추정 홉 포함**을 뜻하고, `fail_closed`의 `dead_ends`에는 무관한 이름이 섞일 수 있습니다(O-22). Java L3를 "안전 지원"으로 과장하지 않습니다.
4. **언어별 깊이** — Python 완전 / Java(함수·능력·간접영향 partial) / JS·TS는 경로층만(심층 후속) / 그 외는 카드에 "심층분석 미지원" 명시.
5. **spine 계약 미검증** — `manifest.yaml`의 apiVersion·필드명은 TO-BE 규격 기준 초안. spine 오너 확인 필요.

---

## 킷은 생성물 — 직접 편집 금지

`gates/`·`policies/`·`templates/`·`tests/`는 개발 저장소에서 복사된 스냅샷입니다. **게이트 로직**을 고칠 일이 있으면 개발 저장소 원본(`.harness/gates`·`policies`)을 고치고 `./sync-from-dev.sh`로 재생성하세요. **정책(규칙)** 만 `--policies` 디렉터리에서 조직값으로 채웁니다.

---

## spine 연동

이 킷은 spine의 **verify 단계** kit입니다. 러너 계약 = `bash · 위치인자 · cwd=kit · exit code` — `run.sh`와 일치해 spine 뼈대를 그대로 씁니다.

## 반영된 MVP

- **MVP-0** 경로·의도 (change-intent·sensitive-zones·감사카드)
- **MVP-1** 함수·능력 (@gov·AST 인벤토리·능력 카탈로그)
- **MVP-1.5** 신뢰·도입 보강 (정책완화 게이트·감사카드 정직화·성숙도 shadow·씨딩·뮤테이션·광역의도)
- **MVP-2** 영향추적 (sink 등록·정적 콜그래프·N홉 역도달성·coverage 정직성) + 패치 생존성(`expected_paths`)
- **MVP-3 J0~J3** Java 언어 라우팅 seam·인벤토리·`@Gov`+Spring 어노테이션·신규 능력
- **MVP-3 Java L3 (partial)** Java sink·콜그래프 기반 간접영향 + 보수적 coverage 노출
