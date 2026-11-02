# Review research — F13, cycle 1

Draft research before the fix of review round 3 of PR #272, written 2026-11-02. It is not a verified fact or a shared rule: the review reads it with the fix.

## Problem

F13 (tool/guard_models.py:544): curl's connection overrides `--connect-to HOST1:PORT1:HOST2:PORT2` and `--resolve [+]HOST:PORT:ADDR[,ADDR]` send the connection to a host other than the URL's. The invariant is that the guard's network effects must include every host curl actually connects to; if it cannot determine one, the command is `top`. `override_host` splits the value on every colon and takes `parts[2]`. For `curl --connect-to 'localhost:80:[::ffff:203.0.113.7]:80' http://localhost/` that is `"["`. `network(Word("["))` finds no host and returns the empty set, so the only effect left is the granted loopback one, and the command passes even though curl connects to 203.0.113.7.

Trigger: repeat, rounds 2, 3.

- [P1] [P1] tool/guard_models.py:544 — Bracketed connection destinations disappear during curl parsing

## Failed fixes

- Round 1 (raised at guard_models.py:503): `--connect-to` and `--resolve` were in CURL_TAKES, so their values were consumed as option values and produced no effect at all. The destination was invisible.
- Round 2 (fix in 1f77357): added `override_host`, which does `w.text.split(":")` and returns `network(Word(parts[2]))`. It assumed no field contains a colon. curl's grammar allows bracketed IPv6 literals in HOST2 and in ADDR, and in HOST since 8.13.0, and these contain colons. A bracketed destination therefore splits into `[`, ``, `ffff`, ... and `parts[2]` is `[`. The fix also inherits a second fail-open from the shared code: `host_of` has no branch for a bare bracketed literal without a following port, so even `network(Word("[::ffff:203.0.113.7]"))` yields host `""`, and `network` turns an unrecognised host into 'no effect' instead of `top`. The regression test `F13-curl-connect-to` (tool/test_guard.py:419) uses only a colon-free hostname, so it exercised the one shape the split handles. Other forms that misparse today: a comma address list mixing in a bracketed address (`localhost:80:[::1],[::ffff:203.0.113.7]` gives parts[2] `[`), and a bracketed first field (`[::1]:80:unlisted.example:80` gives parts[2] `1]`). The second is denied today only by accident, through a bogus host, not through the real destination.

## Sources

### S1 — CURLOPT_CONNECT_TO - libcurl

- URL: https://curl.se/libcurl/c/CURLOPT_CONNECT_TO.html
- Retrieved: 2026-11-02
- Locator: Description
- Fragment: Each single string should be written using the format HOST:PORT:CONNECT-TO-HOST:CONNECT-TO-PORT ... The first string that matches the request's host and port is used. Dotted numerical IP addresses are supported for HOST and CONNECT-TO-HOST. A numerical IPv6 address must be written within [brackets]. Any of the four values may be empty. When the HOST or PORT is empty, the host or port always match (the request's host or port is ignored). When CONNECT-TO-HOST or CONNECT-TO-PORT is empty, the "connect to" feature is disabled for the host or port ... The "connect to" host and port are ignored if they are equal to the host and the port in the request URL
- Applicability: Field values legitimately contain colons inside brackets, so a split on every colon (guard_models.py:544) misaligns the fields. An empty CONNECT-TO-HOST means the URL's host, which m_curl already evaluates.
- Counterevidence: This is the libcurl option page. The curl tool's `--connect-to` passes its value to this option, but the tool's man page entry does not repeat the IPv6 bracket rule.
- Validation: The new guard case `curl --connect-to 'localhost:80:[::ffff:203.0.113.7]:80' http://localhost/` is denied after the fix and passes on 1f77357. `--connect-to 'localhost:80:[::1]:8000'` stays allowed.

### S2 — CURLOPT_RESOLVE - libcurl

