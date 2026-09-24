"""graph — produce the policy graph. Drawing it happens elsewhere."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from wiki.match import (  # noqa: E402
    INJECTABLE, RULE_BUDGET, SLOT, adapter_path, budget, slots_for,
)
from apply import runs  # noqa: E402
from wiki.wikilib import WIKI, front_matter  # noqa: E402

SCOPES = ("operator", "craft")
HOOK_MARK = "inject.py"
NS = "rule"          # the axis this file holds; the knowledge axis lives in a
                     # target repository's own `.wiki/graph.json`

# The enforcement ladder. The colours run from solid to diffuse: layer 1
# blocks, layer 5 is a sentence.
LADDER = [
    (1, "차단", "permissions.deny", "#2b3a67"),
    (2, "주입", "UserPromptSubmit 훅", "#3f6b8f"),
    (3, "절차", "스킬", "#6b8ea3"),
    (4, "검사", "PreToolUse 훅", "#93a8ac"),
    (5, "문장", "산문", "#b9b3a4"),
]

# The ladder is numbered by cost, not by strength. Cheapest is 1, which is
# what makes "stop at the first rung that holds" work. But the question a
# node's colour has to answer is how hard the rule is enforced, and that is a
# different order: the two that block (1 and 4) are strongest, then the
# procedure, then an injection that is only read, and a sentence last.
STRENGTH = [1, 4, 3, 2, 5]


def page_files(project_paths: list[Path]) -> list[tuple[str, str, Path]]:
    """`(scope, name, path)`: the shared wiki's rules plus each repository's
    knowledge pages.

    Decision records are excluded. One repository has 88 of them, so drawing
    them turns a graph into sand, and they do not link to one another anyway.
    Only the count goes into the project table.
    """

    found = []
    for scope in SCOPES:
        directory = WIKI / scope
        if directory.is_dir():
            for path in sorted(directory.glob("*.md")):
                found.append((scope, f"{scope}/{path.stem}", path))
    for repo in project_paths:
        directory = repo / ".wiki"
        if directory.is_dir():
            for path in sorted(directory.glob("*.md")):
                found.append((repo.name, f"{repo.name}/.wiki/{path.stem}", path))
    return found


def load_pages(project_paths: list[Path] | None = None) -> dict[str, dict]:
    pages: dict[str, dict] = {}
    for scope, name, path in page_files(project_paths or []):
        meta, body = front_matter(path.read_text(encoding="utf-8"))
        enforce = meta.get("enforce") if isinstance(meta.get("enforce"), dict) else {}
        headline, rule = "", ""
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("# ") and not headline:
                headline = line[2:]
                continue
            # Both spellings. While pages are being rewritten in English the
            # two live side by side, and reading only one leaves that page's
            # `rule` empty — which takes the map's description and the
            # `--check` comparison with it.
            for marker in ("규칙.", "Rule."):
                if line.startswith(marker):
                    rule = line[len(marker):].strip()
                    break
            if rule:
                break
        pages[name] = {
            "scope": scope,
            "local": path.parent.name == ".wiki",
            "severity": str(meta.get("severity") or "preference"),
            "triggers": [str(t) for t in (meta.get("triggers") or [])],
            "links": [str(x) for x in (meta.get("links") or [])]
            + re.findall(r"\[\[([^\]]+)\]\]", body),
            "sources": [str(s).split("#")[0] for s in (meta.get("sources") or [])]
            + [str(s) for s in (meta.get("reads") or [])],
            "deny": [str(d) for d in (enforce.get("deny") or [])],
            "pretooluse": str(enforce.get("pretooluse") or ""),
            "headline": headline or path.stem,
            "rule": rule,
            "chars": len(body),
            "slots": sorted(set(SLOT.findall(body))),
        }
    return pages


def skills_touching(pages: dict[str, dict]) -> dict[str, list[str]]:
    """Which skill says it holds which page."""

    touched: dict[str, list[str]] = {name: [] for name in pages}
    directory = WIKI / "skills"
    if not directory.is_dir():
        return touched
    for path in sorted(directory.glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8")
        for name in pages:
            if name in text or name.rsplit("/", 1)[-1] in text:
                touched[name].append(path.parent.name)
    return touched


def layers_of(page: dict, skills: list[str]) -> list[int]:
    """The layers this rule actually stands on. Prose (5) is always one of them."""

    found = []
    if page["deny"]:
        found.append(1)
    if page["severity"] in INJECTABLE and page["triggers"]:
        found.append(2)
    if skills:
        found.append(3)
    if page["pretooluse"]:
        found.append(4)
    return found + [5]


def corpus_of(name: str) -> list[str]:
    path = WIKI / "raw" / f"census-{name}.jsonl"
    if not path.exists():
        return []
    found = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            text = json.loads(line).get("text")
        except Exception:
            continue
        if text:
            found.append(text)
    return found


def read_project(path: Path, pages: dict[str, dict]) -> dict:
    """What is actually attached in a target repository.

    Reads what that side's `settings.json` holds rather than what the wiki
    declared. The two disagreeing is the question this screen exists to
    answer.
    """

    settings_path = path / ".claude" / "settings.json"
    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            settings = {}
    deny = set((settings.get("permissions") or {}).get("deny") or [])
    commands = [
        str(entry.get("command", ""))
        for event in (settings.get("hooks") or {}).values()
        for group in event
        for entry in group.get("hooks", [])
    ]
    # One question, answered in one place. Asked as containment, a single
    # foreign `custom-tool/inject.py` makes the map say the injector is
    # attached, and the rule count goes wrong with it.
    inject = any(runs(c, HOOK_MARK) for c in commands)
    values = slots_for(path.name, path)

    status: dict[str, str] = {}
    for name, page in pages.items():
        # Another repository's knowledge pages have nothing to do with this one.
        if page["local"] and not name.startswith(f"{path.name}/"):
            status[name] = "foreign"
            continue
        wants = []
        if page["deny"]:
            wants.append(page["deny"] and all(d in deny for d in page["deny"]))
        if page["pretooluse"]:
            wants.append(any(page["pretooluse"] in c for c in commands))
        if page["severity"] in INJECTABLE and page["triggers"]:
            wants.append(inject)
        if not wants:
            status[name] = "prose"
        elif all(wants):
            status[name] = "on"
        elif any(wants):
            status[name] = "partial"
        else:
            status[name] = "none"

    missing = sorted({
        slot
        for page in pages.values()
        if page["severity"] in INJECTABLE and page["triggers"]
        for slot in page["slots"]
        if slot not in values
    })
    corpus = corpus_of(path.name)
    attached = sum(1 for v in status.values() if v == "on")
    return {
        "key": path.name,
        "short": path.name,
        "path": str(path),
        "adapter": (str(path / ".wiki/adapter.toml") if (path / ".wiki/adapter.toml").exists()
                    else f"adapters/{path.name}.toml") if values else "",
        "inject": inject,
        "deny": len([d for d in deny]),
        # The hooks other than the injector. What counts as the injector is
        # the same question `inject` asks, so it takes the same answer —
        # counted by containment, a foreign `inject.py` hook drops out and the
        # map reports fewer hooks than there are.
        "hooks": len([c for c in commands if not runs(c, HOOK_MARK)]),
        "corpus": len(corpus),
        "missing": missing,
        "status": status,
        "note": (
            f"규칙 {attached}장이 붙어 있다."
            if attached
            else "아직 아무것도 안 붙었다. `apply` 를 돌리지 않은 저장소다."
        ),
        "_corpus": corpus,
    }


def co_injection(pages: dict[str, dict], corpus: list[str]) -> dict[tuple[str, str], int]:
    """How often two pages were carried into the same utterance.

    This is the axis Obsidian does not have. A wikilink is what a person
    connected; this is measured from real traffic, and two rules arriving side
    by side in one turn without knowing about each other leave the reader
    unable to see the relation.
    """

    compiled = {
        name: [re.compile(t, re.IGNORECASE) for t in page["triggers"]]
        for name, page in pages.items()
        if page["severity"] in INJECTABLE and page["triggers"]
    }
    pairs: dict[tuple[str, str], int] = {}
    for text in corpus:
        hit = sorted(n for n, ps in compiled.items() if any(p.search(text) for p in ps))
        for i, a in enumerate(hit):
            for b in hit[i + 1 :]:
                pairs[(a, b)] = pairs.get((a, b), 0) + 1
    return pairs


def per_turn_load(pages: dict[str, dict], corpus: list[str]) -> dict[str, int]:
    """How much is actually carried in one turn.

    Not the sum of the page bodies. Six pages never all match in one turn, so
    the sum means nothing — and calling it a "budget" makes it read like a
    ceiling nobody ever set.
    """

    compiled = {
        name: (page["chars"], [re.compile(t, re.IGNORECASE) for t in page["triggers"]])
        for name, page in pages.items()
        if page["severity"] in INJECTABLE and page["triggers"]
    }
    loads, counts = [], []
    for text in corpus:
        hit = [c for c, ps in compiled.values() if any(p.search(text) for p in ps)]
        if hit:
            loads.append(sum(hit))
            counts.append(len(hit))
    if not loads:
        return {"max": 0, "median": 0, "pages": 0, "hits": 0}
    loads.sort()
    return {
        "max": max(loads),
        "median": loads[len(loads) // 2],
        "pages": max(counts),
        "hits": len(loads),
    }


def build(pages: dict[str, dict], project_paths: list[Path]) -> dict:
    skills = skills_touching(pages)
    names = set(pages)
    projects = [read_project(p, pages) for p in project_paths]
    everything = [t for p in projects for t in p["_corpus"]] or corpus_of("*")
    if not everything:
        for path in sorted((WIKI / "raw").glob("census-*.jsonl")):
            everything += corpus_of(path.stem.removeprefix("census-"))

    nodes = []
    for name, page in pages.items():
        layers = layers_of(page, skills[name])
        nodes.append({
            "id": name,
            "label": name.rsplit("/", 1)[-1],
            "scope": page["scope"],
            "severity": page["severity"],
            "headline": page["headline"],
            "rule": page["rule"],
            "chars": page["chars"],
            "injected": page["severity"] in INJECTABLE and bool(page["triggers"]),
            "triggers": page["triggers"],
            "layers": layers,
            "layer": min(layers, key=STRENGTH.index),
            "deny": page["deny"],
            "pretooluse": page["pretooluse"],
            "skills": skills[name],
            "sources": page["sources"],
            "status": {p["key"]: p["status"][name] for p in projects},
        })

    def resolve(target: str) -> str | None:
        if target in names:
            return target
        return next((n for n in names if n.split("/", 1)[1] == target), None)

    links, seen = [], set()
    for name, page in pages.items():
        for target in page["links"]:
            hit = resolve(target)
            if hit and hit != name:
                key = tuple(sorted((name, hit)))
                if key not in seen:
                    seen.add(key)
                    links.append({"a": key[0], "b": key[1], "kind": "link", "by": {}})

    by_key = {"all": co_injection(pages, everything)}
    for p in projects:
        by_key[p["key"]] = co_injection(pages, p["_corpus"])
    pairs = sorted({k for table in by_key.values() for k in table})
    for a, b in pairs:
        links.append({
            "a": a, "b": b, "kind": "co",
            "by": {key: table.get((a, b), 0) for key, table in by_key.items()},
        })

    for p in projects:
        p.pop("_corpus", None)

    return {
        "nodes": nodes,
        "links": links,
        "projects": projects,
        "evidence": sorted({s for page in pages.values() for s in page["sources"]}),
        "corpus": len(everything),
        "load": per_turn_load(pages, everything),
        "cap": next(
            (budget(p["key"], RULE_BUDGET, p["path"]) for p in projects
             if budget(p["key"], RULE_BUDGET, p["path"])), 0),
        "ladder": [{"n": n, "title": t, "detail": d, "color": c} for n, t, d, c in LADDER],
    }


def connected() -> list[Path]:
    """With no `--project`, find the attached repositories without being told.

    Holding the list by hand means editing this every time a repository is
    added, which means not editing it. Attached is decided the same way the
    injector decides it — does that repository have a `.wiki/adapter.toml`.
    No repository name is written down anywhere.

    Where to look: the workspace in the chat's `.chat-local.json` when there
    is one, otherwise the wiki's parent folder. When `WIKI_ROOT` points at a
    hub that is not this checkout, nothing is searched — that hub's parent is
    not where projects are kept, and this really did once walk a temporary
    folder and put somebody else's repository in the graph.
    """

    if WIKI != HERE.parent:
        return []
    where = ".."
    config = WIKI / ".chat-local.json"
    if config.is_file():
        try:
            where = json.loads(config.read_text(encoding="utf-8")).get("workspace") or ".."
        except (OSError, ValueError):
            pass
    try:
        workspace = (WIKI / Path(where).expanduser()).resolve()
        return [path for path in sorted(workspace.iterdir())
                if (path / ".git").exists() and adapter_path(project=path)]
    except OSError as error:
        print(f"붙은 저장소를 못 찾았다 ({type(error).__name__}). --project 로 직접 줘라.")
        return []


def main() -> int:
    # Down a pipe the default here is cp949. The encoding is not left to the
    # environment.
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="정책 그래프를 아티팩트로 낸다")
    parser.add_argument("--json", type=Path, default=WIKI / "graph.json")
    parser.add_argument(
        "--project", action="append", type=Path, default=[],
        help="붙은 상태를 읽을 저장소. 여러 번 줄 수 있다 (기본: 붙은 저장소를 스스로 찾는다)",
    )
    args = parser.parse_args()

    projects = [p.expanduser().resolve() for p in args.project if p.expanduser().is_dir()]
    if not args.project:
        projects = connected()
    pages = load_pages(projects)
    if not pages:
        print("페이지가 없다.")
        return 2

    data = build(pages, projects)
    data["ns"] = NS
    args.json.write_text(
        json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )

    co = [x for x in data["links"] if x["kind"] == "co"]
    load = data["load"]
    print(f"# graph — 노드 {len(data['nodes'])} · 링크 {len(data['links']) - len(co)} "
          f"· 공동 주입 {len(co)}")
    for p in data["projects"]:
        attached = sum(1 for v in p["status"].values() if v == "on")
        print(f"  {p['short']:32} 붙은 규칙 {attached}/{len(pages)} · 코퍼스 {p['corpus']:,}")
    cap = data["cap"]
    where = f"예산 {cap:,}" if cap else "예산 없음"
    print(f"한 턴 최대 {load['max']:,}자({load['pages']}장) · "
          f"중앙 {load['median']:,}자 · {where}")
    print(f"썼다: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
