# AI Change Governance Kit

코드 변경이 **약속한 범위 안에 있는지**, **민감한 곳을 건드렸는지** 자동으로 확인하는 도구입니다.
위험 신호가 있으면 배포를 조용히 통과시키지 않고 사람 검토로 보냅니다.

- 대상 코드를 실행하거나 import하지 않습니다. 파일 경로와 코드 구조를 정적으로 읽습니다.
- 판정에 LLM을 사용하지 않습니다. 같은 코드와 정책을 넣으면 같은 결과가 나옵니다.
- 검사 결과와 근거를 YAML 감사카드로 남깁니다.

> 이 도구의 `pass`는 “안전이 보장됐다”는 뜻이 아닙니다.
> 현재 정책과 정적분석이 **위험을 찾지 못했다**는 뜻입니다.

## 무엇을 확인하나요?

| 확인 항목 | 쉬운 설명 |
|---|---|
| 변경 의도 | 미리 적어 둔 변경 범위 밖의 파일을 건드렸는지 확인합니다. |
| 민감 경로 | 인증, 결제, 인프라처럼 조직이 민감하다고 정한 경로를 확인합니다. |
| 민감 함수 | Python `@gov`, Java `@Gov`와 Spring 보안 어노테이션이 붙은 함수를 확인합니다. |
| 새로운 위험 기능 | 외부 호출, 암·복호화, 동적 실행 같은 기능이 새로 들어왔는지 확인합니다. |
| 간접 영향 | 바뀐 함수가 중요 함수에 연결되는지 정적 호출 관계를 따라가 봅니다. |
| 정책 변경 | 검사 규칙을 약하게 만들거나 게이트를 우회하는 변경을 확인합니다. |

## 준비물

- Git
- Bash
- Python 3.10 이상
- macOS 또는 Linux
- Windows에서는 WSL 사용 권장

## 5분 시작

### 1. 설치

```bash
git clone https://github.com/easyseop/ai-change-governance-kit.git
cd ai-change-governance-kit

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

가상환경을 쓰면 이 킷의 Tree-sitter 버전이 다른 Python 프로젝트와 충돌하는 일을 줄일 수 있습니다.

### 2. 킷 자체 점검

```bash
./selftest.sh --quick
```

마지막에 `selftest PASS`가 나오면 설치가 끝난 것입니다.

### 3. 변경 의도 파일 만들기

검사할 저장소의 루트에 `change-intent.yaml`을 만듭니다.

```bash
TARGET_REPO=/absolute/path/to/your-repo
cp policies/change-intent.template.yaml "$TARGET_REPO/change-intent.yaml"
```

복사한 파일을 열어 이번 변경에서 허용할 경로를 적습니다.

```yaml
change_intent:
  requirement_id: REQ-2026-001
  purpose: 관리자 검색 조건 추가
  author: developer

  allowed_paths:
    - "src/admin/**"
    - "tests/admin/**"

  forbidden_paths:
    - "src/auth/**"
    - "infra/**"

  # 반드시 diff에 나타나야 하는 파일이 없다면 빈 목록으로 둡니다.
  expected_paths: []
```

- `allowed_paths`: 이번 작업에서 바꿔도 되는 경로입니다.
- `forbidden_paths`: 이번 작업에서 절대 바꾸면 안 되는 경로입니다.
- `expected_paths`: 반드시 실제 diff에 들어 있어야 하는 파일입니다. 선택 항목입니다.

`change_intent:` 아래에 들여쓰기해야 합니다. `allowed_paths`를 파일 맨 위에 쓰면 올바르게 읽지 못합니다.

### 4. 변경 검사

최근 한 커밋을 검사하는 예시입니다.

```bash
./run.sh HEAD~1..HEAD \
  --repo "$TARGET_REPO" \
  --output /tmp/change-evidence.yaml

