"""The oneshot backend design: its blocks checked, the R1 sheet rendered from them, and what an edit reaches.

`docs/plans/oneshot/3-spec-backend.md` ("S3", "R1") is the design. Pure
functions: no records, no sessions. The sheet is rendered by code from the
stored blocks, so the sheet and the record cannot disagree.
"""

from __future__ import annotations

import json
import re

# A requirement: where it came from, whether it is decided, and its conditions.
SOURCES = ("original", "policy", "repository", "decision")
STATUSES = ("confirmed", "assumed", "undecided", "not_applicable")
CONDITIONS = ("preconditions", "postconditions", "invariants", "failures")
REQUIREMENT_KEYS = frozenset({"id", "text", "source", "status", *CONDITIONS, "depends_on", "context", "verify", "global"})
RID = re.compile(r"R[1-9]\d*")

# Each design item kind: its block, its id pattern and the keys it carries.
KINDS = {
    "entities": ("oneshot-entities", re.compile(r"E[1-9]\d*"), ("name", "fields", "relations")),
    "api": ("oneshot-api", re.compile(r"P[1-9]\d*"), ("method", "path", "does", "request", "response", "errors")),
    "flows": ("oneshot-flows", re.compile(r"F[1-9]\d*"), ("name", "steps")),
    "assumptions": ("oneshot-assumptions", re.compile(r"A[1-9]\d*"), ("what", "why", "rejected")),
    "tests": ("oneshot-tests", re.compile(r"T[1-9]\d*"), ("test_name", "given", "when", "then")),
}
BLOCKS = ",".join(block for block, _, _ in KINDS.values())
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
TEST_NAME = re.compile(r"test_[a-z0-9_]+")
MAX_ASSUMPTIONS, MAX_ROWS = 7, 12
PATH = re.compile(r"/[A-Za-z0-9_.{}/-]*")
# A path segment that is a sign-in role, whole: `/authors` is not one.
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


def _items(name: str, value) -> list[dict]:
    block, rid, keys = KINDS[name]
    if not isinstance(value, list) or not value:
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
    elif name == "assumptions":
        for key in ("what", "why", "rejected"):
            item[key] = _text(item, key, where)
    elif name == "tests":
        if not isinstance(item["test_name"], str) or not TEST_NAME.fullmatch(item["test_name"]):
            raise ValueError(f"{where}: `test_name` 은 test_ 로 시작하는 파이썬 이름이다")
        for key in ("given", "when", "then"):
            item[key] = _text(item, key, where)
    return item


def parsed(found: dict) -> dict:
    """The five blocks, each shape-checked: `{kind: [items]}`."""

    design = {}
    for name, (block, _, _) in KINDS.items():
        if block not in found:
            raise ValueError(f"`{block}` 블록이 하나 있어야 한다")
        design[name] = _items(name, found[block])
    return design


def scenarios(reqs: list[dict]) -> list[dict]:
    """A scenario is a requirement with an observable result."""

    return [r for r in reqs if r.get("postconditions")]


def every(design: dict, reqs: list[dict]) -> dict[str, dict]:
    return {**{r["id"]: r for r in reqs}, **{i["id"]: i for kind in KINDS for i in design.get(kind, [])}}