- URL: https://curl.se/libcurl/c/CURLOPT_RESOLVE.html
- Retrieved: 2026-11-02
- Locator: Description; Availability
- Fragment: [+]HOST:PORT:ADDRESS[,ADDRESS] ... The optional leading plus (+) specifies that the new entry should timeout ... The entry to remove must be prefixed with a dash ... Specify the host as a single asterisk (*) to match all names ... Provide IPv6 addresses within [brackets]. ... Bracketed ADDRESS support came in 7.57.0. Multiple addresses per entry came in 7.59.0. The "+" prefix ... came in 7.75.0. IPv6 host components came in 8.13.0.
- Applicability: `--resolve` destinations are a comma list in which any item may be a bracketed IPv6 literal, and the HOST field itself may be bracketed. The third colon-field after a naive split is neither a single host nor the full list.
- Counterevidence: No example shows several bracketed addresses in one entry. That `[a],[b]` is valid is inferred from the comma rule and the bracket rule together. Behaviour differs by curl version.
- Validation: The new guard cases `--resolve 'localhost:80:[::ffff:203.0.113.7]'` and `--resolve 'localhost:80:[::1],[::ffff:203.0.113.7]'` are denied after the fix. `--resolve localhost:8000:127.0.0.1` and `--resolve -localhost:80` stay allowed.

### S3 — curl man page - --connect-to

- URL: https://curl.se/docs/manpage.html
- Retrieved: 2026-11-02
- Locator: --connect-to <HOST1:PORT1:HOST2:PORT2>
- Fragment: For a request intended for the "HOST1:PORT1" pair, connect to "HOST2:PORT2" instead. This option is only used to establish the network connection. It does NOT affect the hostname/port number that is used for TLS/SSL (e.g. SNI, certificate verification) or for the application protocols. "HOST1" and "PORT1" may be empty strings, meaning any host or any port number. "HOST2" and "PORT2" may also be empty strings, meaning use the request's original hostname and port number.
- Applicability: The network connection goes to HOST2, while the URL still shows HOST1. The real destination therefore has to come from the override, and the URL's host cannot stand in for it.
- Counterevidence: The man page entry does not mention IPv6 brackets. That rule comes from S1.
- Validation: The guard reports `network(unlisted.example)` for `--connect-to '[::1]:80:unlisted.example:80'` after the fix.

### S4 — Language-Theoretic Security - Wikipedia (and LangSec search results)

- URL: https://en.wikipedia.org/wiki/Language-Theoretic_Security
- Retrieved: 2026-11-02
- Locator: search-result summary for 'LangSec parser differential full recognition before processing'; not fetched
- Fragment: LangSec proposes specifying the syntactic structure of the input format as a formal grammar, then building a recognizer for it to validate any input before the rest of the program acts on it ... Parser differentials emerge when two (or more) parsers interpret the same input in different ways ... for non-deterministic pushdown automata (ambiguous context-free grammars) and above, the task of validating parser equivalence is UNDECIDABLE ... many parser generators allow the mixing of general purpose code with the parsing definitions, which weakens the guarantees
- Applicability: The guard and curl parse the same override string. `override_host` is ad hoc recognition interleaved with processing, and where it diverges from curl, the guard returns 'no effect'.
- Counterevidence: Seen through a search summary, not fetched. LangSec's guarantees are strongest with a single generated parser; here the guard can only approximate curl's hand-written parser, so equivalence is argued from the docs, not proved.
- Validation: After the fix, every malformed override (`'localhost:80:[::1'`, `'a:1:b:2:c'`) is `top`. Only values that match the documented grammar in full yield ordinary network effects.

### S5 — tool/guard_models.py (network, host_of, m_curl, override_host)

- URL: tool/guard_models.py
- Retrieved: 2026-11-02
- Locator: lines 99-120, 509-546
- Fragment: def host_of(text): ... else: host = re.split(r"[/:?#]", t, maxsplit=1)[0]; host = host.lstrip("@").strip("[]").lower().rstrip(".") ... def network(w): ... host = host_of(w.text); return {("network", host)} if host else set() ... def override_host(w): ... parts = w.text.split(":"); return network(Word(parts[2])) if len(parts) > 2 and parts[2] else set()
- Applicability: This shows the colon split in `override_host`, host_of's fallback that loses a bare bracketed literal, and `network` returning `set()` on an empty host. The last is the shared fail-open that also covers `-x`/`--proxy` and positional URLs.
- Counterevidence: Read only, not executed. The claim that `host_of('[::ffff:203.0.113.7]')` returns '' comes from tracing the regexes by hand.
- Validation: The new test `curl -x '[2001:db8::1]' http://localhost/` passes on 1f77357 and is denied after `network()` treats an empty host as `top`.

### S6 — tool/test_guard.py F13 regression case