echo $?
```

현재 브랜치 전체를 `main`과 비교하려면 범위를 `main..HEAD`로 바꾸면 됩니다.

킷에 들어 있는 기본 정책을 사용할 때는 `--policies`를 생략할 수 있습니다.

## 결과 읽는 법

`run.sh` 마지막 부분에 다음과 같은 요약이 나옵니다.

```text
게이트 판정 : 카드3축=0 · 능력=2 · 간접영향=0 · 정책=0
최종 판정   : 🟠 APPROVAL_REQUIRED (exit 2)
감사카드    : /tmp/change-evidence.yaml
```

| 최종 판정 | 종료코드 | 해야 할 일 |
|---|---:|---|
| `PASS` | `0` | 정책이 찾은 위험이 없습니다. 감사카드의 coverage도 확인합니다. |
| `BLOCKED` | `1` | 변경을 멈춥니다. `forbidden` 또는 `frozen` 항목과 분석 오류를 확인합니다. |
| `APPROVAL_REQUIRED` | `2` | 감사카드와 콘솔 근거를 담당 리뷰어에게 보냅니다. |
| 분석 실패 | 보통 `2` | 도구·정책·환경 문제를 고친 뒤 다시 실행합니다. |

### 현재 버전에서 가장 중요한 점

**최종 판정은 `run.sh`의 `최종 판정` 줄과 프로세스 종료코드입니다.**

`change-evidence.yaml`의 `change_evidence.verdict`는 변경 의도·민감 경로·민감 함수만 반영합니다.
새로운 위험 기능, 간접 영향, 정책 변경 때문에 최종 결과가 더 엄격해질 수 있습니다.
따라서 카드의 `verdict`만 읽고 배포 여부를 결정하면 안 됩니다.

종료코드 `2`는 “사람 승인 필요”와 “분석 실패”가 함께 사용합니다.
콘솔에 `분석 실패`가 표시되면 승인 절차로 넘기지 말고 먼저 도구나 환경 문제를 해결합니다.

## 감사카드에서 볼 곳

```yaml
change_evidence:
  requirement_id: REQ-2026-001
  verdict: approval_required
  changed_files: []
  changed_functions: []
  reviewer_required: []
  coverage_statement:
    checked: []
    not_checked: []
```

- `changed_files`: 어떤 파일이 바뀌었고 민감도는 무엇인지 보여줍니다.
- `changed_functions`: 영향을 받은 민감 함수를 보여줍니다.
- `reviewer_required`: 검토해야 할 담당자를 보여줍니다.
- `coverage_statement.checked`: 이번 실행에서 확인한 항목입니다.
- `coverage_statement.not_checked`: 지원하지 않거나 확인하지 못한 항목입니다.
- `policy_sha`: 판정할 때 사용한 정책 버전을 재현할 수 있게 해 줍니다.

감사카드는 대상 저장소 밖에 저장하는 것이 안전합니다.
대상 저장소 안에 저장하면 다음 `git diff`에 섞일 수 있습니다.

## 조직 정책 사용하기

처음에는 킷에 들어 있는 `policies/`를 그대로 사용할 수 있습니다.
실제 조직에 도입할 때는 정책 폴더를 복사한 뒤 담당자와 함께 수정합니다.

```bash
cp -R policies /path/to/my-governance-policies

./run.sh main..HEAD \
  --repo "$TARGET_REPO" \
  --policies /path/to/my-governance-policies \
  --output /tmp/change-evidence.yaml
```

사용자 정책 폴더에는 다음 7개 파일이 모두 있어야 합니다.

| 파일 | 정하는 내용 |
|---|---|
| `sensitive-zones.yaml` | 차단·승인·관찰할 경로 |
| `sensitive-capabilities.yaml` | Python의 위험 기능 |
| `java-sensitive-capabilities.yaml` | Java의 위험 기능 |
| `approval-routing.yaml` | 위험 영역별 리뷰 담당자 |
| `sink-registry.yaml` | 간접 영향을 추적할 중요 함수 |
| `language-routing.yaml` | 파일 확장자별 분석 방법 |
| `framework-annotations.yaml` | Spring·Jakarta 어노테이션의 민감도 |

`frozen`, 리뷰 담당자, 조직의 중요 경로처럼 실제 운영 판단이 필요한 값은 정책 담당자의 검토를 받아야 합니다.

## 경로 작성 방법

- `**`: 여러 단계의 하위 폴더를 포함합니다. 예: `src/**`
- `*`: 폴더 한 단계만 포함합니다. 예: `services/*/config.py`
- 모든 경로는 검사 대상 저장소의 루트를 기준으로 적습니다.
- `*`로 시작하는 값은 YAML이 다르게 해석할 수 있으므로 따옴표로 감싸는 것이 안전합니다.

예:

```yaml
allowed_paths:
  - "src/**"
  - "tests/*/test_*.py"
