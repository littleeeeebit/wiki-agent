"""`python tool/jev_probe.py [--live]` — is Jev configured, and does it answer?

Without `--live` it prints the settings as this process reads them and sends
nothing. With `--live` it sends one synthetic request — a Noul and a Choice
question about a made-up sky, no repository content — and reports the
responding model, usage, elapsed time and, on failure, the error category:
missing_api_key, auth_failed, quota, timeout, unavailable and the rest. A
reachable service establishes connectivity, not answer quality.

Exit 0 when configured (or, with `--live`, reachable), 1 otherwise.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import decision  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python tool/jev_probe.py", description=__doc__.split("\n")[0])
    parser.add_argument("--live", action="store_true", help="send one synthetic request (a paid API call)")
    args = parser.parse_args(argv)
    cfg = decision.config()
    if not args.live:
        status = cfg.status()
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0 if status["health"] == "configured" else 1
    result = decision.probe(cfg)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["health"] == "reachable" else 1


if __name__ == "__main__":
    raise SystemExit(main())
