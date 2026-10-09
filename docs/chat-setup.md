# Starting wiki-agent on team member PCs

Each person downloads it to their own PC and runs it with their own Codex CLI or Claude Code account.
It is not a method of connecting to the author's PC, CLI, or account. The server runs locally by default. To connect your own phone to your PC over mobile data or another Wi-Fi network, use the [mobile companion setup](mobile.md).

## 1. Preparation first

- Install Git, Python 3.11 or higher, Node.js 22.12 or higher, and Rust (<https://rustup.rs>) to build the window.
- Download this repository and the project to be queried using `git clone`. If the GitHub repository is private, access permissions are required.
- Install only the CLI you intend to use. There is no need to install both.

Installing Codex CLI in Windows PowerShell:

```powershell
npm install -g @openai/codex
```

Installing Claude Code in Windows PowerShell:

```powershell
winget install Anthropic.ClaudeCode
```

After installation, open a new terminal and verify that `codex --version` or `claude --version` runs. If there is an existing installation, update to the latest version using the official method for that CLI.
For macOS/Linux installation, follow the [Codex CLI official guide](https://learn.chatgpt.com/docs/codex/cli) and [Claude Code official guide](https://code.claude.com/docs/en/setup).
For the Claude Code Windows environment, also prepare Git for Windows according to the official guide.

## 2. Installation and logging into my account

In the terminal, navigate to this wiki folder and run **one** command suitable for the tool you are using.
If the command name is `python3` on macOS/Linux, use `python3` instead of `python` below.

```powershell
# Codex CLI만 사용
python tool/setup_chat.py install --agent codex

# Claude Code만 사용
python tool/setup_chat.py install --agent claude

# 둘 다 사용
python tool/setup_chat.py install --agent both
```

The command installs the Python packages required for `.venv` and runs `npm ci` and the screen build.
Then, check the login status of each CLI. If already logged in, maintain it; otherwise, open the official login screen. Check **your account** in the browser and complete the approval.
If the login is not verified, it will not be marked as installation complete.

By default, the project folder is the parent folder containing the wiki. If it is elsewhere, specify it directly.

```powershell
python tool/setup_chat.py install --agent codex --workspace "D:/팀 작업/프로젝트"
```

Spaces and Korean characters can be used. It searches for projects containing `.git` directly under that folder.
Even if the wiki is in a separate location, it is displayed in the list. The wiki is selected on the first run, and the last selection is restored thereafter. The repository chosen in 'Projects' on the left rail is applied to both the four focuses of wiki queries and the worktree list. Conversations and history are preserved per project and focus.

## 3. Running

Before running for the first time and after modifying rules, generate a map file. Personal records are not needed.

```powershell
# Windows
.venv/Scripts/python tool/graph.py
```

On macOS/Linux, use `.venv/bin/python tool/graph.py`.

The window is wrapped with Tauri. To build it for the first time, Rust must be present (<https://rustup.rs>).

Windows:

```powershell
.\tool\app.cmd
```

macOS/Linux (you can also double-click in Finder):

```bash
tool/app.command
```

It takes a few minutes to build the window for the first time. The window launches the Python server directly on an empty port, and when the window is closed, the server, agent, and terminal also shut down. If the server does not start, the reason and the end of `raw/main.err.log` will appear in the window.

To use it in a browser without a window, run `python tool/main` and open `http://127.0.0.1:8787`.
Only the terminal is omitted, and the rest is the same. To exit, use `Ctrl+C`.
If only Codex is installed, the actual model queried during installation is selected by default. If both are installed, Claude is the default.
To select a different CLI on the screen, that CLI must also be installed and logged in.

### Updates

The server checks GitHub's `origin/main` against the commit it started from,
at most once an hour. When `main` has moved on, a card at the bottom of the
rail lists the new commits. It never interrupts: "나중에" hides that version
only, and a failed check shows nothing. "업데이트" fast-forwards a clean `main`
checkout and, when the pull changed them, reinstalls `requirements-chat.txt`,
reruns `npm ci` and rebuilds the screen. The running app keeps its old code,
so the card then offers "다시 시작". It asks about running tasks and review
loops the way closing the window does, starts `tool/app.cmd`
(`tool/app.command`) and closes the window. The launcher waits until the old
window and its server are gone, rebuilds the window when the pull changed it
and opens it again. In a browser tab the card says to reopen
the app with that launcher instead. Another
branch, uncommitted tracked changes or a diverged `main` disable the button;
pull by hand then. A paired phone sees the card but cannot update.

To use a different project folder or port just once when using a browser, run as follows:

```bash
python tool/main --workspace "D:/다른 프로젝트들" --port 9090
```

## Checking login/Changing accounts

Run with the server turned off. Restart the server in the same user environment as the terminal used for login.
If you change accounts, be sure to restart so that the server does not continue using existing conversation sessions and model lists.
`check` below is a command to check the CLI login status and does not verify up to actual answer generation.

```powershell
python tool/setup_chat.py check --agent both
python tool/setup_chat.py login --agent codex
python tool/setup_chat.py login --agent claude

# 로그인 화면을 다시 열어 계정을 확인하거나 변경
python tool/setup_chat.py login --agent codex --force-login
python tool/setup_chat.py login --agent claude --force-login
```

The actual login commands used are `codex login`, `claude auth login`.
To check the current account/authentication method directly, use `codex login status`, `claude auth status`.
[Codex authentication guide](https://learn.chatgpt.com/docs/auth),
[Claude Code authentication command](https://code.claude.com/docs/en/cli-reference).
Model availability and usage follow the permissions/limits of the account each person logged in with.

The app runs the CLI found in `PATH`. It uses the existing CLI settings and environment variables such as `CODEX_HOME`, `CLAUDE_CONFIG_DIR` from that PC as they are. If there is a separately specified authentication environment, check it in the status of that CLI.
There is no step where the app reads account files, passwords, or tokens to copy them to GitHub or other team members. Since this app does not have a web account login screen or a feature to separate multiple users, each person runs it from a separate copy.

## Updates/Troubleshooting

- After updating the code, turn off the server and run the same `install` command again to re-prepare the necessary packages and screen. Existing CLI logins are maintained.
- `.chat-local.json` only stores this PC's project folder and default model. It is excluded from Git and does not need to be delivered to other team members.
- `.venv`, `web/node_modules`, `web/dist`, conversation history, and CLI authentication folders are also not copied. Run the installation command again on a new PC.
- If you have moved to a different drive or the project location has changed, specify `--workspace` and reinstall.
- If the CLI cannot be found, install the corresponding CLI and open a new terminal. If Node.js cannot be found, check the Node.js installation.
- If the login window is closed or installation fails, resolve the cause and run the same command again. The installation tool does not clear existing login information.
- If the screen code has changed but remains the same, restart the server and refresh the browser.

Common rules/hooks installation is separate. Follow [README's team member installation tool](hooks-setup.md).
This chat installation command does not replace project trust or hook approval.
Easy explanation quality has not yet [passed inspection criteria](quality.md).
