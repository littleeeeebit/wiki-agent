# Phase 1 — Translation Engine and Input Path

The overall design and the relationship between the three phases are in [Overview](0-overview.md)].

Goal. When a human writes in Korean, the agent receives it in English. The user screen does not change yet — changing the screen happens after the mirror in Phase 2 is created.

## What to build

### `tool/translate.py`

Call Gemini REST directly with `urllib`. There are 0 new dependencies — `requirements-chat.txt`
is only `fastapi`·`uvicorn`·`PyYAML` and there is no reason to add more here.

```
ko_to_en(text: str) -> str
en_to_ko(text: str) -> str
```

- Model `gemini-3.1-flash-lite`. The key is the `GEMINI_API_KEY` environment variable (already exists)

  The actual measurement is where the plan was corrected. The `gemini-2.5-flash` written initially is 404 for this account — "no longer available to new users". `gemini-2.5-flash-lite` is also the same 404, so the 2.5 generation cannot be used at all. Re-measuring the candidates, the round trip for 2 short strings was `3.6-flash` 6.3 seconds, `3.8-flash` 4.5 seconds, and `3.5-flash-lite` 1.2 seconds. Measuring only the two lites three times each, the median is `3.1-flash-lite` 1.17 seconds and `3.5-flash-lite` 1.00 seconds. The price difference between the two generations cannot be bought with a 0.17-second difference — fixed to `3.1-flash-lite` based on user judgment.
  The large model uses that time for thinking, but in translation where the protected sections are already extracted, there is nothing for it to live on. lite rejects `thinkingConfig` as 400 — it is turned off from the start without needing to be turned off. Since it is a path that runs for every utterance, the cheap tier is correct.

  Aliases are not used. `gemini-flash-latest` does not change the cache key but only changes the model, so the cache would continue to output translations of the model not currently in use.
- If it fails, return the original text as is. This applies to exceptions, timeouts, and missing keys. Do not let translation stop the session — `craft/hooks-fail-open`
- `stdin`·`stdout` UTF-8 fixed, if there is a need to use `subprocess`, then `encoding="utf-8", errors="replace"`. `lint.fragile_tools`·`fragile_io` checks it

### Placeholder protection — Do not leave it to the translator's judgment
Before translation, extract the following into tokens (user-area characters like ``+index) and restore them after translation.

| Protected Target | Why |
| --- | --- |
| Inline backticks `` `...` `` | Commands, paths, identifiers. Execution breaks if translated |
| Fence code blocks ` ``` ` | 같은 이유, 통째로 |
| `[[링크]]` | 위키 링크. 슬러그가 바뀌면 `graph.json` 이 끊긴다 |
| YAML front matter | `triggers` 정규식이 여기 있다. 번역하면 주입이 죽는다 |
| `<!-- wiki:... -->` 주석 | `inject.py` 가 심는 출처 표지 |

### 용어집 `tool/markers/glossary.toml` (새 파일)

```toml
# keep_korean — Words that would point to something else if translated to English. Keep as original text.
keep_korean = ["전자조달", "나라장터", "입찰공고", "지방계약법", "낙찰하한율"]

