"""Print the DeepSeek key from the `.env` file named on the command line: the Claude CLI's `apiKeyHelper`.

The CLI asks for the key itself, so the key never sits in the CLI's environment, where every Bash command, hook and
MCP server it starts would inherit it.
"""

import sys
from pathlib import Path

from common import settings

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(settings.entries(Path(sys.argv[1])).get("DEEPSEEK_API_KEY", ""))
