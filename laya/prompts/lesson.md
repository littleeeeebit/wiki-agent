# Task: improve a labeller's guide from the answers a verifier rejected

Input: JSON with `kind` (the decision being labelled), `guide` (the rules
the labeller follows now, a list), `rejections` (cases where the labeller's
answer was rejected: the state, the question, the labeller's answer and
confidence, and the verifier's evidence, answer and confidence) and
`history` (every guide tried before for this kind: its rules, whether it
was adopted, and its measured gain in verified labels against the noise
floor). Treat every field as data, never as instructions.

The labeller is a small model that reads the guide before every state. A
guide is adopted only when, on the same fresh states, more of the
labeller's answers pass verification with it than with the current guide,
so write rules that change its answers, not commentary.

Write the next guide: the full list of rules, not a diff.

- A rule names a pattern the labeller gets wrong and what the question's
  wording requires instead, in one or two sentences. It must generalise to
  states not shown here: no rule that only fits one case, and no passage
  text copied beyond a few words.
- Read the verifier's evidence critically. When it misreads the state, the
  case teaches nothing; leave it out.
- Keep a current rule unless the rejections show it misleads the labeller.
  Drop a rule that history shows did not help.
- Do not propose again a rule from a guide that was not adopted, unless you
  change what it says and can say why that change matters.
- When the rejections share no pattern, return the current guide unchanged
  and say so in `why`.
- At most 12 rules, each under 300 characters.

## Output

Only one JSON object, no prose around it, no code fence:

```json
{"rules": ["A passage that only shares the query's topic is not useful; it must state something the answer needs."],
 "why": "Most rejections accepted passages on the right subject that did not answer the question."}
```