def checked(design: dict, reqs: list[dict], glossary: list[dict], profile: dict) -> None:
    """The S3 checks; each refusal names what to fix."""

    known = every(design, reqs)
    req_ids = {r["id"] for r in reqs}
    api_ids = {p["id"] for p in design["api"]}
    for item in [i for kind in KINDS for i in design[kind]]:
        if bad := [d for d in item["depends_on"] if d not in known or d == item["id"]]:
            raise ValueError(f"{item['id']}: `depends_on` 이 없는 id 를 가리킨다 — {', '.join(bad)}")
    if found := cycle(known):
        raise ValueError(f"`depends_on` 이 돌고 돈다 — {' → '.join(found)}")
    routes: dict[tuple[str, str], str] = {}
    for p in design["api"]:
        if (twin := routes.setdefault((p["method"], p["path"]), p["id"])) != p["id"]:
            raise ValueError(f"{twin}, {p['id']}: 같은 `{p['method']} {p['path']}` 다 — 하나로 합쳐라")
    for p in design["api"]:
        if not req_ids & set(p["depends_on"]):
            raise ValueError(f"{p['id']} {p['method']} {p['path']}: 어떤 요구에도 쓰이지 않는다 — 범위 밖이다")
    for t in design["tests"]:
        if not req_ids & set(t["depends_on"]) or not api_ids & set(t["depends_on"]):
            raise ValueError(f"{t['id']}: 시험하는 요구와 부르는 엔드포인트를 `depends_on` 에 적어야 한다")
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
            [(p["id"], f"{p['path']} {p['does']}") for p in design["api"]]
    for g in glossary:
        for avoid in g.get("avoid") or []:
            if hits := [i for i, text in words if avoid.lower() in text.lower()]:
                raise ValueError(f"{', '.join(hits)}: 용어집의 `{g['term']}` 대신 쓰지 않기로 한 `{avoid}` 를 썼다")
    auth = [p for p in design["api"] if any(AUTH_SEGMENT.fullmatch(s) for s in p["path"].split("/"))]
    if profile.get("auth") == "none" and auth:
        raise ValueError(f"{auth[0]['id']}: 환경 카드는 로그인 없음인데 인증 엔드포인트가 있다")
    if profile.get("auth") in ("password", "oauth") and not auth:
        raise ValueError(f"환경 카드는 로그인 `{profile['auth']}` 인데 인증 엔드포인트가 없다")


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
    out += [(p["id"], p["does"]) for p in design["api"]]
    out += [(a["id"], a[k]) for a in design["assumptions"] for k in ("what", "why", "rejected")]
    out += [(f["id"], f["name"]) for f in design["flows"]] + [(f["id"], s["say"]) for f in design["flows"]
                                                              for s in f["steps"]]
    out += [(t["id"], t[k]) for t in design["tests"] for k in ("given", "when", "then")]
    out += [(f"용어 {g['term']}", g["meaning"]) for g in glossary]
    return out


def soft(design: dict, reqs: list[dict], glossary: list[dict]) -> list[str]:
    """What sends the blocks back once but never stops the run: the length budget and translationese."""

    found = []
    if len(design["assumptions"]) > MAX_ASSUMPTIONS:
        found.append(f"가정이 {len(design['assumptions'])}개다 — {MAX_ASSUMPTIONS}개 이하로 합치거나 미뤄라")
    if len(design["api"]) > MAX_ROWS:
        found.append(f"엔드포인트가 {len(design['api'])}개다 — {MAX_ROWS}개 이하로 합치거나 미뤄라")
    for where, text in strings(design, reqs, glossary):
        hits = translationese(text)
        if len(hits) >= THRESHOLD:
            found.append(f"{where} 번역체: " + "; ".join(f"{name} `{span}`" for name, span in hits))
    return found


def dependents(design: dict, reqs: list[dict], start: set[str]) -> set[str]:
    """`start` and every item whose `depends_on` reaches one of them, transitively."""

    items = list(every(design, reqs).values())
    reached = set(start)
    while True:
        more = {i["id"] for i in items if i["id"] not in reached and set(i.get("depends_on", [])) & reached}
        if not more:
            return reached
        reached |= more


def decidable(design: dict, reqs: list[dict]) -> list[str]:
    """Every item on the sheet that needs the owner's decision, folded or not: every requirement,
    a scenario or not, and every design item."""

    return [r["id"] for r in reqs] + [i["id"] for kind in KINDS for i in design[kind]]


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