- URL: tool/test_guard.py
- Retrieved: 2026-11-02
- Locator: line 419, F13-curl-connect-to
- Fragment: "F13-curl-connect-to": ("curl --connect-to localhost:80:unlisted.example:80 http://localhost/", "Bash", ...)
- Applicability: The round-2 regression test uses only colon-free fields, so it cannot catch the bracketed forms.
- Counterevidence: none
- Validation: The added bracketed cases sit next to this one and fail on 1f77357.

## Claims

- C1 (source; S1, S2): curl's override grammar allows colons inside a field: numerical IPv6 hosts and addresses are written within brackets, in both --connect-to and --resolve.
- C2 (source; S1, S3): With --connect-to, curl opens the network connection to HOST2:PORT2, not to the URL's host. An empty HOST2 means the URL's host.
- C3 (source; S2): --resolve takes an optional +/- prefix, a possibly bracketed or `*` HOST, and a comma-separated list of addresses, each of which may be a bracketed IPv6 literal.
- C4 (interpretation; S5, S1): `override_host` splits on every colon, so a bracketed destination yields parts[2] == '['. `network` then returns no effect, and only the granted URL host is judged.
- C5 (interpretation; S4, S5): The failure is a parser differential between the guard and curl. A checker that cannot prove it parses like the executor should reject what it does not fully recognise.
- C6 (interpretation; S5): `network()` returning set() when host_of finds no host is a shared fail-open. It also reaches `-x`/`--proxy` and positional targets that are bare bracketed literals, so fixing only override_host would leave sibling holes.
- C7 (interpretation; S6, S1): The round-2 regression test covers only colon-free override fields, which is why the fix passed its own test while missing bracketed destinations.

## Core theory

Language-theoretic security says input handling is a parser and recognition should be separated from processing: the whole input is checked against a grammar before anything acts on it. Ad hoc recognition interleaved with processing ('shotgun parsing') produces parser differentials, where two parsers read the same input differently. Here the two parsers are curl's (the executor) and the guard's (the checker). Wherever they diverge, the guard judges a different command from the one that runs. A safety checker that cannot prove equivalence with the executor has to reject what it does not fully recognise (fail closed), so that a divergence costs a false denial, not an escape.

## Applicability

curl documents the override grammar as `HOST:PORT:CONNECT-TO-HOST:CONNECT-TO-PORT`, with 'A numerical IPv6 address must be written within [brackets]', and `[+]HOST:PORT:ADDRESS[,ADDRESS]` with 'Provide IPv6 addresses within [brackets]'. Colons inside fields are therefore normal input, not an edge case, and `override_host` (guard_models.py:540-546) is a shotgun parser of exactly this grammar. The fail-open default is `network()` (guard_models.py:114-120), shared by m_curl's URLs and proxies (guard_models.py:524-526), m_wget's URLs (guard_models.py:557-558), and every other model that calls `network`. It returns `set()` when `host_of` (guard_models.py:102-111) yields an empty host. host_of's third branch splits on `:` before stripping brackets, so any bracketed literal without a trailing `:port` becomes empty. The same reading-ahead fail-closed pattern was proposed for F3 cycle 2 (`foreach_member`), which suggests this is the guard's general failure mode rather than something curl-specific.

## Hypothesis

This is a parser differential that fails open, at two layers. (1) `override_host` recognises curl's override grammar with an ad hoc colon split that disagrees with curl's own parser, which treats `[...]` as one field. (2) The shared `network()`/`host_of()` returns the empty set when it cannot recognise a host in a non-empty target, so any misparse anywhere becomes 'no network effect' instead of `top`. The same hole reaches siblings that never go through `override_host`: `curl -x '[2001:db8::1]' http://localhost/` and a scheme-less bracketed positional give host `""` from host_of's fallback `re.split(r"[/:?#]", ...)`. Patching the split for brackets alone would leave layer 2 for the next shape. The fix that converges: (a) in `network()`, a non-empty, non-`file:` target whose host comes out empty or unrecognised is `top`, never `set()`; (b) `host_of` recognises a bare `[v6]` literal; (c) `override_host` becomes a full recogniser of curl's grammar. It accepts an optional leading `+` or `-` (`-` removes an entry and returns no effect). Fields are a bracketed literal `[...]` or a run of non-colon characters. `--connect-to` must have exactly 4 fields. `--resolve` must have exactly 3, and the third is a comma list in which each item is a bracketed literal or an IPv4/hostname. Each destination (HOST2, every ADDR item) yields a network effect with brackets stripped and the same loopback normalisation. An empty HOST2 yields nothing, because the URL's own host is already evaluated. Any value that does not match the whole grammar is `top`.

