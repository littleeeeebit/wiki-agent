# Review research — F4, cycle 1

Draft research for PR #307, started before round 4 and corrected for the F7 provenance finding in round 5. This is evidence for independent review, not a verified finding or shared rule.

Finding: F4. Invariant: catalogue coverage.

## Problem

F4 concerns catalogue coverage: PowerShell instructions in scoped documents and prompt text must be checked against the command catalogue. The round-four finding concerns CommonMark inline code with multiple backtick delimiters. The earlier scanner used CommonMark for fences but a single-backtick regex for inline spans, leaving valid inline instructions unchecked.

The separate F6 invariant concerns executable child-shell capability enforcement. It is not the subject of this F4 problem statement, hypothesis or experiment.

## Provenance correction

The draft committed before round 4 and retained in commit 20823ef assigned F4's title and lint finding to a child-shell argument-reconstruction analysis. That was a mixed identity. Adding a distinction in its Result did not correct its problem statement, sources and claims. F7 in round 5 identified this inconsistency.

The following mapping replaces that attribution:

| Finding | Invariant | Relevant code and evidence |
| --- | --- | --- |
| F4 | Catalogue coverage | `tool/shell_lint.py`; inline and fence regression tables |
| F6 | Child-shell capability enforcement | `tool/common/shell.py`; native argument, child-version and assignment-context tests |
| F7 | Research identity and provenance | This correction and `test_research_identity_matches_its_declared_invariant` |

The historical child-argument hypothesis and the harmless split-command experiment remain visible in commit 20823ef. Their evidence belongs to F6. The current F6 draft cites that history explicitly; it is not evidence for the inline-code defect.

## Failed fixes

The supplied F4 review history identifies gaps in multiline prompt strings, mixed-case fence labels, longer CommonMark fences and finally inline code spans. Existing regression files preserve these cases. This paragraph summarizes the supplied review history rather than asserting an independently verified chronology of every commit.

The round-three correction adopted CommonMark for block fences while retaining the separate inline regex. The round-four correction removed that regex and reads inline code tokens, including nested children, from the same parser.

## Sources

- S1: `tool/shell_lint.py`, `instructions` and `check`. These define instruction extraction, catalogue comparison and the three scoped roots.
- S2: `tool/test_shell_round_two.py`, `test_powershell_fences_are_catalogued_independently_of_case`. This covers label case, roots and line endings.
- S3: `tool/test_shell_round_three.py`, `test_commonmark_powershell_fences_are_catalogue_checked`. This covers longer and tilde fences, including containers.
- S4: `tool/test_shell_round_four.py`, `test_commonmark_inline_instructions_are_catalogue_checked`, `test_nested_inline_code_tokens_are_also_catalogue_checked` and `test_inline_literal_data_is_not_an_instruction`. These distinguish executable inline instructions from ordinary inline data.
- S5: The supplied round-four F4 and round-five F7 findings. These establish the finding identities and identify the provenance error; they are not runtime observations.

## Claims

- C1: F4 is a catalogue-lint invariant, supported by S1 and the supplied finding identity in S5.
- C2: Block-fence recognition and inline-span recognition were separate extraction paths. Correctness of one did not establish correctness of the other; S2, S3 and S4 exercise their distinct forms.
- C3: Inline code tokens can occur under other inline tokens. Walking children preserves the same catalogue rule for those locations; S4 includes nested-token controls.
- C4: The old child-shell sources and experiment do not establish C1-C3. Their attribution to F4 was erroneous, even though they motivated a separate F6 repair.

## Hypothesis

For the round-four defect, divergent block and inline extraction caused incomplete catalogue coverage. Using the CommonMark token tree for both should make delimiter length and inline containers independent of whether an instruction reaches catalogue comparison. Ordinary data must remain outside the instruction set.

## Experiment

Run the inline instruction table across single and multiple backtick delimiters, whitespace and newline normalization, and all three scoped roots. Check nested inline tokens as well as ordinary inline data. Preserve the existing fence, multiline prompt and catalogue-form controls. These are static extraction and lint experiments; supplied command strings are data, not commands to execute.

## Result

In the repair commit titled `Recognize assignment-position child shells and correct research provenance`, F7 corrects this draft's identity throughout its problem, sources, claims and experiment. The F4 controls are `test_commonmark_inline_instructions_are_catalogue_checked`, `test_nested_inline_code_tokens_are_also_catalogue_checked` and `test_inline_literal_data_is_not_an_instruction`. The separate F6 draft records the assignment experiment and capability decisions. Both records remain drafts for independent review.

## Unresolved limits

The supplied review history provides finding identities; this correction does not independently certify a complete historical ledger. Catalogue coverage establishes inspection of recognized instructions, not safety of executing them. Child-shell discovery and version compatibility remain separate F6 obligations.
