# 팀원 PC에서 wiki-agent 시작하기

각자 자기 PC에 내려받고, 자기 Codex CLI 또는 Claude Code 계정으로 실행한다.
작성자의 PC·CLI·계정에 접속하는 방식이 아니다. 서버는 실행한 PC에서만 접속할 수 있다.

## 1. 먼저 준비하기

- Git, Python 3.11 이상, Node.js 22.12 이상, 창을 빌드할 Rust(<https://rustup.rs>)를 설치한다.
- 이 저장소와 질문할 프로젝트를 `git clone`으로 내려받는다. GitHub 저장소가 비공개라면 접근 권한이 필요하다.
- 사용할 CLI만 설치한다. 두 가지를 모두 설치할 필요는 없다.

Windows PowerShell에서 Codex CLI 설치:

```powershell
npm install -g @openai/codex
```

Windows PowerShell에서 Claude Code 설치:

```powershell
winget install Anthropic.ClaudeCode
```

설치 후 터미널을 새로 열고 `codex --version` 또는 `claude --version`이 실행되는지 확인한다.
기존 설치가 있다면 해당 CLI의 공식 방법으로 최신 버전으로 갱신한다.
macOS·Linux 설치는 [Codex CLI 공식 안내](https://learn.chatgpt.com/docs/codex/cli)와
[Claude Code 공식 안내](https://code.claude.com/docs/en/setup)를 따른다.
Claude Code의 Windows 환경에는 공식 안내에 따라 Git for Windows도 준비한다.

## 2. 설치와 내 계정 로그인

터미널에서 이 위키 폴더로 이동한 뒤, 사용할 도구에 맞는 명령 **하나**를 실행한다.
macOS·Linux에서 명령 이름이 `python3`라면 아래 `python` 대신 `python3`를 사용한다.

```powershell
# Codex CLI만 사용
python tool/setup_chat.py install --agent codex

# Claude Code만 사용
python tool/setup_chat.py install --agent claude

# 둘 다 사용
python tool/setup_chat.py install --agent both
```

명령은 `.venv`에 필요한 Python 패키지를 설치하고, `npm ci`와 화면 빌드를 실행한다.
그다음 각 CLI의 로그인 상태를 확인한다. 이미 로그인했다면 유지하고, 아니라면
공식 로그인 화면을 연다. 브라우저에서 **본인의 계정**을 확인하고 승인을 완료한다.
로그인이 확인되지 않으면 설치 완료로 표시하지 않는다.

프로젝트 폴더는 기본적으로 위키가 들어 있는 상위 폴더다. 다른 곳에 있다면 직접 지정한다.

```powershell
python tool/setup_chat.py install --agent codex --workspace "D:/팀 작업/프로젝트"
```

공백과 한글을 사용할 수 있다. 그 폴더 바로 아래에서 `.git`이 있는 프로젝트들을 찾는다.
위키는 별도 위치에 있어도 목록에 표시된다. 최초 실행에서는 위키가 선택되고 이후에는
마지막 선택을 복원한다. 왼쪽 레일의 ‘프로젝트’에서 고른 저장소가 위키 질의의 다섯 초점과
작업트리 목록에 함께 적용된다. 대화와 기록은 프로젝트·초점별로 보존된다.

## 3. 실행하기

처음 실행하기 전과 규칙을 고친 뒤에는 지도 파일을 생성한다. 개인 기록은 필요 없다.

```powershell
# Windows
.venv/Scripts/python tool/graph.py
```

macOS·Linux에서는 `.venv/bin/python tool/graph.py`를 쓴다.

창은 Tauri 로 싼다. 처음 한 번 빌드하려면 Rust 가 있어야 한다(<https://rustup.rs>).

Windows:

```powershell
.\tool\app.cmd
```

macOS·Linux (Finder에서 더블클릭해도 된다):

```bash
tool/app.command
```

처음에는 창을 빌드하느라 몇 분 걸린다. 창이 Python 서버를 빈 포트에 직접 띄우고, 창을
닫으면 서버와 에이전트·터미널도 같이 내려간다. 서버가 뜨지 않으면 창에 이유와
`raw/main.err.log` 의 끝이 나온다.

창 없이 브라우저로 쓰려면 `python tool/main` 을 돌리고 `http://127.0.0.1:8787` 을 연다.
터미널만 빠지고 나머지는 같다. 종료는 `Ctrl+C` 다.
Codex만 설치한 경우 설치 시 조회한 실제 모델이 기본 선택된다. 둘 다 설치하면 Claude가 기본이다.
다른 CLI를 화면에서 선택하려면 그 CLI도 설치·로그인되어 있어야 한다.

브라우저로 쓸 때 한 번만 다른 프로젝트 폴더나 포트를 쓰려면 다음처럼 실행한다.

```bash
python tool/main --workspace "D:/다른 프로젝트들" --port 9090
```

## 로그인 확인·계정 바꾸기

서버를 끈 상태에서 실행한다. 로그인에 쓴 터미널과 같은 사용자 환경에서 서버를 다시 켠다.
계정을 바꾸면 서버의 기존 대화 세션과 모델 목록을 이어 쓰지 않도록 반드시 재시작한다.
아래 `check`는 CLI 로그인 상태를 확인하는 명령이며 실제 답변 생성까지 검사하지 않는다.

```powershell
python tool/setup_chat.py check --agent both
python tool/setup_chat.py login --agent codex
python tool/setup_chat.py login --agent claude

# 로그인 화면을 다시 열어 계정을 확인하거나 변경
python tool/setup_chat.py login --agent codex --force-login
python tool/setup_chat.py login --agent claude --force-login
```

실제로 사용하는 로그인 명령은 `codex login`, `claude auth login`이다.
현재 계정·인증 방법을 직접 확인하려면 `codex login status`, `claude auth status`를 사용한다.
[Codex 인증 안내](https://learn.chatgpt.com/docs/auth),
[Claude Code 인증 명령](https://code.claude.com/docs/en/cli-reference).
모델 사용 가능 여부와 사용량은 각자 로그인한 계정의 권한·한도를 따른다.

앱은 `PATH`에서 찾은 CLI를 실행한다. `CODEX_HOME`, `CLAUDE_CONFIG_DIR` 등 기존 CLI 설정과
환경변수도 그 PC의 것을 그대로 사용한다. 별도로 지정한 인증 환경이 있다면 해당 CLI의 상태에서 확인한다.
앱이 계정 파일이나 비밀번호·토큰을 읽어 GitHub 또는 다른 팀원에게 복사하는 단계는 없다.
이 앱에는 웹 계정 로그인 화면이나 여러 사용자를 나누는 기능이 없으므로, 각자 별도 사본에서 실행한다.

## 갱신·문제 해결

- 코드를 갱신한 뒤 서버를 끄고 같은 `install` 명령을 다시 실행하면 필요한 패키지와 화면을 다시 준비한다. 기존 CLI 로그인은 유지한다.
- `.chat-local.json`에는 이 PC의 프로젝트 폴더와 기본 모델만 저장한다. Git에서 제외되며, 다른 팀원에게 전달할 필요가 없다.
- `.venv`, `web/node_modules`, `web/dist`, 대화 기록과 CLI 인증 폴더도 복사하지 않는다. 새 PC에서는 설치 명령을 다시 실행한다.
- 다른 드라이브로 옮겼거나 프로젝트 위치가 바뀌었다면 `--workspace`를 지정해 다시 설치한다.
- CLI를 찾지 못하면 해당 CLI를 설치한 뒤 새 터미널을 연다. Node.js를 찾지 못하면 Node.js 설치를 확인한다.
- 로그인 창을 닫거나 설치가 실패하면 원인을 해결하고 같은 명령을 다시 실행한다. 설치 도구는 기존 로그인 정보를 지우지 않는다.
- 화면 코드가 바뀌었는데 그대로라면 서버를 재시작하고 브라우저도 새로고침한다.

공통 규칙·hooks 설치는 별도다. [README의 팀원 설치 도구](hooks-setup.md)를 따른다.
이 채팅 설치 명령은 프로젝트 신뢰나 hooks 승인을 대신하지 않는다.
쉬운 설명 품질은 아직 [검수 기준 미통과](quality.md)다.