## Counterevidence

- No curl binary was run in this session. That curl actually connects to the bracketed destination rests on the libcurl docs, not on observation. A curl built without IPv6 rejects the option with an error ('fail attempt to set an IPv6 numerical without IPv6 support', commit title seen only in a search result), in which case the command fails instead of escaping.
- The docs do not show one `--resolve` entry with several bracketed addresses. That `[a],[b]` is accepted is inferred from the comma rule plus the bracket rule.
- Making `network()` fail closed on an unrecognised host may deny commands that pass today, for example a curl positional that is a bare path or `:port`. The size of that false-positive cost is unknown until the corpus is run.
- `--connect-to` values equal to the request's host:port are ignored by curl, so some overrides the new parser turns into network effects are no-ops. That only costs precision, never soundness.

## Alternatives

- Make `override_host` bracket-aware but keep `network()` returning `set()` on an empty host. Rejected as the first fix: this is the same per-shape patching that failed in round 2, and the empty-host fail-open still reaches `-x '[v6]'`, scheme-less bracketed positionals and wget.
- Treat any `--connect-to` or `--resolve` as `top`. Sound and one line, but it would refuse legitimate local test forms such as `--resolve localhost:8000:127.0.0.1`. Kept as the fallback if the recogniser grows beyond a few lines.
- Reuse `urllib.parse.urlsplit` by prefixing `//` to each field. Rejected: it parses URL authority syntax, not curl's override grammar (comma lists, `+`/`-` prefixes, `*`, empty fields), which is another differential.
- Also model other destination-changing options (`--doh-url`, `--dns-servers`, `--alt-svc`). Not first: they are not in CURL_TAKES, so getopt leaves their values as positionals that already reach `network()` and are denied unless granted. `--alt-svc` reads a file and stays a limit.

## Experiment

On 1f77357, add guard cases to the OUTSIDE_FORMS-style table in tool/test_guard.py, each of which must be denied. `curl --connect-to 'localhost:80:[::ffff:203.0.113.7]:80' http://localhost/`; `curl --connect-to '[::1]:80:unlisted.example:80' http://localhost/`, which must report `network(unlisted.example)`, not a bogus host; `curl --resolve 'localhost:80:[::ffff:203.0.113.7]' http://localhost/`; `curl --resolve 'localhost:80:[::1],[::ffff:203.0.113.7]' http://localhost/`; `curl --resolve '+localhost:80:[2001:db8::1]' http://localhost/`; `curl -x '[2001:db8::1]' http://localhost/`; and malformed values that must be `top`: `--connect-to 'localhost:80:[::1'` and `--connect-to 'a:1:b:2:c'`. These must stay allowed: `curl --connect-to localhost:80:127.0.0.1:8000 http://localhost/`, `curl --connect-to 'localhost:80:[::1]:8000' http://localhost/`, `curl --connect-to localhost:80::8000 http://localhost/`, `curl --resolve localhost:8000:127.0.0.1 http://localhost:8000/`, `curl --resolve -localhost:80 http://localhost/`, and the existing `F13-curl-connect-to` case. The hypothesis predicts that the bracketed connect-to, resolve and `-x` cases pass on 1f77357 and are denied after the fix. Then run `python -m pytest -q tool/test_guard.py` and `python -m ruff check tool laya`, and list every existing guard case whose verdict changed from allowed to denied because of the `network()` empty-host rule. That list measures the false-positive cost. Optionally, outside the repo: `curl -v --connect-to 'localhost:80:[::1]:9' http://localhost/` should show it trying `::1` port 9, which confirms curl reads the bracketed field as one host.

## Result

pending — the fix records what it observed here, with the tests and the commit.

## Unresolved limits

Nothing was executed in this session (no shell). Every 'passes today' claim comes from reading `override_host`, `host_of` and `network`. The real curl behaviour for multi-address bracketed `--resolve` lists, for IPv6 HOST fields (8.13.0+), and for the `*` wildcard host was taken from the docs, not observed. Not covered: destinations chosen at run time from files or the environment (`--alt-svc` cache files, `http_proxy`/`ALL_PROXY` environment variables, `.curlrc` read without `-K`, `--doh-url`'s own fetch), which this finding does not raise. Whether every other caller of `network()` tolerates the new fail-closed empty-host rule needs the full guard test run.
