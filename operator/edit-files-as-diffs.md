---
scope: operator
severity: contract
repeat: rule
triggers: ["edit\\s*(만|diff)", "diff\\s*(로|형태|방식)", "ran\\s*(형식|으로)\\s*(수정|하지|작성)", "스크립트로\\s*(수정|고쳐|바꾸)", "sed\\s+-i", "here-?string"]
slots: []
enforce:
  deny:
    - "Bash(sed -i*)"
    - "Bash(perl -pi*)"
    - "Bash(perl -i*)"
    - "Bash(perl -0pi*)"
    - "Bash(dos2unix*)"
    - "Bash(unix2dos*)"
  pretooluse: edit_as_diff.py
sources: []
sources_withheld: true
links: [destructive-git-guards]
---

# Edit files as diffs — do not rewrite them with a script

Rule. An existing file is changed by pairing the text to replace with what
replaces it. Writing the whole thing is for a new file only. No shell
redirection, no here-strings, no `sed -i`, and no one-line script that reads a
file, substitutes and writes it back.

Rewriting with a script makes three things worse at once. A whole file is
rewritten to change one line, what changed does not survive in the diff, and
encoding and line endings flip silently.

What goes wrong. Nobody can read where the change was. A diff fails loudly
when the target text does not match; a script succeeds quietly having changed
nothing.

## Enforcement — this page uses layer 1 and layer 4

The `sed -i` family looks like a command, so `permissions.deny` blocks it.
`apply` merges this page's `enforce.deny` into the target repository's
`.claude/settings.json`.

`Bash(perl -i*)` does not match `perl -0pi`, because the front is `-0pi`.

So the deny list here holds only the shapes that were actually measured, as
literals. An uncounted combination such as `perl -0777pi` still gets through —
when one shows up, a line is added then.

`tool/edit_as_diff.py` is layer 4, and it looks only at files that already exist.

- `Bash`: among the paths named by `cat >`, `cat >>`, `tee`, a redirection,
  `Path(...).write_*` or `open(..., "w")`, the ones present right now
- `Write`: when the target path already exists — since writing whole is for
  new files only

Half of it is what it lets through: new files, scratch outside the repository,
`/dev/null`, `2>&1`, and `Edit` itself.
**A false positive that stops the work is a hook the user switches off.**
Then enforcement is zero. `tool/test_edit_as_diff.py` holds eight blocking
cases and nine passing ones.

Command shapes are still infinite. What it cannot catch, the prose below holds.

What decides is what the body becomes. The body of `python - <<'PY'` is code
that runs and has to be read; the body of `git commit -F - <<'MSG'` is data
and must not be. So `strip_data_heredocs` looks at the receiver and drops the
body from the scan when it is not an interpreter. A redirection on the command
line itself is outside the body, so `cat > file <<'PY'` is still caught.

## A new file is the exception

Creating a file that did not exist is written whole. There is nothing to
replace, so it cannot be a diff.

The rule for the irreversible side is in [[destructive-git-guards]]. The two
hold the same ground — stopping a change from becoming unreadable to everyone.
