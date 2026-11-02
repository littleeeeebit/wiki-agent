# Review research — F14, cycle 1

Draft research before the fix of review round 3 of PR #272, written 2026-11-02. It is not a verified fact or a shared rule: the review reads it with the fix.

## Problem

F14 (tool/guard_models.py:766): `gh api` sends its request to whatever host an absolute endpoint names. The invariant is that m_gh's network effects include every host gh will contact, and if the guard cannot work out that host, the command is `top`. The round-2 repair only inspects endpoint words with no `why` that contain `://`. `gh api "$ENDPOINT"` has `why='variable'`, so it falls to `else set()`. Only the default `github.com` effect is left, the command passes under a github.com grant, and at run time $ENDPOINT can be `https://unlisted.example/x`.

Trigger: repeat, rounds 2, 3.

- [P1] [P1] tool/guard_models.py:766 — Unknown gh API endpoints are silently ignored

## Failed fixes

- Round 1 (raised at guard_models.py:714): m_gh's effects came only from `--hostname`, GH_HOST in ctx.env, and the HOST of a three-part `-R`. The endpoint positional was never looked at, so `gh api https://unlisted.example/path` was judged against github.com.
- Round 2 (fix in 1f77357, lines 762-766): added a loop over pos[1:] doing `network(w) if not w.why and "://" in w.text else set()`. It positively matches one dangerous shape, a literal absolute URL, and turns everything else into 'no effect'. Its `not w.why` test runs before `network()` sees the word, and `network()` is the shared function that already returns `top` for a run-time value (`if w.why: return top(...)`, line 115). The caller therefore bypassed the only fail-closed check on the path. gh decides 'absolute' on the expanded value, so a literal-only check cannot cover what the shell supplies. The regression test `F14-gh-absolute-endpoint` (tool/test_guard.py:421) uses only a literal URL, so it exercised the one shape the fix handles.

## Sources

### S1 — cli/cli pkg/cmd/api/http.go (trunk)