forbidden_paths:
  - "**/auth/**"
```

## 처음 도입할 때 후보 찾기

`bootstrap.sh`는 민감 경로나 함수를 자동으로 확정하지 않습니다.
검토할 **후보 목록**만 만들며, 사람이 확인한 뒤 정책에 반영해야 합니다.

```bash
./bootstrap.sh functions "$TARGET_REPO"
./bootstrap.sh zones "$TARGET_REPO" --rules /path/to/rules.yaml
```

## 자주 겪는 문제

### 모든 변경이 승인 필요로 나옵니다

다음을 확인합니다.

1. 대상 저장소 루트에 `change-intent.yaml`이 있는지
2. `allowed_paths`가 `change_intent:` 아래에 들여쓰기됐는지
3. 실제 변경 경로가 `allowed_paths`와 맞는지

변경 의도 파일이 없으면 현재 버전은 안전하게 `APPROVAL_REQUIRED`로 처리합니다.

### `selftest`에서 Tree-sitter 오류가 납니다

Python 3.10 이상인지 확인하고, 기존 환경 대신 새 가상환경에서 다시 설치합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
./selftest.sh --quick
```

### 종료코드가 2인데 승인해야 할지 모르겠습니다

콘솔에서 `분석 실패` 문구를 먼저 찾습니다.

- 문구가 없고 위험 근거가 표시됨: 사람 검토가 필요합니다.
- 문구가 있음: 도구·정책·환경 문제를 고친 뒤 다시 검사합니다.

### 감사카드가 다음 변경에 포함됩니다

`--output`을 대상 저장소 밖의 절대경로로 지정하거나, 카드 경로를 대상 저장소의 `.gitignore`에 추가합니다.

## 지원 범위와 한계

| 언어 | 지원 깊이 |
|---|---|
| Python | 경로, 함수, 위험 기능, 간접 영향 |
| Java | 경로, 함수, 위험 기능, 간접 영향 일부 |
| JavaScript·TypeScript | 경로 중심. 정밀분석은 아직 지원하지 않습니다. |
| 그 외 언어 | 경로 중심. 미지원 항목은 coverage에 표시합니다. |

- 런타임에서 실제로 어떤 코드가 실행되는지는 확인하지 않습니다.
- 등록하지 않은 민감 로직은 찾지 못할 수 있습니다.
- Java 간접 영향 분석은 보수적으로 판단하므로 불필요한 승인 요청이 생길 수 있습니다.
- 함수 안의 주석이나 공백만 바뀌어도 함수 변경으로 볼 수 있습니다.
- `pass`는 보안 인증이 아닙니다. `coverage_statement`에서 확인하지 못한 항목을 함께 봐야 합니다.

<details>
<summary>포함된 게이트 21종 보기</summary>

### 최종 판정에 참여하는 게이트

- `check-change-intent`
- `check-sensitive-zones`
- `check-function-gov-level`
- `check-new-capabilities`
- `check-indirect-impact`
- `check-policy-change`
- `generate-change-evidence`

### 내부 분석 게이트

- `extract-python-inventory`
- `map-diff-to-functions`
- `classify-python-function-changes`
- `extract-gov-annotations`
- `extract-python-capabilities`
- `extract-java-capabilities`
- `extract-java-callgraph`
- `extract-sinks`
- `extract-callgraph`

### 초기 설정 도구

- `bootstrap-sensitive-zones`
- `bootstrap-sensitive-functions`

### 언어 확인 도구

- `language-router`
- `check-tree-sitter-languages`
- `extract-java-inventory`

</details>

## 자체검증

```bash
./selftest.sh --quick  # 기본 회귀 테스트
./selftest.sh          # 기본 회귀 테스트 + 변이 테스트
```

게이트 파일이 빠졌거나 서로 import하지 못하면 자체검증이 실패합니다.

## Spine 연동

이 킷은 Spine의 `verify` 단계에서 `run.sh`를 실행하도록 설계됐습니다.
연동 계약과 게이트 목록은 `manifest.yaml`에 있습니다.
