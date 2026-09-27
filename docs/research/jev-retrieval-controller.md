# Jev retrieval controller

This record describes the initial experiment. The
[full advancement plan](../plans/jev/0-overview.md) defines the remaining
architecture and ten implementation stages; those stages are not yet complete.

Decision. Use Jev for bounded retrieval decisions in the wiki query path.
Keep execution, source access, thresholds, and retry limits in Python. Use
English instructions and criteria. Keep source quotations intact so citations
still refer to the original text. Research checked on 2026-09-26.

## Where graph engineering stands

`tool/graph.py` builds the hub policy graph, including declared relationships
and observed co-injection. `tool/repo_graph.py` builds document links, rule
references, and orphan counts; its `picture` function draws current repository
knowledge without writing into the checkout.

`tool/search/daemon.py` splits Markdown at headings, indexes English terms and
Hangul bigrams with BM25, and combines local multilingual-e5-small cosine
rankings through reciprocal rank fusion. Embeddings are cached by content and
model identity. Missing vectors fall back to lexical retrieval. Search keeps
the best section per page. The CLI adds one-hop neighbour titles and paths.

This is hybrid retrieval with a document/policy graph. It has no extracted
entity graph, community summaries, graph-based candidate expansion, or measured
multi-hop answer quality. A rich map does not establish those capabilities.
The earlier local decisions `2026-09-25-019-token-diet-5-search` and
`2026-09-25-020-token-diet-6-chat-search` in `.wiki/decisions/` remain applicable.

## Research adopted

| Primary source | Finding and adoption |
| --- | --- |
| [TypeSafe API](https://docs.typesafe.ai/api) | State plus typed questions produces keyed answers. Use Noul probabilities for binary gates; validate the response in code. Question IDs carry no inference meaning, so every instruction names its target explicitly. |
| [TypeSafe passage classification](https://docs.typesafe.ai/cookbooks/classifying_rag_passages) | Grade retrieved passages before generation. Preserve useful contradictions and partial evidence; relevance is not agreement with the question. |
| [TypeSafe reranking](https://docs.typesafe.ai/cookbooks/rerank_typesafe) | Retrieve a cheap shortlist before semantic scoring. Its legal-corpus experiment is evidence for the pattern, not an accuracy forecast for this wiki. |
| [Jev reranker implementation](https://github.com/hotchpotch/jev-reranker) | Practitioners retain partial answers and bridge facts, preserve document IDs, expose scores, and tune filtering on their own queries. Adopt these mechanics; reuse our existing retrieval stack rather than install another retrieval wrapper. |
| [LangGraph agentic RAG](https://docs.langchain.com/oss/python/langgraph/agentic-rag) | Model decisions route between retrieval, grading, and generation. Adopt explicit transitions and a bounded retry; ordinary functions suffice for this small graph. |
| [Microsoft GraphRAG local search](https://microsoft.github.io/graphrag/query/local_search/) | Entity relationships and source text can jointly supply evidence. Our document graph is a different representation; do not claim Microsoft-style GraphRAG from neighbour labels alone. |
| [TypeSafe confidence](https://docs.typesafe.ai/confidence) | Choice/Score confidence and a Noul's probability are different fields. Do not invent a Noul confidence field or treat a high probability as correctness proof. |
| [TypeSafe model specifications](https://docs.typesafe.ai/models) | English is the primary training language and currently the strongest. Pin `jev-1.13.0` for reproducibility; keep English control instructions and test Korean source evidence separately. |
| [Jev limitations](https://docs.typesafe.ai/model-jaggedness/jev-1.13) | Literal wording, irrelevant state, indirection, arithmetic, and adversarial text can cause failures. Use compact context, explicit English questions, local validation, and conservative fallbacks. |

## Implementation contract

The controller first decides whether external evidence is needed and which
existing sources could help: hub rules, repository documents/decision records,
and saved memory summaries. There is no paper database connector to route to;
adopted papers and web research can be stored as cited Markdown documents.
Source selection applies before the top-k cutoff. Uncertain routing searches
all sources. Rules injected by hooks are never subject to Jev filtering.

Retrieved sections are graded for useful evidence, including contradictions
and bridge facts. Only confidently irrelevant sections are dropped; uncertain
sections survive. The retained evidence is checked for sufficiency. Insufficient
evidence allows one wider search, then hands control back to the answering
agent with an explicit need for further verification. Jev never authorizes
tools, merges, writes, or declarations that an answer is true.

Stage 1 of the [plan](../plans/jev/1-runtime-baseline.md) moved configuration
into the hub's `.env` (or the file `JEV_ENV` names), read per request by
`tool/decision/`: `TYPESAFE_API_KEY`, `WIKI_JEV_MODEL`, and
`WIKI_JEV_MODE=off|shadow|active`, which defaults to shadow with a key and off
without one; the older `WIKI_JEV=on` still means active. Shadow records the
dossier beside the turn without giving it to the answering agent.
`python tool/jev_search.py` replaces `tool/search --jev` for explicit use, and
`python tool/jev_probe.py --live` checks connectivity. Requests send the question,
supplied context, and candidate excerpts to TypeSafe. No key or network error
silently counts as a negative judgment: return baseline retrieval and an
explicit fallback status. Keep the key out of logs. Do not modify hook routing.

## Verification boundary

Initial thresholds are conservative policy defaults, not calibrated wiki
measurements: at most `0.2` is a confident no; at least `0.8` is a confident yes.
The dossier records the policy and each successful decision's model, scores,
usage, and elapsed time. Automated checks cover routing, invalid responses, API failure,
relevance filtering, retry bounds, citation identity, and source isolation.
No TypeSafe key was present during the initial implementation session.

Validation on 2026-09-26: the controller, query, search, keep-alive, and
distribution suites passed all 121 tests. The broader run passed 515 tests;
its five failures were resolved in that rerun after correcting research-file
packaging and finishing edits that invalidate the search daemon's version.
The CLI also retrieved this research through its missing-key fallback.
Existing repository lint findings remain outside this change.

Before claiming a quality or cost improvement, compare identical wiki queries
against baseline retrieval: source recall, retained supporting passages,
unsupported answers, latency, API usage, and premature stopping. Include
English and Korean questions, contradictory decisions, bridge facts, and
questions the corpus cannot answer. Record the exact model and thresholds.
English controller instructions are a consistency choice, not proof that every
language or task performs equally. Entity extraction and graph expansion need
their own retrieval evaluation before becoming a default.

That comparison is stage 10 of the [plan](../plans/jev/10-evaluation-rollout.md):
a frozen dataset (`eval/jev/intents.json`, 120 intents in English and Korean,
split 60 calibration and 60 held out), frozen gates (`eval/jev/gates.json`),
and `tool/eval/compare.py` and `tool/eval/report.py` to run and read it, with
intervals resampled by intent. The plan's Reproduction section holds the exact
commands and its Measured section the numbers so far. As of 2026-09-28 only the
calibration split and a free held-out baseline have been run, on model-drafted
labels; no quality claim rests on them until a person reviews the labels and
the held-out arms with Jev run.
