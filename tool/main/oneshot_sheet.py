"""The oneshot backend design: its blocks checked, the R1 sheet rendered from them, and what an edit reaches.

`docs/plans/oneshot/3-spec-backend.md` ("S3", "R1") is the design. Pure
functions: no records, no sessions. The sheet is rendered by code from the
stored blocks, so the sheet and the record cannot disagree.
"""

from __future__ import annotations

import json
import re
import unicodedata

# A requirement: where it came from, whether it is decided, and its conditions.
SOURCES = ("original", "policy", "repository", "decision")
STATUSES = ("confirmed", "assumed", "undecided", "not_applicable")
CONDITIONS = ("preconditions", "postconditions", "invariants", "failures")
REQUIREMENT_KEYS = frozenset({"id", "text", "source", "status", *CONDITIONS, "depends_on", "context", "verify", "global",
                              "capability", "kinds"})
# A platform capability an Android requirement needs: the permission, the prompt the user sees, the distribution risk.
CAPABILITY_KEYS = ("permission", "prompt", "risk")
RID = re.compile(r"R[1-9]\d*")

# Every call that leaves the product, with its failure handling and what the owner gives it (`oneshot_needs`).
OUTSIDE = ("oneshot-outside", re.compile(r"X[1-9]\d*"), ("target", "does", "failure", "needs"))
# Each design item kind: its block, its id pattern and the keys it carries.
KINDS = {
    "entities": ("oneshot-entities", re.compile(r"E[1-9]\d*"), ("name", "fields", "relations")),
    "api": ("oneshot-api", re.compile(r"P[1-9]\d*"), ("method", "path", "does", "request", "response", "errors")),
    "flows": ("oneshot-flows", re.compile(r"F[1-9]\d*"), ("name", "steps")),
    "assumptions": ("oneshot-assumptions", re.compile(r"A[1-9]\d*"), ("what", "why", "rejected")),
    "tests": ("oneshot-tests", re.compile(r"T[1-9]\d*"), ("test_name", "given", "when", "then")),
    "outside": OUTSIDE,
}
BLOCKS = ",".join(block for block, _, _ in KINDS.values())
# An Android app's core design (`docs/plans/oneshot/7-android.md`, "S3"): modules and their contracts instead
# of an API, and every call that leaves the phone with its failure handling.
CORE = {
    "modules": ("oneshot-modules", re.compile(r"M[1-9]\d*"),
                ("name", "contract", "inputs", "outputs", "errors", "state", "stores", "calls")),
    "outside": OUTSIDE,
    "assumptions": KINDS["assumptions"],
    "tests": KINDS["tests"],
}
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
TEST_NAME = re.compile(r"test_[a-z0-9_]+")
MAX_ASSUMPTIONS, MAX_ROWS = 7, 12
# `oneshot_profile.PARTS`, as the sheet names them
PART_NAMES = {"backend": "백엔드", "frontend": "프런트엔드", "styling": "스타일", "data_store": "데이터 저장",
              "tests": "테스트", "deploy": "배포", "language": "언어", "ui": "화면", "core": "핵심 로직", "build": "빌드"}
KIND_NAMES = {"webapp": "웹앱", "android": "Android 앱", "desktop": "PC 앱"}   # `web/src/lib/oneshot.ts` KINDS


def part_name(part: str) -> str:
    """A stack part as the sheet names it; several kinds' own parts are `<kind>.<part>`."""

    kind, _, own = part.rpartition(".")
    return f"{KIND_NAMES.get(kind, kind)} {PART_NAMES.get(own, own)}" if kind else PART_NAMES.get(part, part)
PATH = re.compile(r"/[A-Za-z0-9_.{}/-]*")
# A path segment that is a sign-in role, whole: `/authors` is not one.
HANGUL = re.compile(r"[가-힣]")
AUTH_SEGMENT = re.compile(r"log-?(in|out)|sign-?(in|up|out)|auth|oauth2?|sessions?|tokens?|passwords?|register", re.I)

# The translationese check: counts and names, never rewrites. The patterns and the threshold are the knobs.
HUMANS = ("사람", "사용자", "회원", "관리자", "학생", "고객", "친구", "우리", "여러분", "직원", "참가자", "선생님",
          "아이", "부모", "손님", "작가", "독자", "멤버", "팀원", "가족")
PATTERNS = {
    "이중 피동": re.compile(r"[가-힣]*되어(?:지|진|져)[가-힣]*"),
    "번역투 연결": re.compile(r"[가-힣]*(?:에 대해서?|에 대한|에 있어서?|[을를] 통해서?)"),
    "명사형 끝맺음": re.compile(r"[가-힣]+ 것이다(?=[.!?\s]*$)", re.M),
    "사물에 붙은 들": re.compile(r"(?<![가-힣])([가-힣]+)들(?=[을를이가은는의에도과와]|\s|$)"),
    "괄호 속 영어": re.compile(r"[가-힣]+\s?\([A-Za-z][A-Za-z \-]*\)"),
}
THRESHOLD = 1   # matches in one string before it goes back to the model
CHAIN = 3       # `~의` in one clause
NOT_GENITIVE = ("회의", "주의", "동의", "정의", "논의", "문의", "의의", "강의", "합의", "협의", "건의")


def translationese(text: str) -> list[tuple[str, str]]:
    """`(pattern, span)` for every match in `text`."""

    found = []
    for clause in re.split(r"[,.;:!?\n]|그리고|하지만", text):
        chain = [w for w in clause.split() if re.fullmatch(r"[가-힣]+의", w) and w not in NOT_GENITIVE]
        if len(chain) >= CHAIN:
            found.append(("의 연쇄", " ".join(chain)))
    for name, pattern in PATTERNS.items():
        for m in pattern.finditer(text):
            # A one-syllable stem is a word that ends in `들` (`핸들`, `아들`), not a plural.
            if name == "사물에 붙은 들" and (m.group(1).endswith(HUMANS) or len(m.group(1)) < 2):
                continue
            found.append((name, m.group(0).strip()))
    return found


