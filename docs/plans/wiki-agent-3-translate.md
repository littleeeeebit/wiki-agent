# 3단계 — `translate` 독립

전체 설계와 단계의 관계는 [개요](wiki-agent-0-overview.md)에 있다.

목표. `translate` 는 계약 하나로만 불린다 — 문장 목록, 방향, 마감을 받아 같은 길이의 문장
목록을 돌려준다. 예산은 둘이다. 마감은 부르는 쪽이 넘기고, 월 비용 한도는 `translate` 가
스스로 센다. 한 예산을 여러 기능이 나눠 쓰던 구조(#18·#19)가 여기서 끝난다.

## 공개 진입점

`tool/translate/__init__.py` 의 `__all__` 이 공개 진입점이다.

| 이름 | 무엇 | 부르는 쪽 |
| --- | --- | --- |
| `translate(texts, direction, deadline)` | 번역. 실패하면 원문을 돌려준다 | `inject`, `session_state`, `chat`, `mirror` |
| `usage()` | 이번 달 사용액과 한도 | `python tool/translate --usage`, 6단계 화면 |
| `glossary()` | 용어집. 번역하지 않는 한국어 용어 | `english_progress` |
| `KO_EN`, `EN_KO` | 방향 | 위 전부 |

- `direction` 과 `deadline` 에서 기본값을 뺀다. 마감의 기본값 60초(`TIMEOUT`)도 지운다.
  기본값이 있으면 마감을 넘기지 않은 호출자가 남의 마감으로 돈다 — #19 가 그 모양이었다.
  60초는 화면 쪽(`chat` 의 `/api/translate`, `mirror`)이 자기 상수로 넘긴다
- `ko_to_en`·`en_to_ko` 는 지운다. `inject.rendering` 은 `translate([prompt], KO_EN, deadline)[0]`
  을 부른다. 같은 일을 하는 입구가 셋이면 공개 진입점이 하나가 아니다
- `--check` 와 그 도구들(`protect`, `by_kind`, `baseline` 등)은 패키지 안에 그대로 둔다.
  테스트(`test_*.py`)는 내부를 들여다봐도 된다

### 검사

`lint.pipeline_surface` 가 본다. 파이프라인의 `__init__.py` 에 `__all__` 이 있으면, `tool/`
루트 모듈(테스트 제외)이 그 파이프라인에서 쓰는 이름이 전부 `__all__` 안에 있어야 한다.

- `import translate as T` 뒤의 `T.x`, `from translate import x`, `from tool import translate`
  를 다 본다
- 하위 모듈 import(`import translate.x`, `from translate.x import y`)는 곧바로 발견이다
- 발견의 종류는 `공개 진입점` 이다

`__all__` 이 없는 파이프라인(`wiki`, `agent`, `workspace`)은 아직 보지 않는다. 4·5단계가
`__all__` 을 쓰는 순간 같은 검사가 붙는다.

| `test_lint.py` 에 심는 것 | 기대 |
| --- | --- |
| 루트 모듈이 `import translate` 뒤 `translate._ask(...)` | 빨강 |
| 루트 모듈이 `import translate as T` 뒤 `T.protect` | 빨강 |
| 루트 모듈이 `from translate import protect` | 빨강 |
| 루트 모듈이 `from translate.x import y` | 빨강 |
| 루트 모듈이 `translate.translate`, `from translate import KO_EN`, 테스트 파일이 `translate._ask` | 초록 |

## 월 비용 한도

| 무엇 | 어떻게 |
| --- | --- |
| 한도 | `.env` 의 `TRANSLATE_MONTHLY_USD`, 없으면 같은 이름의 환경 변수, 둘 다 없으면 $5. 읽는 순서는 `GEMINI_API_KEY` 와 같다 |
| `0` | 새 요청을 보내지 않는다. 캐시는 그대로 답한다 |
| 읽을 수 없는 값 | `0` 으로 본다. 한도를 못 읽었을 때 돈 쪽으로 안전하게. 숫자가 아닌 값, 음수, `nan`·`inf`, 그리고 빈 값 — 키처럼 줄이 있으면 그 줄이 이긴다 |
| 요금 | 응답의 `usageMetadata` — 입력은 `promptTokenCount`, 출력은 `candidatesTokenCount` 와 `thoughtsTokenCount` 의 합 — 에 요금표를 곱한다. `gemini-3.1-flash-lite` 입력 $0.25, 출력 $1.50 (100만 토큰당, 2026-09-24 ai.google.dev 요금표) |
| 먼저 기록 | 요청을 보내기 전에 선차감을 쌓는다. 보낸 요청 본문의 바이트 수를 입력·출력 토큰 수로 본 값이라 실제보다 많다. 쓰지 못하면 보내지 않는다. 응답 뒤에 기록하면 잠김으로 쓰기가 실패할 때 비용이 장부에서 빠지기 때문이다(리뷰 라운드 1) |
| 응답이 오면 | 실제 요금과 선차감의 차이로 정산한다. 정산 쓰기가 실패하면 더 큰 선차감이 남는다 |
| 응답 없이 시간이 다 된 요청 | 서버는 끝까지 처리하고 청구했을 수 있다. 선차감을 그대로 둔다 |
| 연결 실패, HTTP 오류 | 청구되지 않으므로 선차감을 되돌린다 |
| 저장 | 캐시와 같은 sqlite 파일의 `spend(month, usd)` 표. 달은 UTC `YYYY-MM` |
| 판정 | 요청을 보내기 전에 이번 달 사용액이 한도 이상이면 보내지 않고 원문을 돌려준다 |
| 캐시를 못 열거나 사용액 조회가 실패할 때 | 셀 수 없으므로 요청하지 않는다. 이미 찾은 캐시 적중은 그대로 돌려준다 |

요금표는 `MODEL` 옆의 상수다. 모델을 바꾸면 같이 바꾼다.

한계. 두 프로세스가 한도 바로 아래에서 동시에 요청하면 둘 다 나간다. 넘치는 양은 요청
하나 크기다.

## 캐시

바꾸지 않는다. 이미 파이프라인 안에 있고, 키에 모델·프롬프트·용어집 버전이 든다. 한도를
넘어도 캐시는 답한다 — 이미 낸 돈이다.

## 넣지 않은 것

| 무엇 | 언제 |
| --- | --- |
| 화면의 사용량 표시 | 6단계. 여기서는 `usage()` 와 `python tool/translate --usage` 까지 |
| 번역 켬·끔 스위치 | 6단계. 메인의 스위치다 |

## 검증

| 확인 | 결과 |
| --- | --- |
| `pytest tool/` | 282 통과. 바꾸기 전 274, 한도 테스트 8개를 더했다 |
| 한도 테스트가 빨개지는가 | 한도 판정을 지우면 2개, 시간 초과 기록을 지우면 1개, 읽을 수 없는 한도를 기본값으로 바꾸면 1개, `thoughtsTokenCount` 를 빼면 1개, 저장소가 없을 때 보내게 바꾸면 1개가 빨강. 리뷰 라운드 1 수정분 — 사용액 조회 예외를 안 잡으면, 선차감 실패를 무시하면, 환불·정산을 빼면, 한도 읽기를 되돌리면 각각 1개가 빨강 |
| `python tool/lint.py --check` | 종료 0. `english_progress.py` 에 `translate.protect` 를 심으면 종료 1 과 `공개 진입점` 발견 |
| `python tool/test_lint.py` | `공개 진입점` 위반 다섯 가지가 빨강, 공개 이름·테스트 파일·`__all__` 없는 파이프라인은 초록 |
| 직접 실행 스크립트 | `test_apply`·`test_inject`·`test_declared_continuation`·`test_repo_lint`·`test_trajectory` 종료 0, `ruff check tool` 통과 |
| 실제 요청 | 버리는 캐시로 한 문장을 번역했다. `usageMetadata` 로 $0.000067 이 기록됐다. 한도 0 에서 새 문장은 원문, 캐시된 문장은 번역이 돌아왔다. 선차감으로 바꾼 뒤 다시 한 문장 — 선차감 약 $0.0017 이 실제 $0.000083 으로 정산됐다 |
| `python tool/translate --check` | 바꾸기 전과 출력이 같다 |

검증하다 찾은 것. `lint` 는 발견을 찍을 때 종류 이름을 고정된 목록에서만 골랐다. 목록에 없는
종류는 종료 코드를 1 로 만들면서 화면에는 안 나왔다 — 새 `공개 진입점` 이 그랬고, 기존
`주석이 한국어다` 도 그랬다. 목록에 없는 종류를 뒤에 붙여 찍는다.

리뷰. 라운드 1 이 P1 셋을 냈다. 한도 읽기(빈 값·`inf` 가 기본값·무제한이 됐다 — `nan` 은 이미 0 이었다), 사용액 조회 예외가 캐시 적중까지 버림, 응답 뒤 기록이 잠김으로 실패하면 비용이 빠짐. 셋 다 고쳤고, 마지막 것은 먼저 기록하고 보내는 쪽으로 바꿨다.
