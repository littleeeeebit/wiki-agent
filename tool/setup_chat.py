"""Install the chat on a team member's PC, sign in, and report state.

Credentials stay with each CLI; nothing here captures or copies a token.
Everything printed is read by the person doing the install, so those strings
are Korean.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

from agent.chat_local import ROOT, SETTINGS, cli_command, settings

AUTH = {
    "claude": (["auth", "status"], ["auth", "login"]),
    "codex": (["login", "status"], ["login"]),
}


def logged_in(agent):
    result = subprocess.run([*cli_command(agent), *AUTH[agent][0]], cwd=ROOT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    if result.returncode not in (0, 1):
        raise ValueError(f"{agent}: 로그인 상태 확인 실패. CLI를 갱신하고 직접 상태를 확인하세요.")
    return result.returncode == 0


def login(agent, force=False):
    if not force and logged_in(agent):
        print(f"{agent}: 이 PC 사용자의 기존 로그인을 사용합니다.")
        return
    print(f"{agent}: 브라우저에서 본인 계정으로 로그인하세요.", flush=True)
    # The parent terminal and this user's own environment, unchanged. No token
    # is captured or copied — the CLI owns the sign-in and keeps it.
    subprocess.run([*cli_command(agent), *AUTH[agent][1]], cwd=ROOT, check=True)
    if not logged_in(agent):
        raise ValueError(f"{agent}: 로그인이 확인되지 않았습니다. login 명령을 다시 실행하세요.")
    print(f"{agent}: 로그인 확인 완료")


def environment_python():
    return ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def install(agents, workspace):
    if sys.version_info < (3, 11):
        raise ValueError("Python 3.11 이상으로 실행하세요.")
    workspace = workspace.expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"프로젝트를 모아 둔 폴더가 없습니다: {workspace}")
    if workspace == ROOT:
        raise ValueError(f"프로젝트 폴더로 위키 자신을 지정했습니다: {workspace}. "
                         f"프로젝트들이 들어 있는 상위 폴더를 지정하세요: {ROOT.parent}")
    for name in ("git", "node", "npm", *agents):
        cli_command(name)
    version = subprocess.check_output([*cli_command("node"), "--version"], text=True).strip()
    if tuple(map(int, version.lstrip("v").split("."))) < (22, 12, 0):
        raise ValueError("Node.js 22.12 이상이 필요합니다.")
    python = environment_python()
    if not (ROOT / ".venv").exists():
        venv.EnvBuilder(with_pip=True).create(ROOT / ".venv")
    if not python.is_file():
        raise ValueError(".venv에 Python이 없습니다. 다른 PC의 가상환경을 복사하지 말고 이 PC에서 새로 만드세요.")
    print("1/3: 앱 전용 Python 환경과 패키지를 준비합니다.", flush=True)
    result = subprocess.run([str(python), "-c", "import sys; sys.exit(sys.version_info < (3, 11))"], cwd=ROOT)
    if result.returncode:
        raise ValueError("기존 .venv의 Python이 3.11 미만이거나 실행되지 않습니다. 새 Python으로 가상환경을 준비하세요.")
    subprocess.run([str(python), "-m", "pip", "install", "-r", str(ROOT / "requirements-chat.txt")],
                   cwd=ROOT, check=True)
    print("2/3: 화면 패키지를 설치하고 빌드합니다. 처음에는 몇 분 걸릴 수 있습니다.", flush=True)
    npm = cli_command("npm")
    subprocess.run([*npm, "ci"], cwd=ROOT / "web", check=True)
    subprocess.run([*npm, "run", "build"], cwd=ROOT / "web", check=True)
    print("3/3: 선택한 CLI에서 이 PC 사용자의 로그인을 확인합니다.", flush=True)
    for agent in agents:
        login(agent)
    model = ""
    if agents == ["codex"]:
        from chat_channels import codex_models
        models = codex_models()
        model = next((m for m in models if m.get("is_default")), models[0])["id"]
    # The path and the default model are written only once the install has
    # finished. No account and no token is written at any point.
    data = settings()
    try:
        local_workspace = os.path.relpath(workspace, ROOT)
    except ValueError:  # two Windows drives have no relative path between them
        local_workspace = str(workspace)
    data.update(workspace=local_workspace, model=model)
    SETTINGS.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print("설치 완료. Windows: tool\\chat.cmd / macOS·Linux: tool/chat.command")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "login", "check"))
    parser.add_argument("--agent", choices=("claude", "codex", "both"), required=True,
                        help="Claude Code / Codex CLI / 둘 다")
    parser.add_argument("--workspace", type=Path, help="프로젝트들이 들어 있는 폴더 (기본: 위키의 상위 폴더)")
    parser.add_argument("--force-login", action="store_true", help="login에서만: 기존 로그인 대신 로그인 화면 열기")
    args = parser.parse_args()
    if args.force_login and args.action != "login":
        parser.error("--force-login은 login에서만 사용하세요.")
    agents = list(AUTH) if args.agent == "both" else [args.agent]
    try:
        if args.action == "install":
            saved = settings().get("workspace", str(ROOT.parent))
            install(agents, args.workspace or ROOT / Path(saved).expanduser())
        elif args.action == "login":
            for agent in agents:
                login(agent, args.force_login)
        else:
            ready = True
            for agent in agents:
                ok = logged_in(agent)
                print(f"{agent}: {'로그인됨' if ok else '로그인 필요'}")
                ready = ready and ok
            return 0 if ready else 1
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"실패: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("중단했습니다. 완료되지 않은 단계는 같은 명령으로 다시 실행할 수 있습니다.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