# -- The blocks --------------------------------------------------------------

def _text(item: dict, key: str, where: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: `{key}` 가 비었다")
    return " ".join(value.split())


def _strings(item: dict, key: str, where: str) -> list[str]:
    value = item.get(key) or []
    if not isinstance(value, list) or not all(isinstance(v, (str, int)) and not isinstance(v, bool) for v in value):
        raise ValueError(f"{where}: `{key}` 는 문자열 목록이어야 한다")
    return [str(v) for v in value]


def table(kind: str | list[str] = "webapp") -> dict:
    """The design's item kinds for a run's kind: an installed app's core (Android or PC), or a web app's backend.
    Several kinds share one core, and a web app among them adds its server's endpoints, which the others may call."""

    kinds = [kind] if isinstance(kind, str) else list(kind)
    if len(kinds) > 1:
        return {**CORE, "api": KINDS["api"]} if "webapp" in kinds else CORE
    return CORE if kinds[0] in ("android", "desktop") else KINDS


def blocks(kind: str | list[str] = "webapp") -> str:
    return ",".join(block for block, _, _ in table(kind).values())


EMPTY = {"outside"}   # a product that never leaves the device has no outside calls


def _items(name: str, value, kinds: dict = KINDS) -> list[dict]:
    block, rid, keys = kinds[name]
    if not isinstance(value, list) or not value and name not in EMPTY:
        raise ValueError(f"`{block}` 는 비지 않은 목록이어야 한다")
    out, seen = [], set()
    for n, item in enumerate(value, 1):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not rid.fullmatch(item["id"]):
            raise ValueError(f"`{block}` {n}번째 항목의 `id` 는 {rid.pattern[0]}1, {rid.pattern[0]}2 … 여야 한다")
        where = f"`{block}` {item['id']}"
        if item["id"] in seen:
            raise ValueError(f"{where}: id 가 겹친다")
        seen.add(item["id"])
        deps = item.get("depends_on", [])
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            raise ValueError(f"{where}: `depends_on` 은 id 목록이어야 한다")
        made = {"id": item["id"], "depends_on": list(dict.fromkeys(deps))}
        for key in keys:
            made[key] = item.get(key)
        out.append(_kind(name, made, where))
    return out


def _kind(name: str, item: dict, where: str) -> dict:
    if name == "entities":
        _text(item, "name", where)
        fields = item["fields"]
        if not isinstance(fields, list) or not fields or not all(
                isinstance(f, dict) and all(isinstance(f.get(k), str) and f[k].strip() for k in ("name", "type"))
                and isinstance(f.get("key", False), bool) for f in fields):
            raise ValueError(f"{where}: `fields` 마다 `name` 과 `type` 이 있고, `key` 는 true 나 false 다")
        item["fields"] = [{"name": f["name"], "type": f["type"], "rule": str(f.get("rule") or ""),
                           "key": bool(f.get("key"))} for f in fields]
        item["relations"] = _strings(item, "relations", where)
    elif name == "api":
        if item["method"] not in METHODS:
            raise ValueError(f"{where}: `method` 는 {', '.join(METHODS)} 중 하나다")
        if not isinstance(item["path"], str) or not PATH.fullmatch(item["path"]):
            raise ValueError(f"{where}: `path` 는 / 로 시작하고 글자, 숫자, `-_.{{}}/` 만 쓴다")
        item["does"] = _text(item, "does", where)
        for key in ("request", "response"):
            if not isinstance(item[key], dict):
                raise ValueError(f"{where}: `{key}` 는 필드 이름에서 형식으로 가는 객체다")
        item["errors"] = _strings(item, "errors", where)
    elif name == "flows":
        _text(item, "name", where)
        steps = item["steps"]
        if not isinstance(steps, list) or not steps or not all(
                isinstance(s, dict) and all(isinstance(s.get(k), str) and s[k].strip() for k in ("from", "to", "say"))
                for s in steps):
            raise ValueError(f"{where}: `steps` 마다 `from`, `to`, `say` 가 있어야 한다")
        item["steps"] = [{"from": " ".join(s["from"].split()), "to": " ".join(s["to"].split()),
                          "say": " ".join(s["say"].split()), "api": str(s.get("api") or "")} for s in steps]
    elif name == "modules":
        for key in ("name", "contract"):
            item[key] = _text(item, key, where)
        for key in ("inputs", "outputs"):
            if not isinstance(item[key], dict):
                raise ValueError(f"{where}: `{key}` 는 이름에서 형식으로 가는 객체다")
        for key in ("state", "stores"):   # empty when the module keeps or stores nothing
            if not isinstance(item[key] if item[key] is not None else "", str):
                raise ValueError(f"{where}: `{key}` 는 글줄이다 — 없으면 빈 글줄")
            item[key] = " ".join((item[key] or "").split())
        item["errors"], item["calls"] = _strings(item, "errors", where), _strings(item, "calls", where)
    elif name == "outside":
        for key in ("target", "does", "failure"):
            item[key] = _text(item, key, where)
        from . import oneshot_needs   # what the owner gives it; that module imports this one
        item["needs"] = oneshot_needs.parsed(item["needs"], where)
    elif name == "assumptions":
        for key in ("what", "why", "rejected"):
            item[key] = _text(item, key, where)
    elif name == "tests":
        if not isinstance(item["test_name"], str) or not TEST_NAME.fullmatch(item["test_name"]):
            raise ValueError(f"{where}: `test_name` 은 test_ 로 시작하는 파이썬 이름이다")
        for key in ("given", "when", "then"):
            item[key] = _text(item, key, where)
    return item


def parsed(found: dict, kind: str | list[str] = "webapp") -> dict:
    """The kind's design blocks, each shape-checked: `{item kind: [items]}`."""

    design, kinds = {}, table(kind)
    for name, (block, _, _) in kinds.items():
        if block not in found:
            raise ValueError(f"`{block}` 블록이 하나 있어야 한다")
        design[name] = _items(name, found[block], kinds)
    return design


def scenarios(reqs: list[dict]) -> list[dict]:
    """A scenario is a requirement with an observable result."""

    return [r for r in reqs if r.get("postconditions")]


def every(design: dict, reqs: list[dict]) -> dict[str, dict]:
    return {**{r["id"]: r for r in reqs}, **{i["id"]: i for items in design.values() for i in items}}


def linked(design: dict, reqs: list[dict]) -> dict[str, dict]:
    """Every id, its `depends_on` checked to name ids that exist and never to form a cycle."""

    known = every(design, reqs)
    for item in [i for items in design.values() for i in items]:
        if bad := [d for d in item["depends_on"] if d not in known or d == item["id"]]:
            raise ValueError(f"{item['id']}: `depends_on` 이 없는 id 를 가리킨다 — {', '.join(bad)}")
    if found := cycle(known):
        raise ValueError(f"`depends_on` 이 돌고 돈다 — {' → '.join(found)}")
    return known


def checked(design: dict, reqs: list[dict], glossary: list[dict], profile: dict, kind: str = "webapp") -> None:
    """The S3 checks; each refusal names what to fix."""

    if "modules" in design:   # an Android app's core, or the core several kinds share
        core_checked(design, reqs, glossary, profile)
        if not isinstance(kind, str) and len(kind) > 1:
            for k in kind:
                if foreign := [f"{m} → {r}" for m, r in asked_by(design, reqs, k) if k not in (by_id(reqs)[r].get("kinds") or kind)]:
                    raise ValueError(f"{KIND_NAMES.get(k, k)} 가 쓰는 모듈이 다른 종류의 권한에 기댄다 — {', '.join(foreign)}: "
                                     "그 종류의 얇은 플랫폼 모듈은 그 종류만 부른다")
        return
    linked(design, reqs)
    req_ids = {r["id"] for r in reqs}
    api_ids = {p["id"] for p in design["api"]}
    endpoints_checked(design, reqs, profile)
    for t in design["tests"]:
        if not req_ids & set(t["depends_on"]) or not api_ids & set(t["depends_on"]):
            raise ValueError(f"{t['id']}: 시험하는 요구와 부르는 엔드포인트를 `depends_on` 에 적어야 한다")
    for x in design["outside"]:
        if not req_ids & set(x["depends_on"]) or not api_ids & set(x["depends_on"]):
            raise ValueError(f"{x['id']}: 이 바깥 호출을 쓰는 요구와 그것을 부르는 엔드포인트를 `depends_on` 에 적어야 한다")
    from . import oneshot_needs   # it imports this module
    oneshot_needs.owed(design)   # one id per need, one need per `env`
    for f in design["flows"]:
        if missing := sorted({s["api"] for s in f["steps"] if s["api"]} - set(f["depends_on"])):
            raise ValueError(f"{f['id']}: 단계가 부르는 엔드포인트를 `depends_on` 에 적어야 한다 — {', '.join(missing)}")
        if bad := sorted({s["api"] for s in f["steps"] if s["api"]} - api_ids):
            raise ValueError(f"{f['id']}: 없는 엔드포인트를 부른다 — {', '.join(bad)}")
    for r in scenarios(reqs):
        if not any(r["id"] in p["depends_on"] for p in design["api"]):
            raise ValueError(f"{r['id']}: 이 시나리오를 맡은 엔드포인트가 없다")
        if not any(r["id"] in t["depends_on"] for t in design["tests"]):
            raise ValueError(f"{r['id']}: 이 시나리오를 확인할 테스트가 없다")
    words = [(e["id"], e["name"]) for e in design["entities"]] + \
            [(e["id"], f["name"]) for e in design["entities"] for f in e["fields"]] + \
            [(p["id"], t) for p in design["api"] for t in (p["path"], p["does"])] + \
            [(x["id"], t) for x in design["outside"] for t in (x["target"], x["does"])]
    avoided(words, glossary)


def endpoints_checked(design: dict, reqs: list[dict], profile: dict) -> None:
    """A web app's endpoints: one per route, each used by a requirement, and sign-in as the card says."""

    req_ids = {r["id"] for r in reqs}
    routes: dict[tuple[str, str], str] = {}
    for p in design["api"]:
        if (twin := routes.setdefault((p["method"], p["path"]), p["id"])) != p["id"]:
            raise ValueError(f"{twin}, {p['id']}: 같은 `{p['method']} {p['path']}` 다 — 하나로 합쳐라")
    for p in design["api"]:
        if not req_ids & set(p["depends_on"]):
            raise ValueError(f"{p['id']} {p['method']} {p['path']}: 어떤 요구에도 쓰이지 않는다 — 범위 밖이다")
    auth = [p for p in design["api"] if any(AUTH_SEGMENT.fullmatch(s) for s in p["path"].split("/"))]
    if profile.get("auth") == "none" and auth:
        raise ValueError(f"{auth[0]['id']}: 환경 카드는 로그인 없음인데 인증 엔드포인트가 있다")
    if profile.get("auth") in ("password", "oauth") and not auth:
        raise ValueError(f"환경 카드는 로그인 `{profile['auth']}` 인데 인증 엔드포인트가 없다")


def core_checked(design: dict, reqs: list[dict], glossary: list[dict], profile: dict | None = None) -> None:
    """A core's S3 checks: every module reached from a requirement, every call that leaves the app made by a
    module, and every scenario carried by a module and checked by a test. A web app among several kinds adds
    its endpoints, which a module may call."""

    linked(design, reqs)
    req_ids = {r["id"] for r in reqs}
    modules = {m["id"]: m for m in design["modules"]}
    outside = {x["id"] for x in design["outside"]}
    api = {p["id"] for p in design.get("api", [])}
    if "api" in design:
        endpoints_checked(design, reqs, profile or {})
    for m in modules.values():
        if bad := [c for c in m["calls"] if c not in modules and c not in outside and c not in api or c == m["id"]]:
            raise ValueError(f"{m['id']}: `calls` 는 다른 모듈(M)이나 바깥 호출(X){'이나 엔드포인트(P)' if api else ''}"
                             f"를 가리킨다 — {', '.join(bad)}")
    reached = {m for m, item in modules.items() if req_ids & set(item["depends_on"])}
    while more := {c for m in reached for c in modules[m]["calls"] if c in modules} - reached:
        reached |= more
    if loose := sorted(set(modules) - reached):
        raise ValueError(f"{', '.join(loose)}: 어떤 요구에도, 요구를 맡은 모듈의 호출에도 쓰이지 않는다 — 범위 밖이다")
    if unused := sorted(outside - {c for m in modules.values() for c in m["calls"]}):
        raise ValueError(f"{', '.join(unused)}: 이 바깥 호출을 부르는 모듈이 없다")
    from . import oneshot_needs   # it imports this module
    oneshot_needs.owed(design)   # one id per need, one need per `env`
    for t in design["tests"]:
        if not req_ids & set(t["depends_on"]) or not set(modules) & set(t["depends_on"]):
            raise ValueError(f"{t['id']}: 시험하는 요구와 그 계약을 확인하는 모듈을 `depends_on` 에 적어야 한다")
    for r in scenarios(reqs):
        if not any(r["id"] in m["depends_on"] for m in modules.values()):
            raise ValueError(f"{r['id']}: 이 시나리오를 맡은 모듈이 없다")
        if not any(r["id"] in t["depends_on"] for t in design["tests"]):
            raise ValueError(f"{r['id']}: 이 시나리오를 확인할 테스트가 없다")
    avoided([(m["id"], t) for m in modules.values() for t in (m["name"], m["contract"])] +
            [(x["id"], t) for x in design["outside"] for t in (x["target"], x["does"])] +
            [(p["id"], t) for p in design.get("api", []) for t in (p["path"], p["does"])], glossary)


def _spans(word: str, text: str, edged: bool) -> list[tuple[int, int]]:
    """Every place `word` stands in `text`, overlapping ones too. `edged`: the word stands whole, not inside a
    longer name (`AutoLLM`, `auto_llm_gateway`, `LLM도구Backend`, `préLLM`, `llm-gateway-legacy`). Hangul after it
    is a particle or a compound (`LLM Gateway로`, `변환을`, `변환기`), in a name as in prose: a Korean name is a
    compound, and refusing it would refuse the approved term itself. Hangul before it is a compound only when the
    word starts in Hangul."""

    def runs_on(i: int, step: int) -> bool:   # does `text[i]` carry a name on, looking away from the word
        j = i
        while 0 <= j < len(text) and text[j] == "-":   # hyphens join only when a name character stands past
            j += step                                    # them all (`변환- 결과` is prose, `a--b` one name)
        return 0 <= j < len(text) and _name_char(text[j])

    out = []
    for m in re.finditer(f"(?=({re.escape(word)}))", text):
        s, e = m.start(), m.start() + len(word)
        if edged and (runs_on(s - 1, -1) and not (HANGUL.match(text[s - 1]) and HANGUL.match(word[0])) or
                      runs_on(e, 1) and not HANGUL.match(text[e])):
            continue
        out.append((s, e))
    return out


def _name_char(c: str) -> bool:
    return c.isalnum() or c == "_" or unicodedata.category(c).startswith("M")


def avoided(words: list[tuple[str, str]], glossary: list[dict]) -> None:
    """Refuses the first glossary `avoid` word any `(id, text)` uses, each field checked on its own. An
    occurrence one whole glossary term covers is not a use: one entry may avoid a word another entry defines
    (`번역` avoiding `변환`), and using that term is never using the avoided word. Everything is compared in
    NFC, so a decomposed accent reads as the letter it makes."""

    def fold(s: str) -> str:
        return unicodedata.normalize("NFC", s).lower()

    terms = {fold(g["term"]) for g in glossary}

    def uses(text: str, avoid: str) -> bool:
        text, avoid = fold(text), fold(avoid)
        cover = [span for t in terms if avoid in t for span in _spans(t, text, True)]
        return any(not any(ts <= s and e <= te for ts, te in cover) for s, e in _spans(avoid, text, False))

    for g in glossary:
        for avoid in g.get("avoid") or []:
            if hits := list(dict.fromkeys(i for i, text in words if uses(text, avoid))):
                raise ValueError(f"{', '.join(hits)}: 용어집의 `{g['term']}` 대신 쓰지 않기로 한 `{avoid}` 를 썼다")


def cycle(known: dict[str, dict]) -> list[str]:
    """A cycle through the requirements and the design items together, or empty."""

    edges = {i: item.get("depends_on", []) for i, item in known.items()}
    state: dict[str, int] = {}

    def visit(node: str, path: list[str]) -> list[str]:
        if state.get(node) == 1:
            return path[path.index(node):] + [node]
        if state.get(node) == 2 or node not in edges:
            return []
        state[node] = 1
        for nxt in edges[node]:
            if found := visit(nxt, [*path, node]):
                return found
        state[node] = 2
        return []

    for node in edges:
        if found := visit(node, []):
            return found
    return []


def strings(design: dict, reqs: list[dict], glossary: list[dict]) -> list[tuple[str, str]]:
    """`(where, text)` for every string a person reads on the sheet."""

    out = [(r["id"], t) for r in reqs for t in (r["text"], *r.get("postconditions", []))]
    out += [(r["id"], r["capability"][k]) for r in reqs if r.get("capability") for k in ("prompt", "risk")]
    out += [(p["id"], p["does"]) for p in design.get("api", [])]
    out += [(m["id"], m["contract"]) for m in design.get("modules", [])]
    out += [(x["id"], x[k]) for x in design.get("outside", []) for k in ("does", "failure")]
    out += [(a["id"], a[k]) for a in design["assumptions"] for k in ("what", "why", "rejected")]
    out += [(f["id"], f["name"]) for f in design.get("flows", [])] + [(f["id"], s["say"]) for f in design.get("flows", [])
                                                                     for s in f["steps"]]
    out += [(t["id"], t[k]) for t in design["tests"] for k in ("given", "when", "then")]
    out += [(f"용어 {g['term']}", g["meaning"]) for g in glossary]
    return out


def soft(design: dict, reqs: list[dict], glossary: list[dict]) -> list[str]:
    """What sends the blocks back once but never stops the run: the length budget and translationese."""

    found = []
    if len(design["assumptions"]) > MAX_ASSUMPTIONS:
        found.append(f"가정이 {len(design['assumptions'])}개다 — {MAX_ASSUMPTIONS}개 이하로 합치거나 미뤄라")
    for name, what in (("modules", "모듈이"), ("api", "엔드포인트가")):   # several kinds' core may have both
        if len(rows := design.get(name, [])) > MAX_ROWS:
            found.append(f"{what} {len(rows)}개다 — {MAX_ROWS}개 이하로 합치거나 미뤄라")
    for where, text in strings(design, reqs, glossary):
        hits = translationese(text)
        if len(hits) >= THRESHOLD:
            found.append(f"{where} 번역체: " + "; ".join(f"{name} `{span}`" for name, span in hits))
    return found


def dependents(design: dict, reqs: list[dict], start: set[str]) -> set[str]:
    """`start` and every item whose `depends_on` reaches one of them, transitively. A module's `calls`
    link it to what it calls both ways: a changed contract moves its callers, and a changed caller may
    need its callees changed. A web server's outside call and the endpoint that makes it are linked alike."""

    items = list(every(design, reqs).values())
    calls = [(i["id"], c) for i in items for c in i.get("calls", [])]
    if "modules" not in design:   # a web server: the endpoint an outside call names is the one that makes it
        api = {p["id"] for p in design.get("api", [])}
        calls += [(d, x["id"]) for x in design.get("outside", []) for d in x["depends_on"] if d in api]
    reached = set(start)
    while True:
        more = {i["id"] for i in items if set(i.get("depends_on", [])) & reached}
        more |= {b for a, b in calls if a in reached} | {a for a, b in calls if b in reached}
        more -= reached
        if not more:
            return reached
        reached |= more


def decidable(design: dict, reqs: list[dict]) -> list[str]:
    """Every item on the sheet that needs the owner's decision, folded or not: every requirement,
    a scenario or not, and every design item."""

    return [r["id"] for r in reqs] + [i["id"] for items in design.values() for i in items]


# -- The sheet ----------------------------------------------------------------

def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _actor(text: str) -> str:
    """A Mermaid participant: letters, digits and Hangul only."""

    return re.sub(r"[^\w가-힣]", "_", text) or "_"


def _said(text: str) -> str:
    """A Mermaid message: one line, no statement separator, no fence."""

    return " ".join(text.split()).replace(";", ",").replace("#", "＃").replace("`", "'")


def _shape(value: dict) -> str:
    return ", ".join(f"`{k}`: {v}" for k, v in value.items()) or "없음"


def marker(o: dict):
    """What the sheet writes after an item's id: whether the owner's last edit reached it."""

    marks = (o.get("r1") or {}).get("marks") or {}
    tag = {"edited": " (고친 항목)", "consequence": " (따라 바뀜)", "again": " (다시 승인)"}
    return lambda i: tag.get(marks.get(i), "")


def opening(spec: dict, title: str, mark) -> list[str]:
    """The sheet's title, the sentence, the stack S2 settled and the agent's assumptions."""

    o = spec["oneshot"]
    design = o["design"]
    lines = [f"# {title} — {spec['repo']}", "", f"> {o['sentence']}", "", o["intent"]["restated"], ""]
    stack = {s["part"]: s for s in o.get("stack") or []}
    if stack:   # read-only here: S2 settled it, and another stack means a new run
        lines += ["## 스택", "", "요구 대화에서 고른 것이다. 바꾸려면 새 실행을 시작한다.", "",
                  "| 부분 | 고른 것 | 이유 |", "| --- | --- | --- |"]
        lines += [f"| {part_name(p)} | {_cell(s['choice'])} | {_cell(s['why'])} |" for p, s in stack.items()]
        lines.append("")
    lines += ["## 에이전트가 정한 가정", ""]
    shown = design["assumptions"][:MAX_ASSUMPTIONS]
    lines += [f"- **{a['id']}**{mark(a['id'])} {a['what']} — 이유: {a['why']} / 버린 안: {a['rejected']}" for a in shown]
    if len(design["assumptions"]) > MAX_ASSUMPTIONS:
        lines.append(f"- 그 밖의 가정 {len(design['assumptions']) - MAX_ASSUMPTIONS}개는 아래 접힌 부분에 있다")
    return lines


def tested_scenarios(design: dict, reqs: list[dict], mark) -> list[str]:
    lines = ["", "## 시나리오와 확인할 테스트", ""]
    for r in scenarios(reqs):
        tests = [t["test_name"] for t in design["tests"] if r["id"] in t["depends_on"]]
        lines.append(f"- **{r['id']}**{mark(r['id'])} {r['text']} → {', '.join(f'`{t}`' for t in tests)}")
    return lines + [f"- **{r['id']}**{mark(r['id'])} {r['text']} (지켜야 할 조건, 시나리오 아님)"
                    for r in reqs if not r.get("postconditions")]


def sheet(spec: dict) -> str:
    """The R1 sheet in Markdown, sections in the order the plan fixes."""

    o = spec["oneshot"]
    if "modules" in o["design"]:
        return core_sheet(spec)
    design, reqs, glossary, profile = o["design"], spec.get("requirements") or [], o.get("glossary") or [], o["profile"]
    mark = marker(o)
    lines = opening(spec, "백엔드 설계", mark)
    stack = {s["part"]: s for s in o.get("stack") or []}
    keys = [f"{e['name']}.{f['name']}" for e in design["entities"] for f in e["fields"] if f["key"]]
    lines += ["", "## 나중에 되돌리기 어려운 것", "",
              f"- 저장 키: {', '.join(f'`{k}`' for k in keys) or '없음'} (저장소: {_cell(stack['data_store']['choice']) if 'data_store' in stack else '정하지 않음'})",
              f"- 로그인: `{profile.get('auth')}`"]
    if profile.get("visibility") == "public":
        lines.append("- 공개 저장소다: 코드와 설계 문서가 모두에게 보인다")
    lines += ["", "## 데이터와 API", "", "```mermaid", *mermaid_er(design), "```", "",
              "| id | 메서드 | 경로 | 하는 일 |", "| --- | --- | --- | --- |"]
    lines += [f"| {p['id']}{mark(p['id'])} | {p['method']} | `{_cell(p['path'])}` | {_cell(p['does'])} |"
              for p in design["api"][:MAX_ROWS]]
    if len(design["api"]) > MAX_ROWS:
        lines.append(f"\n그 밖의 엔드포인트 {len(design['api']) - MAX_ROWS}개는 아래 접힌 부분에 있다")
    from . import oneshot_needs   # it imports this module
    lines += oneshot_needs.leaving(design, "서버", mark)   # a design from before the web had outside calls has none
    lines += tested_scenarios(design, reqs, mark)
    lines += ["", "<details><summary>흐름, 요청·응답, 용어집</summary>", ""]
    for f in design["flows"]:
        lines += [f"### {f['id']}{mark(f['id'])} {f['name']}", "", "```mermaid", "sequenceDiagram",
                  *[f"    {_actor(s['from'])}->>{_actor(s['to'])}: {_said(s['say'])}" for s in f["steps"]], "```", ""]
    for n, p in enumerate(design["api"]):   # an endpoint past the table shows what it does here
        tabled = "" if n < MAX_ROWS else f"{p['does']} / "
        lines.append(f"- **{p['id']}**{mark(p['id'])} `{p['method']} {p['path']}` {tabled}요청 {_shape(p['request'])} / "
                     f"응답 {_shape(p['response'])} / 오류 {', '.join(p['errors']) or '없음'}")
    return "\n".join(lines + folded_tail(design, glossary, mark))


def folded_tail(design: dict, glossary: list[dict], mark) -> list[str]:
    """The folded part's end: the assumptions past the budget, the planned tests and the glossary."""

    lines = [f"- **{a['id']}**{mark(a['id'])} {a['what']} — 이유: {a['why']} / 버린 안: {a['rejected']}"
             for a in design["assumptions"][MAX_ASSUMPTIONS:]]
    lines += ["", *[f"- **{t['id']}**{mark(t['id'])} `{t['test_name']}`: {t['given']} / {t['when']} / {t['then']}"
                    for t in design["tests"]]]
    return lines + ["", *[f"- {g['term']}: {g['meaning']}" for g in glossary], "", "</details>", ""]


def core_sheet(spec: dict) -> str:
    """An installed app's R1 sheet (Android or PC, or several kinds): the module table where a web app has its
    API table, the platform capabilities with the prompt each causes and its distribution risk, and every call
    leaving the device."""

    o = spec["oneshot"]
    design, reqs, glossary = o["design"], spec.get("requirements") or [], o.get("glossary") or []
    mark = marker(o)
    lines = opening(spec, "핵심 설계", mark)
    stored = [f"{m['name']}: {m['stores']}" for m in design["modules"] if m["stores"]]
    abilities = [r for r in reqs if r.get("capability")]
    intent = o.get("intent") or {}
    kinds = intent.get("kinds") or [intent.get("kind", "android")]   # a record from before kinds has its one `kind`
    several = len(kinds) > 1
    device, device_out = ("기기", "앱") if several else ("PC", "PC") if kinds == ["desktop"] else ("폰", "폰")
    lines += ["", "## 나중에 되돌리기 어려운 것", "",
              f"- {device}에 저장하는 것: {'; '.join(_cell(s) for s in stored) or '없음'}",
              f"- 플랫폼 권한: {', '.join(f'`{r['capability']['permission']}`' for r in abilities) or '없음'}"]
    if "api" in design:
        lines.append(f"- 로그인: `{o['profile'].get('auth')}`")
    if o["profile"].get("visibility") == "public":
        lines.append("- 공개 저장소다: 코드와 설계 문서가 모두에게 보인다")
    if several:   # what each kind the owner picked uses of the one core
        lines += ["", "## 종류마다 쓰는 것", ""]
        for kind in kinds:
            uses = used_by(design, reqs, kind)
            # A capability the kind declares, and one a module it uses depends on: the check makes them the same.
            mine = {r for _, r in asked_by(design, reqs, kind)} | {r["id"] for r in abilities if kind in (r.get("kinds") or kinds)}
            asks = [r["capability"]["permission"] for r in abilities if r["id"] in mine]
            lines.append(f"- {KIND_NAMES.get(kind, kind)}: 모듈 {', '.join(uses) or '없음'} / 권한 "
                         f"{', '.join(f'`{_cell(p)}`' for p in asks) or '없음'}"
                         + (f" / 엔드포인트 {', '.join(p['id'] for p in design['api']) or '없음'}"
                            if kind == "webapp" and "api" in design else ""))
    if abilities:
        lines += ["", "## 플랫폼 권한", "", "| id | 권한 | 사용자에게 뜨는 안내 | 배포 위험 |", "| --- | --- | --- | --- |"]
        lines += [f"| {r['id']}{mark(r['id'])} | `{_cell(r['capability']['permission'])}` | "
                  f"{_cell(r['capability']['prompt'])} | {_cell(r['capability']['risk'])} |" for r in abilities]
    lines += ["", "## 모듈", "", "| id | 모듈 | 계약 | 부르는 것 |", "| --- | --- | --- | --- |"]
    lines += [f"| {m['id']}{mark(m['id'])} | {_cell(m['name'])} | {_cell(m['contract'])} | {', '.join(m['calls']) or '없음'} |"
              for m in design["modules"][:MAX_ROWS]]
    if len(design["modules"]) > MAX_ROWS:
        lines.append(f"\n그 밖의 모듈 {len(design['modules']) - MAX_ROWS}개는 아래 접힌 부분에 있다")
    if design.get("api"):   # a web app's server among several kinds
        lines += ["", "## 서버 API", "", "| id | 메서드 | 경로 | 하는 일 |", "| --- | --- | --- | --- |"]
        lines += [f"| {p['id']}{mark(p['id'])} | {p['method']} | `{_cell(p['path'])}` | {_cell(p['does'])} |"
                  for p in design["api"]]
    from . import oneshot_needs   # it imports this module
    lines += oneshot_needs.leaving(design, device_out, mark)
    lines += tested_scenarios(design, reqs, mark)
    lines += ["", "<details><summary>모듈의 입력·출력·상태, 테스트, 용어집</summary>", ""]
    for n, m in enumerate(design["modules"]):   # a module past the table shows its contract and calls here
        tabled = "" if n < MAX_ROWS else f"계약 {m['contract']} / 부르는 것 {', '.join(m['calls']) or '없음'} / "
        lines.append(f"- **{m['id']}**{mark(m['id'])} {m['name']}: {tabled}입력 {_shape(m['inputs'])} / 출력 {_shape(m['outputs'])} / "
                     f"오류 {', '.join(m['errors']) or '없음'} / 상태 {m['state'] or '없음'}")
    lines += [f"- **{p['id']}**{mark(p['id'])} `{p['method']} {p['path']}` 요청 {_shape(p['request'])} / "
              f"응답 {_shape(p['response'])} / 오류 {', '.join(p['errors']) or '없음'}" for p in design.get("api", [])]
    return "\n".join(lines + folded_tail(design, glossary, mark))


def by_id(reqs: list[dict]) -> dict[str, dict]:
    return {r["id"]: r for r in reqs}


def asked_by(design: dict, reqs: list[dict], kind: str) -> list[tuple[str, str]]:
    """`(module, requirement)` for every platform capability a module this kind uses depends on, directly or
    through the requirements and design items its `depends_on` reaches."""

    known, out = every(design, reqs), []
    for m in sorted(reach(design, reqs, kind)):
        reached, edge = set(), list(known[m]["depends_on"])
        while edge:
            if (i := edge.pop()) in known and i not in reached:
                reached.add(i)
                edge += known[i].get("depends_on") or []
        out += [(m, r) for r in sorted(reached) if known[r].get("capability")]
    return out


def reach(design: dict, reqs: list[dict], kind: str) -> set[str]:
    """The modules and endpoints one kind uses: the modules carrying a requirement that applies to it, and for a web
    app every endpoint, since its server runs them whoever calls; then everything they call or are built on
    (`depends_on`), module or endpoint, transitively. Another kind reaches an endpoint only by calling it."""

    mine = {r["id"] for r in reqs if kind in (r.get("kinds") or [kind])}
    parts = {i["id"]: i for i in [*design["modules"], *design.get("api", [])]}
    edge = [m["id"] for m in design["modules"] if mine & set(m["depends_on"])]
    reached, edge = set(), edge + ([p["id"] for p in design.get("api", [])] if kind == "webapp" else [])
    while edge:
        if (i := edge.pop()) in parts and i not in reached:
            reached.add(i)
            edge += [*parts[i].get("calls", []), *parts[i]["depends_on"]]
    return reached


def used_by(design: dict, reqs: list[dict], kind: str) -> list[str]:
    """The modules one kind uses, in the design's order (`reach`)."""

    reached = reach(design, reqs, kind)
    return [m["id"] for m in design["modules"] if m["id"] in reached]


def core(spec: dict) -> str:
    """`docs/design/core.json`: the modules and the calls leaving the app, which the contract tests are written
    from, and a web app's endpoints when several kinds share the core."""

    design = spec["oneshot"]["design"]
    return json.dumps({k: design[k] for k in ("modules", "outside", "api") if k in design},
                      ensure_ascii=False, indent=2) + "\n"


def mermaid_er(design: dict) -> list[str]:
    def name(e: dict) -> str:
        return re.sub(r"\W", "_", e["name"]) or e["id"]

    out = ["erDiagram"]
    for e in design["entities"]:
        out += [f"    {name(e)} {{", *[f"        {re.sub(r'\W', '_', f['type'])} {re.sub(r'\W', '_', f['name'])}"
                                        f"{' PK' if f['key'] else ''}" for f in e["fields"]], "    }"]
    ids = {e["id"]: name(e) for e in design["entities"]}
    for e in design["entities"]:
        for other in e["depends_on"]:
            if other in ids:
                out.append(f"    {name(e)} }}o--|| {ids[other]} : \"\"")
    return out


def openapi(spec: dict) -> str:
    """`docs/design/api.yaml`, written as JSON: every JSON document is valid YAML."""

    o = spec["oneshot"]
    paths: dict[str, dict] = {}
    for p in o["design"]["api"]:
        schema = lambda shape: {"type": "object", "properties": {k: {"description": str(v)}  # noqa: E731
                                                                 for k, v in shape.items()}}
        paths.setdefault(p["path"], {})[p["method"].lower()] = {
            "operationId": p["id"], "summary": p["does"],
            **({"requestBody": {"content": {"application/json": {"schema": schema(p["request"])}}}}
               if p["request"] else {}),
            "responses": {"200": {"description": "OK", "content": {"application/json": {"schema": schema(p["response"])}}},
                          **{e: {"description": e} for e in p["errors"] if re.fullmatch(r"[1-5]\d\d", e)}}}
    document = {"openapi": "3.1.0", "info": {"title": spec["repo"], "version": "0.1.0"}, "paths": paths}
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def _texts(value, what: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError(f"{what} 는 비지 않은 글줄의 목록이어야 한다")
    return [" ".join(v.split()) for v in value]


def requirements(value) -> list[dict]:
    """`value` checked and normalized, raising `ValueError` that names the item and the key."""

    if not isinstance(value, list):
        raise ValueError("`requirements` 는 목록이어야 한다")
    out, seen = [], set()
    for n, item in enumerate(value, 1):
        if not isinstance(item, dict):
            raise ValueError(f"`requirements` {n}번째 항목은 객체여야 한다")
        rid = item.get("id")
        if not isinstance(rid, str) or not RID.fullmatch(rid):
            raise ValueError(f"`requirements` {n}번째 항목의 `id` 는 R1, R2 … 여야 한다 — {rid!r}")
        where = f"`requirements` {rid}"
        if unknown := sorted(set(item) - REQUIREMENT_KEYS):
            raise ValueError(f"{where}: 모르는 키 — {', '.join(unknown)}")
        if rid in seen:
            raise ValueError(f"{where}: id 가 겹친다")
        seen.add(rid)
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{where}: `text` 가 비었다")
        source = item.get("source")
        if not isinstance(source, dict) or set(source) != {"kind", "locator"} or source["kind"] not in SOURCES \
                or not isinstance(source["locator"], str) or not source["locator"].strip():
            raise ValueError(f"{where}: `source` 는 {{kind: {'|'.join(SOURCES)}, locator}} 여야 한다")
        if item.get("status") not in STATUSES:
            raise ValueError(f"{where}: `status` 는 {', '.join(STATUSES)} 중 하나다 — {item.get('status')!r}")
        if "global" in item and not isinstance(item["global"], bool):
            raise ValueError(f"{where}: `global` 은 참·거짓이다")
        verify = item.get("verify") or []
        if not isinstance(verify, list) or not all(
                isinstance(v, dict) and set(v) == {"item", "expected"}
                and all(isinstance(v[k], str) and v[k].strip() for k in v) for v in verify):
            raise ValueError(f"{where}: `verify` 항목마다 `item` 과 `expected` 가 있어야 한다")
        made = {"id": rid, "text": " ".join(text.split()), "source": {"kind": source["kind"],
                "locator": " ".join(source["locator"].split())}, "status": item["status"]}
        for key in (*CONDITIONS, "depends_on", "context", "kinds"):   # `kinds`: the run's kinds it applies to (plan 9b)
            if key in item:
                made[key] = _texts(item[key], f"{where}: `{key}`")
        if verify:
            made["verify"] = [{k: " ".join(v[k].split()) for k in ("item", "expected")} for v in verify]
        if (ability := item.get("capability")) is not None:
            if not isinstance(ability, dict) or set(ability) != set(CAPABILITY_KEYS) or not all(
                    isinstance(ability[k], str) and ability[k].strip() for k in CAPABILITY_KEYS):
                raise ValueError(f"{where}: `capability` 는 {{{', '.join(CAPABILITY_KEYS)}}} 를 모두 채운 객체다")
            made["capability"] = {k: " ".join(ability[k].split()) for k in CAPABILITY_KEYS}
        if item.get("global"):
            made["global"] = True
        out.append(made)
    for r in out:
        if bad := [t for t in r.get("depends_on", []) if t not in seen or t == r["id"]]:
            raise ValueError(f"`requirements` {r['id']}: `depends_on` 이 정의되지 않은 id 를 가리킨다 — {', '.join(bad)}")
    return out


def requirement_block(value) -> tuple[list[dict], list[dict]]:
    """`oneshot-spec`: `{requirements, glossary}`, checked."""

    if not isinstance(value, dict):
        raise ValueError("`oneshot-spec` 는 `requirements` 와 `glossary` 를 가진 객체다")
    reqs = requirements(value.get("requirements"))
    glossary = value.get("glossary") or []
    if not isinstance(glossary, list) or not all(
            isinstance(g, dict) and all(isinstance(g.get(k), str) and g[k].strip() for k in ("term", "meaning"))
            for g in glossary):
        raise ValueError("`glossary` 항목마다 `term` 과 `meaning` 이 있어야 한다")
    glossary = [{"term": g["term"].strip(), "meaning": " ".join(g["meaning"].split()),
                 "avoid": [a for a in _strings(g, "avoid", f"용어 {g['term']}") if a.strip()]} for g in glossary]
    return reqs, glossary
