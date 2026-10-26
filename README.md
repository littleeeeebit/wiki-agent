# wiki-agent

위키에 먼저 묻고, 일은 코딩 에이전트에게 맡기고, 결과는 리뷰 루프로 돌려받는
데스크톱 앱입니다. Claude Code와 Codex CLI 위에서 돌아갑니다.

AI 코딩 에이전트는 세션이 끝나면 배운 것을 잊습니다. 지난주에 바로잡은 실수를
이번 주 다른 프로젝트에서 또 합니다. wiki-agent는 그 교훈을 위키에 적어 두고,
에이전트가 일을 시작하기 전에 맞는 규칙을 눈앞에 먼저 펼쳐 줍니다. 그 위에
"무엇을 할지 정하기 → 맡기기 → 리뷰 → 머지 → 왜 그렇게 했는지 기록"을 한 창에서
돌리는 앱을 얹었습니다.

한국어 사용자를 위해 만들었습니다. 화면, 질문, 선택지는 한국어입니다. 에이전트는
더 정확하게 일하도록 영어로 생각하고 쓰며, 사람은 번역을 거쳐 한국어로 읽습니다.

왜 이걸 만들었고 어떤 순서로 커졌는지는 [소개 사이트의 이야기](https://littleeeeebit.github.io/wiki-agent/overview.html)에
있습니다(저장소 안의 [`overview.html`](overview.html)과 [`overview/`](overview/)).

## 다섯 갈래

| 갈래 | 하는 일 | 자세히 |
| --- | --- | --- |
| ① 규칙 훅 | 교훈을 규칙 페이지로 적고, 훅이 세션 시작 · 말할 때마다 · 도구 직전 · 턴 끝에 끼어들어 규칙을 읽히거나 위반을 막습니다. 앱 없이도 어느 저장소에나 붙습니다 | [규칙 훅](https://littleeeeebit.github.io/wiki-agent/overview/wiki.html) |
| ② 맡기기 | 다음 작업을 대화로 명세 한 장에 정하고, 작업 브랜치에서 에이전트에게 맡기고, 서버가 검사를 다시 돌려 PR을 올립니다. 큰 일의 계획과 리팩터링도 같은 길로 갑니다 | [맡기기](https://littleeeeebit.github.io/wiki-agent/overview/task.html) |
| ③ 리뷰 | 읽기만 하는 리뷰 칸이 라운드마다 읽고, 머지 허용이 나오면 허용된 그 커밋으로 머지하고 결정을 기록합니다 | [리뷰](https://littleeeeebit.github.io/wiki-agent/overview/review.html) |
| ④ 묻기 · Jev | 저장소의 문서 · 결정 · 코드를 근거로 답합니다. 검색과 작업 흐름의 갈림길은 결정 모델 Jev가 고르고, 권한은 코드가 쥡니다 | [묻기 · Jev](https://littleeeeebit.github.io/wiki-agent/overview/ask.html) |
| ⑤ 회고 | 하루의 어긋난 자리를 세어 규칙 후보로 내고, 자가 개선 실험은 재서 나아진 것만 고릅니다 | [회고](https://littleeeeebit.github.io/wiki-agent/overview/improve.html) |

## 이런 분께 맞습니다

- Claude Code나 Codex를 매일 쓰는데, 같은 지적을 계속 반복하게 되는 분
- 에이전트에게 맡긴 일을 명세, PR, 리뷰, 머지까지 한 흐름으로 관리하고 싶은 분
- 여러 저장소에 같은 작업 규칙을 걸어 두고 싶은 분

## 시작하기

### 준비물

- Git, Python 3.11 이상, Node.js 22.12 이상
- Rust(<https://rustup.rs>) — 데스크톱 창을 처음 빌드할 때 필요합니다
- Claude Code나 Codex CLI 중 하나 이상, 본인 계정으로 로그인
- 선택: Gemini API 키 — 한국어 번역에 씁니다

주로 Windows에서 쓰며 만들었습니다. macOS와 Linux용 실행 스크립트도 있습니다.

### 앱 설치와 실행

```powershell
git clone https://github.com/littleeeeebit/wiki-agent.git
cd wiki-agent
python tool/setup_chat.py install --agent codex   # 또는 claude, both
.venv/Scripts/python tool/graph.py               # 지도 파일. macOS·Linux: .venv/bin/python
.\tool\app.cmd                                    # macOS·Linux: tool/app.command
```

`setup_chat.py`는 패키지와 화면을 준비하고, CLI에 로그인이 안 돼 있으면 공식 로그인
창을 엽니다. 창을 처음 빌드할 때는 몇 분 걸립니다. 창 없이 브라우저로 쓰려면
`python tool/main`을 실행하고 `http://127.0.0.1:8787`을 여세요.

프로젝트 목록은 기본으로 위키 폴더의 상위 폴더에서 `.git`이 있는 폴더를 찾습니다.
다른 곳에 있다면 설치할 때 `--workspace "D:/작업 폴더"`로 지정합니다.

한국어 번역을 쓰려면 위키 폴더에 `.env` 파일을 만들고 키를 적습니다.

```text
GEMINI_API_KEY=여기에-본인-키
```

자세한 설치와 문제 해결은 [`docs/chat-setup.md`](docs/chat-setup.md)에 있습니다.

### 내 프로젝트에 규칙 연결하기

앱의 프로젝트 화면에서 [연결]을 누르면 어댑터 작성, 훅 설치, 실제 세션 시험까지
안내합니다. 앱 없이 명령으로 하려면:

```powershell
python -m pip install -r requirements-hooks.txt
python tool/setup_agents.py --project "../내-프로젝트" --agent both
```

대상 프로젝트에 미리 둘 파일(`.wiki/adapter.toml`, `.wiki/wiki-revision`)과 훅 신뢰,
실제 동작 확인은 [`docs/hooks-setup.md`](docs/hooks-setup.md)에 있습니다.

## 쓰기 전에 알아 둘 점

- 작업 세션은 선택한 체크아웃의 작업 브랜치에서 전체 권한으로 돕니다. 파일을 쓸
  때마다 허용을 기다리지 않습니다. 중요한 변경은 커밋해 두고 쓰세요.
- 모델 사용량은 각자 CLI 계정에서 나갑니다. 리뷰 루프와 리팩터링 조사는 여러 턴을
  씁니다.
- 서버는 실행한 PC에서만 열립니다. 외부에서 닿는 길은 휴대폰 연결을 직접 켰을 때뿐입니다.
- 한 사람이 매일 쓰면서 고치는 프로젝트라 빠르게 바뀝니다.
- Jev는 TypeSafe API 키가 있어야 돌고, 질문과 검색한 조각이 TypeSafe로 갑니다.
  키가 없어도 나머지 기능은 기본 검색으로 동작합니다.
- 진행 중인 계획은 [`docs/plans/`](docs/plans/)에 있습니다.

## 문서

| 알고 싶은 것 | 읽을 곳 |
| --- | --- |
| 왜 만들었나, 다섯 갈래 설명 | [`overview.html`](overview.html), [`overview/`](overview/) |
| 설치와 실행 순서 한 장 | [`overview/guide.html`](overview/guide.html) |
| Jev 운영과 되돌리기 | [`docs/jev-maintenance.md`](docs/jev-maintenance.md) |
| 자가 개선 실험 | [`docs/self-improvement.md`](docs/self-improvement.md) |
| 앱 설치, 계정, 문제 해결 | [`docs/chat-setup.md`](docs/chat-setup.md) |
| 내 프로젝트에 규칙 연결 | [`docs/hooks-setup.md`](docs/hooks-setup.md) |
| 휴대폰 연결 | [`docs/mobile.md`](docs/mobile.md) |
| 프로그램 구조 | [`docs/architecture.md`](docs/architecture.md) |
| 개발과 검사 | [`docs/development.md`](docs/development.md) |
| 자세한 운영 노트(영어) | [`docs/reference.md`](docs/reference.md) |
| 공통 규칙 목록 | [`index.md`](index.md) |
| 위키를 고치기 전에 | [`SCHEMA.md`](SCHEMA.md), [`ENFORCEMENT.md`](ENFORCEMENT.md), [`MAINTENANCE.md`](MAINTENANCE.md) |

## 배경

Karpathy의 [LLM wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
아이디어를 코딩 에이전트에 맞게 바꿨습니다. 원래는 논문과 글이 원재료지만, 여기서는
대화 기록과 실행 기록이 원재료라서 "읽고 요약하기"가 아니라 "세고 비교하기"로
규칙을 만듭니다.

이 공개 저장소에는 원본의 규칙, 스킬, 적용 범위, 심각도가 그대로 있고, 실제 대화와
측정 기록만 빠져 있습니다. 근거를 비공개로 둔 규칙에는 `sources_withheld: true`가
붙어 있습니다.

질문이나 버그 제보는 GitHub 이슈로 남겨 주세요.