# fixed — Words with defined meanings within this wiki. Must not be translated differently per call.
[fixed]
"wiki" = "wiki"
"landmine" = "landmine"
"contract" = "contract"      # Page severity. Not a legal contract
"gate" = "gate"
"injection" = "injection"
"utterance" = "utterance"
"hook" = "hook"
```

두 표를 번역 프롬프트에 싣는다. `keep_korean` 은 "이 낱말은 한국어 그대로 두라",
`fixed` 는 "이 낱말은 반드시 이 영어로 옮기라".

### 캐시 `raw/translate-cache.sqlite3`

방향·원문·모델·프롬프트·용어집 버전 → 번역.
stdlib SQLite로 훅·미러의 동시 쓰기를 처리한다. 실패 결과는 캐시하지 않는다.
`raw/*`는 현재 `.gitignore` 대상이다.

### `translate.py --check`

```
python tool/translate.py --check --manifest docs/translation-baseline.json <path...>
```

Git 원문과 번역 산출물의 기계 검사. `--source-root`는 커밋 전 작업 검사용 대체 입력이다.

| 검사 | 빨개지는 조건 |
| --- | --- |
| `keep_korean` 손실 | 원문에 있던 보존 용어가 번역본에 없다 |
| 백틱 변조 | 인라인·펜스 코드의 내용이 원문과 한 글자라도 다르다 |
| 깨진 `[[링크]]` | 링크 슬러그가 바뀌었거나 대상 페이지가 없다 |
| front matter 변조 | `triggers`·`scope`·`severity`·`links` 가 달라졌다 |
| 표본 역번역 | 표본 N개를 en→ko 로 되돌려 `raw/translate-review.md` 에 원문과 나란히 적는다. 이 항목은 종료 코드에 안 넣는다 — 사람이 읽는 자리다 |

### `tool/test_translate.py`

- 자리표시자 왕복 — 백틱·코드블록·`[[링크]]`·front matter 가 있는 문서를 넣어
  네트워크를 안 타고(번역 함수를 가짜로 바꿔 대문자화 같은 것만 하게) 보호 구간이
  한 글자도 안 바뀌는지 본다
- 키가 없을 때 `ko_to_en` 이 원문을 그대로 돌려주는지
- 자식 프로세스로 불렀을 때 한글이 파이프에서 안 죽는지 — `test_edit_as_diff.py` 의
  같은 테스트가 본이다

## 고칠 것

### `tool/inject.py` — 입력 ko→en

순서가 전부다. 아래 셋을 이 순서로 한다.

1. 발화를 한국어 원문 그대로 받아 `triggers` 정규식에 매칭한다 — 지금 하던 대로
2. 페이지를 고른다 — 지금 하던 대로
3. 그 다음에 `ko_to_en(발화)` 를 불러 주입문 끝에 영어본을 덧붙인다

2번과 3번을 바꾸면 트리거가 영어 문장에 한국어 정규식을 대는 꼴이 되어 주입이
전부 죽는다. 죽는데 조용히 죽는다 — `craft/hooks-fail-open` 의 그 모양이다.

- 발화에 한글이 없으면 번역을 건너뛴다. API 호출 하나를 아끼고, 영어 발화를
  다시 영어로 옮기는 헛일을 막는다
- `.claude/settings.json` 의 이 훅 `timeout` 을 10 → 15 로 올린다. Gemini 왕복이
  들어온다. 단 훅은 예산을 넘겨도 통과시켜야 하므로, `translate.py` 자체에
  더 짧은 자체 타임아웃(예: 6초)을 두고 넘으면 원문만 쓴다.
  설정 원본은 `apply.py::hook_entry/session_entry`다. 그곳을 고친 뒤
  `python tool/apply.py --project . --agent claude --write`와
  `python tool/apply.py --project . --agent codex --write`로 생성한다
- 주입 형식:

```
<!-- wiki:english-rendering -->
English rendering of the user's message (Gemini; the Korean above is authoritative):
...
```

It is important to write that the Korean original is the authoritative version. When a translation is incorrect, the agent must be able to return to the original text.

### `tool/session_state.py` — Session start context ko→en

Users continue to write commit messages, PRs, and `.wiki/decisions/` in Korean. It is translated to English at the point of assembling `report()` that enters the agent context. The summary of decisions per utterance is also translated at the point of assembling the context in `inject`.

- `(title, why)` two strings produced by `decisions()` — commit title and `왜.` first sentence
- `.wiki/plan-active.md` body produced by `active_page()`
- Rows of the plan table produced by `open_steps()`
- Preserve the default Korean output of `branch_line()`, and make only `report()` explicitly select English output. Provide English notation for fixed strings directly and do not leave it to Gemini
- Fixed prose of `report()` (`## 브랜치`, `## 최근 결정 — 다시 뒤집기 전에 이유를 보라`, etc.) is also rewritten directly in English in the same way
- `## 단계` table parser of `open_steps()` and `완료`·`취소`·`상태` judgments are left in Korean as is. The plan documents in this folder use Korean tables, and users also write in Korean. Even if the page goes to English in Phase 2, the plan document is written by humans, so it does not follow
- This hook `timeout` is 15 → 25. Translates 4 decisions + plan table

`decisions()`·`active_page()`·`open_steps()` are maintained as functions that read the Korean authoritative version. Since `slack_brief.standup()` and `chat.handoff()` also call these functions, translating here changes the Slack/web user screen from Phase 1. Translation is applied only to the output assembly of `report()`, and Korean preservation for Slack/handover is added to existing tests.

Translation does not call sequentially for 6 seconds per string. Bundle and translate at once within the remaining time of the entire hook or pass a common deadline, and if time runs out, assemble the untranslated parts as original text and inject them without fail. Since waiting for each of the four decisions alone is 24 seconds, the 25-second setting alone cannot guarantee injection preservation upon failure. Verify with a test that fakes network latency.

### English-only prompts for agents — Not user screens

| File | Remarks |
| --- | --- |
| `tool/prompts/chat-answer.md` | |
| `tool/prompts/chat-explain.md` | |
| `tool/prompts/slack-retro.md` | Phrases output to Slack remain in Korean |
| `tool/prompts/slack-standup.md` | Same |
| `skills/after-merge/SKILL.md` | Korean trigger words of `description` (`머지했다`, etc.) remain |
| `skills/review-loop/SKILL.md` | Same |
| `skills/retrospect/SKILL.md` | Same |
| `skills/design-pass/SKILL.md` | Same |

Korean triggers for skill `description` have the same nature as the `triggers` regex — they exist to catch user utterances, so if changed to English, the skill will not trigger.

`chat-answer.md` and `chat-explain.md` are already English instructions. Instructions requiring Korean output are maintained until Phase 3 web overlay verification. The language of the prompt and the language of the output are separate. `tool/chat.py::WIKI_WRITER` and `CLAUDE_MD_WRITER` are also instructions for the agent, so translate them to English at this stage, but maintain the result explanation returned to the web in Korean. Distinguish from UI labels/errors.

## Things not touched

Korean word extraction of `tool/markers/ko.toml` · `census.py` · `triggers` · `korean_progress.py` of page front matter (Phase 2) · `statusMessage` of `settings.json` (keep Korean) · Page prose (Phase 2) · `tool/*.py` comments (Phase 3) · `lint.broken_wraps` (Phase 2)

## Phases

| # | Name | What | Status |
| --- | --- | --- | --- |
| 1 | translate | `tool/translate.py` + `glossary.toml` + cache | Done |
| 2 | check | Git-based manifest·`translate.py --check`·original/rename/new document sample·back-translation record | Done |
| 3 | test | `tool/test_translate.py` — placeholder round-trip·no key·pipe encoding | Done |
| 4 | inject | ko→en in `inject.py` (after trigger matching) + timeout 15 | Done |
| 5 | session | Translate decisions/plans only in `report()`, keep common functions in Korean + timeout 25 | Done |
| 6 | prompts | 4 prompts·4 skills·2 chat inline writers check/English, keep output in Korean | Done |
| 7 | gate | `tool/lint.py --check` and `pytest tool/` green | Done |

## Verification

- All `pytest tool/` green
- `python tool/test_apply.py` · `python tool/test_inject.py` · `python tool/test_slack_brief.py`
  — Direct execution main check also passes. Do not replace this test execution with pytest collection count
- `python tool/lint.py --check` green — especially whether `fragile_tools`·`fragile_io`·
  `missing_hook_guards` passes new `translate.py`
- `python tool/apply.py --project . --agent claude --check` and
  `python tool/apply.py --project . --agent codex --check` green — compare generation source and wiring
- Actual measurement: Type one Korean utterance and check (a) is the page still injected (b) is the English version attached (c) what is the perceived latency
- Same utterance with key intentionally removed — injection should run as is and only the English version should be missing

## How to revert

Revert translation calls, fixed prose, prompts, and timeout changes of `apply.py` together and regenerate the two host settings. If only the generated settings are reverted, the next apply will change them again. Disable the cache or change the version so that old translations are not reused.

## Review reflection — Translation contract and missing injection path

- `--check` cannot be compared without the original text. The authoritative version of the original text is git. The Phase 1 check manager creates a `docs/translation-baseline.json` format and checker that can be tracked. For each target, record the pre-translation fixed full commit SHA·original path·output path·type (translation/rewrite/new). Do not use `HEAD` as the default original text or guess renames. The Phase 2 pages manager fills in the actual target before translation. New clones need history including the specified commit, and if not, it explicitly fails. Read with `git show <SHA>:<원문 경로>`, and page renames specify the old path. Uncommitted Korean original text must first be included in the authoritative commit before fixing the SHA. Only checks for work before that allow `--source-root raw/translate-source` and it does not count as passing the deployment gate. New documents written in English from the start are specified as new and excluded from original text comparison, but undergo link/format checks. Do not automatically classify existing translated documents as new because the original text is missing. Record the previous value and allowed new value per file in the manifest for rewrite/rename protection section exceptions and review. Do not turn off checks for the entire file. General translation items must have the same protection sections. Absence of original text, empty targets, and tampering with protection sections are failures. Expand directories and `*.md` patterns inside the CLI, but exclude `docs/plans/` from translation targets. For rewrite pages, record only approved changes separately and compare the rest of the front matter, links, and backticks with the original text. In Phase 1, complete the checker with commits/renames/new/missing original text samples from a temporary Git repository. Since actual 32 English translations and user meaning verification are performed by the Phase 2 gate manager, Phase 1 completion does not wait for Phase 2 outputs. Exclude the manifest itself and plan documents from translation targets.
- Check for loss, duplication, or tampering of placeholders before restoration. Also protect `keep_korean` instead of trusting only the prompt, and return the original text upon verification failure and do not cache failed translations. Also protect the destination of Markdown links and `{slot}`. Check for fake responses that damage protection tokens.
- Include model, prompt, and glossary versions in the cache key in addition to direction and original text. Since hooks and mirrors are used simultaneously, use atomic storage such as stdlib SQLite. Do not put failed original text into the success cache.
- `source_map` of `inject.py`, fixed injection prose, `knowledge/digest` decision summary, and target `.wiki/*.md` body are also agent inputs. Preserve Korean authoritative version and trigger judgment, and translate after selection/summary. Even if no pages match, utterance translation is output. `trajectory` continues to record original utterances. Do not attach an English version label upon failure.
- The document title of `session_state.doc_catalog()` is also a translation target. If the entire catalog is wrapped in code fences and then translated, it will be caught by protection, so translate only the title first and preserve the path.
- `inject.shrink()` finds paragraphs with `규칙.`. Before Phase 2 body conversion, also make it recognize `Rule.`, and check if one line of rule remains in the small rule_budget of Korean/English pages.