- URL: https://raw.githubusercontent.com/cli/cli/trunk/pkg/cmd/api/http.go
- Retrieved: 2026-11-02
- Locator: func httpRequest, requestURL selection
- Fragment: isGraphQL := p == "graphql" ... if strings.Contains(p, "://") { ... } else if isGraphQL { requestURL = ghinstance.GraphQLEndpoint(hostname) ... requestURL = ghinstance.RESTPrefix(hostname) + strings.TrimPrefix(p, "/")
- Applicability: gh treats an endpoint as absolute when the final, expanded string contains `://` anywhere, and then ignores --hostname and GH_HOST. The guard cannot evaluate that test for a word with `why`.
- Counterevidence: Read through a fetch summary that quoted the branch lines but elided the body of the `://` branch. The full body is taken from S2.
- Validation: After the fix, `gh api "$ENDPOINT"` is denied as top in tool/test_guard.py, while `gh api repos/o/r/pulls` and `gh api graphql -f query=x` stay allowed, matching the RESTPrefix and graphql branches.

### S2 — gh 2.23.0 pkg/cmd/api/http.go (Debian sources)

- URL: https://sources.debian.org/src/gh/2.23.0%2Bdfsg1-1/pkg/cmd/api/http.go
- Retrieved: 2026-11-02
- Locator: func httpRequest
- Fragment: if strings.Contains(p, "://") { requestURL = p } else if isGraphQL { requestURL = ghinstance.GraphQLEndpoint(hostname) } else { requestURL = ghinstance.RESTPrefix(hostname) + strings.TrimPrefix(p, "/") } ... requestURL = addQuery(requestURL, pp) ... req, err := http.NewRequest(method, requestURL, body)
- Applicability: Confirms that an absolute endpoint is used verbatim as the request URL. The request goes to that URL's host, not the default GitHub host.
- Counterevidence: This is an older release (2.23.0). S1 shows that trunk keeps the same condition.
- Validation: The existing F14-gh-absolute-endpoint case (literal URL) stays denied with network(unlisted.example) after the change.

### S3 — gh api - GitHub CLI manual

- URL: https://cli.github.com/manual/gh_api
- Retrieved: 2026-11-02
- Locator: Description; Options
- Fragment: The endpoint argument should either be a path of a GitHub API v3 endpoint, or `graphql` to access the GitHub API v4. Placeholder values `{owner}`, `{repo}`, and `{branch}` in the endpoint argument will get replaced with values from the repository of the current directory or the repository specified in the GH_REPO environment variable. ... --cache <duration> ... -p, --preview <strings> ... --hostname <string> The GitHub hostname for the request (default "github.com"). (The page does not mention absolute URLs.)
- Applicability: `--cache` and `--preview` take values that GH_TAKES does not list (only `-p` is listed, for another command). The endpoint is therefore not reliably pos[1], and the loop must keep scanning all of pos[1:].
- Counterevidence: The manual does not document absolute endpoints at all. That behaviour rests on S1/S2 source, not on a documented contract.
- Validation: `gh api --cache 1h "$ENDPOINT"` is denied after the fix; `gh api 'repos/{owner}/{repo}/pulls'` stays allowed.

### S4 — gh environment - GitHub CLI manual

- URL: https://cli.github.com/manual/gh_help_environment
- Retrieved: 2026-11-02
- Locator: GH_HOST; GH_REPO
- Fragment: GH_HOST: specify the GitHub hostname for commands where a hostname has not been provided, or cannot be inferred from the context of a local Git repository. If this host was previously authenticated with, the stored credentials will be used. Otherwise, setting GH_TOKEN or GH_ENTERPRISE_TOKEN is required, depending on the targeted host. GH_REPO: specify the GitHub repository in the [HOST/]OWNER/REPO format for commands that otherwise operate on a local repository.
- Applicability: GH_REPO can carry a host and m_gh never reads it. GH_HOST is only a fallback, since the host can also come from the local repository's remote, which m_gh does not model.
- Counterevidence: GH_HOST/GH_REPO with no stored credentials need a token for that host. Without one, gh may fail before sending anything; whether it still connects is not documented.
- Validation: `GH_REPO=unlisted.example/o/r gh issue list` is denied after the fix, and `GH_REPO=o/r gh issue list` stays allowed.

### S5 — Go issue 19297 and CL 'net/url: reject colon in first segment of relative path in Parse' (search results)

- URL: https://github.com/golang/go/issues/19297
- Retrieved: 2026-11-02
- Locator: search-result summary for Go url.Parse getScheme; not fetched
- Fragment: If there is no scheme and the path doesn't start with a slash, and a colon appears before the first slash, Parse rejects the input ... The check was added so that `scheme:foo` input isn't misread as a relative path ... Go's Parse accepts both absolute and relative URLs, so when the scheme is invalid it treats the string as a relative path.
- Applicability: Supports the optional refinement only: if a `/` precedes the first expansion in an endpoint, the string cannot have a scheme, so http.NewRequest gets a relative URL with no host and cannot reach a new host.
- Counterevidence: Seen through a search summary, not fetched. It describes url.Parse, not http.Client's handling of a hostless URL, which is inferred to fail with an unsupported-scheme error.
- Validation: Only if the refinement is added: `gh api repos/o/r/pulls/$N` stays allowed and `gh api "$E"/x` stays denied.

### S6 — tool/guard_models.py (host_of, network, m_gh)

- URL: tool/guard_models.py
- Retrieved: 2026-11-02
- Locator: lines 102-120, 749-780
- Fragment: def network(w): if w.why: return top(f"{w.why} in a network target") ... hosts = [w.text for w in vals.get("--hostname", [])] or [env.get("GH_HOST", "github.com")] ... if repo is not None and repo.text.count("/") >= 2: hosts.append(repo.text.split("/")[0]) ... if verb == "api": for w in pos[1:]: out |= network(w) if not w.why and "://" in w.text else set()
- Applicability: Shows network()'s own fail-closed branch, the gh api loop that filters `why` words out before network() sees them, and the -R/--hostname reads that use `.text` and ignore `why`.
- Counterevidence: Read only, not executed.
- Validation: After the one-line change, the new `$ENDPOINT` cases report the network() top reason, `in a network target`.

### S7 — tool/test_guard.py F14 regression case

- URL: tool/test_guard.py
- Retrieved: 2026-11-02
- Locator: line 421, F14-gh-absolute-endpoint
- Fragment: "F14-gh-absolute-endpoint": ("gh api https://unlisted.example/path", "Bash", "허가된 호스트가 아니다")
- Applicability: The round-2 regression test covers only a literal absolute URL, the one shape the fix handles.
- Counterevidence: none
- Validation: The added variable-endpoint cases sit next to this one, are allowed on 1f77357 and denied after the fix.

### S8 — tool/guard.py (Word, Ctx.env)

- URL: tool/guard.py
- Retrieved: 2026-11-02
- Locator: lines 35-47, 67, 647
- Fragment: why: str = ""  # set when the value is only known at run time ... env: tuple = ()  # assignments in front of the current command ... ctx = replace(ctx, env=tuple(w.text for w in env))
- Applicability: `why` is the guard's marker for a run-time value. ctx.env keeps only text, so a variable GH_HOST/GH_REPO prefix loses its `why`, and exported variables from earlier statements are not in ctx.env at all.
- Counterevidence: Whether `bind` resolves `$E` assigned earlier in the same script into a literal (consts) was not traced.
- Validation: `gh --hostname "$H" api user` is denied as top, not through a bogus host, after the sibling change.

## Claims

- C1 (source; S1, S2): gh decides at run time whether an endpoint is absolute, using strings.Contains(p, '://') on the final string. An absolute endpoint becomes the request URL verbatim, overriding --hostname and GH_HOST.
- C2 (source; S3): gh api has value-taking flags (--cache, --preview) that GH_TAKES does not list, so the endpoint is not reliably the first positional after `api`.
- C3 (interpretation; S6): The round-2 fix filters out `why` words before network() sees them, bypassing network()'s existing fail-closed branch. An unknown endpoint therefore contributes no effect instead of top.
- C4 (interpretation; S4, S6, S8): GH_REPO and -R carry an optional HOST and can select a host other than github.com. m_gh ignores GH_REPO entirely and ignores `why` on -R and --hostname.
- C5 (interpretation; S6, S7): The round-2 regression test covers only a literal absolute URL, which is why the fix passed its own test while missing variable endpoints.
- C6 (interpretation; S5, S1): An endpoint whose literal text puts a `/` before its first expansion cannot parse with a scheme and host, so it cannot leave the default host. That makes it a candidate for a precision exemption.
- C7 (interpretation; S8, S6): The guard marks run-time values with Word.why, and the sound treatment of such a value in a network target is top, as network() already does.

## Core theory

A checker that runs before the executor has to judge the set of values a word may take, not the text it sees. When the executor decides a property such as 'is this an absolute URL' on the expanded value, the checker can decide it statically only for literal words. For any other word it must over-approximate, which means the unknown/`top` element, or the analysis stops being sound. gh's own source shows the run-time decision: `if strings.Contains(p, "://") { requestURL = p } else if isGraphQL { ... } else { requestURL = ghinstance.RESTPrefix(hostname) + strings.TrimPrefix(p, "/") }`, followed by `http.NewRequest(method, requestURL, body)`.

## Applicability

m_gh (guard_models.py:749-780) receives Words whose `why` marks a value known only at run time (guard.py:37). The shared `network()` (guard_models.py:114-120) already maps a `why` word to `top`. The bug is that the gh api loop filters such words out before network() sees them. The same pattern sits on other gh inputs that select a host: `-R` (lines 758-760 read `repo.text` and ignore `why`), `--hostname` (line 757 uses `w.text`), and the environment, where ctx.env keeps only text (guard.py:647) and GH_REPO is not consulted at all. This is the same failure as F13 cycle 1 (network() returning set() on an unrecognised host) and F3 cycle 2 (foreach_member returning no effect for unexplained arguments): a model that returns 'no effect' for input it did not fully recognise.

## Hypothesis

This is a time-of-decision mismatch that fails open. gh chooses the destination at run time, after shell expansion, using `strings.Contains(p, "://")` on the final endpoint string. The guard sees the word before expansion, and for any word it cannot read it substitutes 'no effect' instead of 'unknown'. The fix that converges is to let unknown endpoint words reach the shared fail-closed rule. In the `api` branch, change line 766 to `out |= network(w) if w.why or "://" in w.text else set()`, so every `gh api` positional with `why` is `top` through network(). Keep iterating over all of pos[1:]: `--cache` and `--preview` take values that GH_TAKES does not list, so the endpoint is not reliably pos[1]. Siblings of the same invariant in m_gh, to fix in the same change because they rely on the same 'literal or ignored' reading: (a) a `-R`/`--repo` value with `why` should be `top`, because `[HOST/]OWNER/REPO` can carry a host; (b) the `GH_REPO` environment variable, which gh honours in the same `[HOST/]OWNER/REPO` form, is not read at all. A literal `GH_REPO=host/o/r` prefix should add its host like `-R`; (c) a `--hostname` value with `why` should be `top` rather than going through host_of(raw text). It is denied today only because the raw text yields a bogus host. Optional precision refinement, not needed for soundness: a `why` word whose literal text before its first expansion already contains `/` cannot become a URL with a host, so it can stay on the default host.

## Counterevidence

- Making every `gh api` positional with `why` `top` also denies common relative forms such as `gh api repos/o/r/pulls/$N/comments`, which cannot leave the default host. Without the prefix refinement this is a real false-positive cost, and it was probably why round 2 added `not w.why`.
- The guard may already bind `E=https://x; gh api "$E"` to a literal through `consts`, in which case that form is caught today. The hole is only for values the script did not assign. Not checked by running.
- gh's endpoint placeholders `{owner}`, `{repo}` and `{branch}` are substituted from the current repository or GH_REPO. They are literal text to the guard, so they do not set `why`. Their substituted values cannot contain `://`, because git ref names and GitHub owner/repo names exclude `:`, so they do not reopen the hole. This rests on naming rules, not on gh's code.

## Alternatives

- Make the whole `gh api` command `top` whenever any positional has `why`. That is equivalent to the hypothesis in effect, but it loses the reason string network() produces. Not preferred.
- Inspect the literal prefix of a `why` word and allow it when a `/` comes before the first expansion. This is sound per Go's URL parsing (a scheme ends at the first `:` and may contain only letters, digits, `+`, `-`, `.`; a schemeless relative URL has no host, and the request fails). It depends on how Word.text encodes expansions (MARK bytes, guard.py:51) and on Go internals, so it comes second, as a precision change once the fail-closed rule is in.
- Also model `export GH_HOST=...` / `export GH_REPO=...` in an earlier statement of the same command, since ctx.env holds only prefix assignments. Not first: the finding does not raise it, and it needs the env to flow across statements, which the evaluator does not carry today. Recorded as a limit.
- Resolve the variable from the environment at check time. Rejected: the reviewer explicitly says no environment resolution is needed, and the value at execution can differ.

## Experiment

On 1f77357, add to the OUTSIDE_FORMS-style table in tool/test_guard.py, each of which must be denied: `gh api "$ENDPOINT"`; `gh api --cache 1h "$ENDPOINT"`; `gh api -X POST "$ENDPOINT" -f a=b`; `gh -R "$R" issue list`; `GH_REPO=unlisted.example/o/r gh issue list` (with github.com granted); `gh --hostname "$H" api user`, which must be denied as `top`, not through a bogus host. These must stay allowed with github.com granted: `gh api repos/o/r/pulls`, `gh api 'repos/{owner}/{repo}/pulls'`, `gh api graphql -f query=x`, `gh pr view "$N"` (a `why` positional outside `api` must not become top), `GH_REPO=o/r gh issue list`, and the existing `F14-gh-absolute-endpoint` case. The hypothesis predicts that the `$ENDPOINT` cases, `-R "$R"` and GH_REPO pass on 1f77357 and are denied after the fix. Then run `python -m pytest -q tool/test_guard.py` and `python -m ruff check tool laya`, and list every existing case whose verdict changed. Any relative `gh api ...$VAR` form among them measures the false-positive cost and decides whether the prefix refinement is worth adding.

## Result

pending — the fix records what it observed here, with the tests and the commit.

## Unresolved limits

Nothing was run in this session (no shell). The claim that `gh api "$ENDPOINT"` passes today comes from reading lines 762-766. gh's decision rule was read from trunk and 2.23.0 source, not observed against a live server. Not covered: GH_HOST/GH_REPO set by `export` in an earlier statement or inherited from the session environment; gh inferring the host from the local repository's git remote (gh's docs say GH_HOST applies only when the host cannot be inferred from the local repository); `gh api --input` bodies; and `gh extension` commands, which are already `top`. Whether the prefix refinement is safe depends on Word.text's MARK encoding, which was not traced.