def sheet(spec: dict) -> str:
    """The R1 sheet in Markdown, sections in the order the plan fixes."""

    o = spec["oneshot"]
    design, reqs, glossary, profile = o["design"], spec.get("requirements") or [], o.get("glossary") or [], o["profile"]
    marks = (o.get("r1") or {}).get("marks") or {}
    tag = {"edited": " (고친 항목)", "consequence": " (따라 바뀜)", "again": " (다시 승인)"}
    mark = lambda i: tag.get(marks.get(i), "")   # noqa: E731
    lines = [f"# 백엔드 설계 — {spec['repo']}", "", f"> {o['sentence']}", "", o["intent"]["restated"], ""]
    lines += ["## 에이전트가 정한 가정", ""]
    shown = design["assumptions"][:MAX_ASSUMPTIONS]
    lines += [f"- **{a['id']}**{mark(a['id'])} {a['what']} — 이유: {a['why']} / 버린 안: {a['rejected']}" for a in shown]
    if len(design["assumptions"]) > MAX_ASSUMPTIONS:
        lines.append(f"- 그 밖의 가정 {len(design['assumptions']) - MAX_ASSUMPTIONS}개는 아래 접힌 부분에 있다")
    keys = [f"{e['name']}.{f['name']}" for e in design["entities"] for f in e["fields"] if f["key"]]
    lines += ["", "## 나중에 되돌리기 어려운 것", "",
              f"- 저장 키: {', '.join(f'`{k}`' for k in keys) or '없음'} (저장소 `{profile.get('data_store')}`)",
              f"- 로그인: `{profile.get('auth')}`"]
    if profile.get("visibility") == "public":
        lines.append("- 공개 저장소다: 코드와 설계 문서가 모두에게 보인다")
    lines += ["", "## 데이터와 API", "", "```mermaid", *mermaid_er(design), "```", "",
              "| id | 메서드 | 경로 | 하는 일 |", "| --- | --- | --- | --- |"]
    lines += [f"| {p['id']}{mark(p['id'])} | {p['method']} | `{_cell(p['path'])}` | {_cell(p['does'])} |"
              for p in design["api"][:MAX_ROWS]]
    if len(design["api"]) > MAX_ROWS:
        lines.append(f"\n그 밖의 엔드포인트 {len(design['api']) - MAX_ROWS}개는 아래 접힌 부분에 있다")
    lines += ["", "## 시나리오와 확인할 테스트", ""]
    for r in scenarios(reqs):
        tests = [t["test_name"] for t in design["tests"] if r["id"] in t["depends_on"]]
        lines.append(f"- **{r['id']}**{mark(r['id'])} {r['text']} → {', '.join(f'`{t}`' for t in tests)}")
    lines += [f"- **{r['id']}**{mark(r['id'])} {r['text']} (지켜야 할 조건, 시나리오 아님)"
              for r in reqs if not r.get("postconditions")]
    lines += ["", "<details><summary>흐름, 요청·응답, 용어집</summary>", ""]
    for f in design["flows"]:
        lines += [f"### {f['id']}{mark(f['id'])} {f['name']}", "", "```mermaid", "sequenceDiagram",
                  *[f"    {_actor(s['from'])}->>{_actor(s['to'])}: {_said(s['say'])}" for s in f["steps"]], "```", ""]
    for p in design["api"]:
        lines.append(f"- **{p['id']}**{mark(p['id'])} `{p['method']} {p['path']}` 요청 {_shape(p['request'])} / "
                     f"응답 {_shape(p['response'])} / 오류 {', '.join(p['errors']) or '없음'}")
    for a in design["assumptions"][MAX_ASSUMPTIONS:]:
        lines.append(f"- **{a['id']}**{mark(a['id'])} {a['what']} — 이유: {a['why']} / 버린 안: {a['rejected']}")
    lines += ["", *[f"- **{t['id']}**{mark(t['id'])} `{t['test_name']}`: {t['given']} / {t['when']} / {t['then']}"
                    for t in design["tests"]]]
    lines += ["", *[f"- {g['term']}: {g['meaning']}" for g in glossary], "", "</details>", ""]
    return "\n".join(lines)


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
        for key in (*CONDITIONS, "depends_on", "context"):
            if key in item:
                made[key] = _texts(item[key], f"{where}: `{key}`")
        if verify:
            made["verify"] = [{k: " ".join(v[k].split()) for k in ("item", "expected")} for v in verify]
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
