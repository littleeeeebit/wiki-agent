"""git: the row of the program table that changes the repository or reaches the network, for `guard`.

git runs only in the checkout the grant was read for, or a worktree of the
same repository, and only with the options and verbs listed here. What git
starts from its configuration (hooks, merge drivers, a pager) is trusted
because that configuration is protected; per-call configuration (`-c`) is
limited to keys whose values run nothing. A verb that moves the working tree
between commits (checkout, switch, merge, rebase, stash) writes what the
repository's history holds: the agent cannot author a commit that changes a
protected file or lays down a link, so that history is trusted as a whole.
A path the agent names (`git restore -- f`) is a write of that path, and a
write of the repository is a write of the whole worktree it runs in. A push
turns off HTTP redirects and submodule recursion on the command line, so only
the configured remote's own hosts are reached; fetch and pull have no row,
since a server may answer in protocol v2 and name another host for the pack.
A URL given in place of a remote reaches its host.
"""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import posixpath
import re

from guard import Word, moved_to, paths, top
from guard_models import gate, getopt, row, writes

# git's own options before the verb that take no value. `-C` (a directory) and `-c` (a listed key) are read
# by `m_git`; `--git-dir`, `--work-tree`, `--exec-path` and `--config-env` change which repository,
# configuration or program runs: they are not here, so they are `top`.
GIT_GLOBAL = {"--no-pager", "-P", "--no-optional-locks", "--literal-pathspecs", "--no-replace-objects", "--version",
              "--help"}
# Keys whose value git never runs, loads or sends anywhere: identity, whitespace and display.
GIT_SAFE_KEYS = {"user.name", "user.email", "core.whitespace", "core.autocrlf", "core.safecrlf", "core.quotepath",
                 "color.ui", "color.diff", "color.status", "advice.detachedhead"}
# A push must turn off what lets the server's answer pick another host: an HTTP redirect, and a submodule
# pushed through its own remote. A setting on the command line overrides every file.
NO_REDIRECTS = "http.followRedirects=false"
# The options the log/diff family shares. `--output` is a write; `--ext-diff`, `--textconv` are not here.
GIT_LOG = ("-pusczwbRqiEm -M? -B? -C? -U: -n: -S: -G: -L: -O: --patch --no-patch --stat --numstat --shortstat --name-only "
           "--name-status --summary --check --raw --cached --staged --merge-base --no-index --exit-code --quiet "
           "--color --no-color --word-diff --diff-filter: --unified: --relative --minimal --patience --histogram "
           "--ignore-all-space --ignore-space-change --ignore-blank-lines --ignore-space-at-eol --ignore-cr-at-eol "
           "--abbrev --full-index --binary --no-prefix --src-prefix: --dst-prefix: --find-renames --no-renames "
           "--dirstat --oneline --format: --pretty: --graph --decorate --no-decorate --all --branches --tags "
           "--remotes --author: --committer: --since: --until: --after: --before: --grep: --follow --reverse "
           "--no-merges --merges --first-parent --date: --abbrev-commit --left-right --cherry-pick --cherry-mark "
           "--boundary --topo-order --date-order --max-count: --skip: --source --no-walk --stat-width: "
           "--cc --full-history --simplify-by-decoration --notes --no-notes")
# verb (or `verb subverb`): (the flags its row understands, what its model does). A verb with no row is `top`.
GIT_VERBS = {
    "status": ("-sbvz -u? --short --branch --porcelain --long --untracked-files --ignored --ahead-behind "
               "--no-ahead-behind --show-stash --column --no-column --renames --no-renames --verbose", "read"),
    "diff": (GIT_LOG, "read"), "log": (GIT_LOG, "read"), "show": (GIT_LOG, "read"),
    "rev-list": (GIT_LOG + " --count", "read"),
    "rev-parse": ("-q --verify --quiet --short --abbrev-ref --symbolic --symbolic-full-name --show-toplevel "
                  "--show-prefix --show-cdup --git-dir --git-common-dir --absolute-git-dir --is-inside-work-tree "
                  "--is-inside-git-dir --is-bare-repository --is-shallow-repository --show-object-format --all "
                  "--branches --tags --remotes --not --default: --since: --until: --path-format: --git-path: --sq "
                  "--local-env-vars --show-superproject-working-tree --revs-only --no-revs --flags --no-flags", "read"),
    "ls-files": ("-cdmoiksuvtz -x: -X: --cached --deleted --modified --others --ignored --stage --unmerged --killed "
                 "--exclude-standard --exclude: --exclude-from: --error-unmatch --full-name --directory "
                 "--no-empty-directory --abbrev --eol", "read"),
    "ls-tree": ("-rdtlz --name-only --name-status --object-only --full-name --full-tree --long --abbrev", "read"),
    "check-ignore": ("-qvnz --quiet --verbose --non-matching --no-index --stdin", "read"),
    "merge-base": ("-a --all --octopus --independent --is-ancestor --fork-point", "read"),
    "cat-file": ("-tsep", "read"),
    "describe": ("--tags --all --always --long --dirty --exact-match --first-parent --contains --abbrev: --match: "
                 "--exclude:", "read"),
    "blame": ("-wsefltnpc -M? -C? -L: --porcelain --line-porcelain --root --show-name --show-number --date: "
              "--abbrev: --ignore-rev:", "read"),
    "grep": ("-inwvclLhHEFPIoqz -e: -f: -A: -B: -C: -m: --count --cached --untracked --no-index --heading --break "
             "--line-number --ignore-case --word-regexp --invert-match --files-with-matches --files-without-match "
             "--extended-regexp --fixed-strings --perl-regexp --only-matching --quiet --max-depth: --threads: --and "
             "--or --not --all-match --full-name --column", "read"),
    "remote": ("-v --verbose", "read"),
    "remote get-url": ("--push --all", "read"),
    "config": ("-lz --get --get-all --get-regexp --list --global --system --local --worktree --show-origin "
               "--show-scope --name-only --null --bool --int --path --type: --default:", "config"),
    "stash list": ("--oneline --format: --pretty: --date: -n: --max-count:", "read"),
    "stash show": ("-pu --stat --patch --name-only --name-status --include-untracked", "read"),
    "worktree list": ("-vz --porcelain --verbose", "read"),
    # Writes of the repository it runs in; a path operand is also a write of that path.
    # No `-A`/`--all`, `stash -u`, or a folder to add: each picks up untracked files, a protected one included
    # once an edited ignore rule stops hiding it. Only named files, each a write of that path.
    "add": ("-uv --update --verbose", "stage"),
    "commit": ("-qa -m: -F: --quiet --all --message: --file: --no-edit", "repo"),
    "branch": ("-avrdDmMfql -u: --all --remotes --verbose --list --show-current --delete --move --force --quiet "
               "--contains: --no-contains: --merged --no-merged --sort: --points-at: --set-upstream-to: "
               "--unset-upstream --track --no-track", "branch"),
    "checkout": ("-qft -b: -B: --quiet --force --detach --track --no-track --ours --theirs", "paths"),
    "switch": ("-qdft -c: -C: --quiet --detach --force --discard-changes --track --no-track", "repo"),
    "restore": ("-qSW -s: --source: --staged --worktree --quiet --ours --theirs", "paths"),
    "merge": ("-qn -m: -F: --quiet --ff --no-ff --ff-only --no-edit --no-commit --commit --squash --abort --continue "
              "--quit --no-verify --message: --file: --stat --no-stat --log --no-log --autostash", "repo"),
    "rebase": ("-q --quiet --continue --abort --skip --quit --onto: --autostash --no-autostash --keep-base "
               "--update-refs", "repo"),
    "stash push": ("-qk -m: --message: --keep-index --no-keep-index --quiet --staged", "paths"),
    "stash pop": ("-q --index --quiet", "repo"), "stash apply": ("-q --index --quiet", "repo"),
    "stash drop": ("-q --quiet", "repo"),
    "worktree add": ("-qf -b: -B: --quiet --force --detach --no-checkout --checkout --track --no-track --lock "
                     "--guess-remote --no-guess-remote", "worktree"),
    "worktree remove": ("-f --force", "worktree"), "worktree prune": ("-nv --dry-run --verbose --expire:", "repo"),
    # And the network: a push to a configured remote's hosts. No fetch or pull: a server may answer in protocol
    # v2 whatever version was asked, and v2 takes a packfile URI naming another host unasked; no option
    # refuses that, so they are left to a later row.
    "push": ("-uq --set-upstream --quiet --no-recurse-submodules", "push"),
}
GIT = {verb: (row(None, spec, digits=True), kind) for verb, (spec, kind) in GIT_VERBS.items()}
# `git branch` flags that only list.
BRANCH_LISTS = {"--all", "--remotes", "--verbose", "--list", "--show-current"}
# Verbs whose first operand picks what they do; the default when none is written.
SUBVERBS = {"stash": "push", "worktree": "", "remote": ""}


def same_path(a: str, b: str, ctx) -> bool:
    """One place by the host's rule: Windows paths ignore case, POSIX paths do not."""
    a, b = a.rstrip("/"), b.rstrip("/")
    return a.lower() == b.lower() if ctx.windows else a == b


def native(ctx) -> bool:
    """`ctx` models the paths of the host running the guard, so its places can be looked at."""
    return ctx.windows == (os.name == "nt")


def physical(path: str, ctx) -> str:
    """Where `path` is once links and junctions are followed, as git finds it from the process's directory.
    A context modelling the other host's paths is taken as written."""
    if not native(ctx):
        return path.rstrip("/") or "/"
    return os.path.realpath(path).replace("\\", "/").rstrip("/") or "/"


def repo_roots(ctx) -> set | None:
    """The work tree each place `ctx` may run in belongs to: the nearest folder holding `.git` above where
    the place really is, or the checkout the grant was read for. `None` when a place is unknown."""
    roots, repo = set(), ctx.repo and physical(ctx.repo, ctx)
    for cwd in ctx.cwds:
        if cwd is None:
            return None
        p, found = physical(cwd, ctx), None
        while found is None:
            if repo and same_path(p, repo, ctx) or os.path.exists(p + "/.git"):
                found = p
            elif (up := posixpath.dirname(p)) == p:
                return None
            else:
                p = up
        roots.add(found)
    return roots


def common_dir(top: str) -> str | None:
    from guard_grant import layout

    found = layout(Path(top))
    return found and found[2].as_posix()


def own_repo(ctx) -> bool:
    """Every place `ctx` may run in is the checkout whose remotes the grant read, or a worktree of the same
    repository: one configuration, one set of hooks, one set of remotes."""
    roots = repo_roots(ctx)
    if not ctx.repo or roots is None:
        return False
    repo = physical(ctx.repo, ctx)
    common = native(ctx) and common_dir(repo)
    return all(same_path(r, repo, ctx) or common and (c := common_dir(r)) and same_path(c, common, ctx)
               for r in roots)


def linked(path: str, ctx) -> bool:
    """`path` is a linked worktree of the repository `ctx` runs in, as git registers one: its `.git` file
    names a folder in the common directory's `worktrees`, which names it back. Never the main checkout,
    whose `.git` is the repository itself. Files the agent may write decide this, so one that cannot be
    read is no worktree, and the target stays an ordinary delete."""
    from guard_grant import layout

    if not native(ctx):
        return False
    try:
        if not own_repo(ctx):
            return False
        found = layout(Path(path))
        if found is None or not same_path(physical(str(found[0]), ctx), physical(path, ctx), ctx) \
                or not same_path(found[2].as_posix(), common_dir(physical(ctx.repo, ctx)) or "", ctx) \
                or not same_path(found[1].parent.as_posix(), (found[2] / "worktrees").as_posix(), ctx):
            return False
        back = (found[1] / "gitdir").read_text(encoding="utf-8").strip()
        return same_path(physical(back, ctx), physical(path + "/.git", ctx), ctx)
    except Exception:   # any failure (a decode, a NUL, a link loop on 3.11/3.12) only means "not recognised"
        return False


def worktrees(ctx) -> set:
    """`repo(root)` for the work tree of each place `ctx` may run in: a write moves the whole worktree and
    its index, wherever below the root git runs. Called once `own_repo` holds, so every root is known."""
    return writes("repo", [Word(r) for r in repo_roots(ctx)], ctx)


def reached(verb: str, pos: list, ctx) -> set:
    """The hosts `git push [REMOTE [BRANCH]]` reaches: every `url` and `pushurl` the grant read for that
    remote, or for every destination when none is named. A refspec with `+`, `:` or a revision suffix (a
    forced push, a deletion, another ref) is not in its row. A URL in place of a remote reaches its host,
    which the grant allows only when a remote names it."""
    from guard_models import host_of

    if pos and not pos[0].why and re.match(r"[a-zA-Z][\w+.-]*://|[^/\\:\s]{2,}:(?!\\)", pos[0].text):
        return {("network", host_of(pos[0].text))}
    if any(w.why for w in pos) or pos and pos[0].text not in {n for n, _ in ctx.remotes} or not ctx.remotes \
            or len(pos) > 2 or len(pos) == 2 and re.search(r"[+:^~@{}]", pos[1].text):
        return top(f"git {verb} to anything but a configured remote and a branch")
    hosts = {h for n, h in ctx.remotes if not pos or n == pos[0].text}
    if "\0unread" in hosts:
        return top(f"git {verb} to a local path, a rewritten URL or configuration syntax the guard does not decode")
    return {("network", h) for h in hosts}


PRETTY = {"oneline", "short", "medium", "full", "fuller", "reference", "email", "mboxrd", "raw"}


def formats(args: list) -> set:
    """A `--format`/`--pretty` value is a built-in format or a literal template. A `%G` field verifies the
    signature, which runs the configured verifier; any other name is an alias the config defines."""
    for k, w in enumerate(args):
        m = re.match(r"--(?:format|pretty)(=?)(.*)", w.text, re.S)
        if not m:
            continue
        value = m.group(2) if m.group(1) else (args[k + 1].text if k + 1 < len(args) else "")
        if "%G" in value:
            return top("a git format field that verifies signatures")
        if "%" not in value and value.removeprefix("format:").removeprefix("tformat:") == value \
                and value not in PRETTY:
            return top("a git format alias the configuration defines")
    return set()


def pathspecs(verb: str, pos: list, ctx) -> set:
    """Each operand as a write of that path. A pathspec glob or magic (`*.toml`, `:(top)`) matches paths
    the operand does not show, so it is `top`."""
    if any(not w.why and re.search(r"[*?\[]|^:", w.text) for w in pos):
        return top(f"git {verb} with a pathspec pattern")
    return writes("write", pos, ctx)


def configured(rest: list) -> set:
    """`git config` reads only: `--get…`/`--list`, the `get`/`list` subcommands, or one key alone."""
    pos, _, flags = getopt(rest)
    keys = [w.text for w in pos]
    if any(f in flags for f in ("--get", "--get-all", "--get-regexp")) and 1 <= len(keys) <= 2 \
            or any(f in flags for f in ("-l", "--list")) and not keys \
            or keys[:1] == ["get"] and len(keys) == 2 or keys == ["list"] \
            or len(keys) == 1 and "." in keys[0] and not set(flags) & {"--get", "--get-all", "--get-regexp"}:
        return set()
    return top("git config that sets or edits a value")


def git_options(args: list, ctx):
    """`(index of the verb, ctx)` after git's own options, or `(effects, None)` for one outside the row."""
    i = 0
    while i < len(args):
        w, t = args[i], args[i].text
        if w.why:
            return top(f"{w.why} as a git option or verb"), None
        if t in ("-C", "-c"):
            if i + 1 >= len(args) or args[i + 1].why:
                return top(f"git {t} with a value known only at run time"), None
            value = args[i + 1]
            if t == "-C":
                ctx = replace(ctx, cwds=moved_to(value, ctx))
            elif value.text.partition("=")[0].lower() not in GIT_SAFE_KEYS \
                    and value.text != NO_REDIRECTS:
                return top(f"git -c {value.text.partition('=')[0]}: a key whose value git may run or send"), None
            i += 2
            continue
        if t not in GIT_GLOBAL:
            if t.startswith("-"):
                return top(f"git {t}: an option the table does not model"), None
            break
        i += 1
    return i, ctx


def settings(args: list, end: int) -> set:
    """The values of the `-c` pairs before the verb, read pair by pair as `git_options` reads them; a `-C`
    value is a directory, whatever it spells."""
    out, k = set(), 0
    while k < end:
        if args[k].text in ("-C", "-c"):
            if args[k].text == "-c":
                out.add(args[k + 1].text)
            k += 2
        else:
            k += 1
    return out


def m_git(name, args, ctx):
    i, ctx = git_options(args, ctx)
    if ctx is None:
        return i, None
    if i >= len(args):
        return set(), None
    verb, rest = args[i].text, args[i + 1:]
    if verb in SUBVERBS:
        sub = rest[0].text if rest and not rest[0].why and not rest[0].text.startswith("-") else SUBVERBS[verb]
        if rest and sub == rest[0].text:
            rest = rest[1:]
        verb = f"{verb} {sub}".strip()
    if verb not in GIT:
        return top(f"git {verb}: a verb the table does not model"), None
    r, kind = GIT[verb]
    if stop := gate(f"git {verb}", rest, r):
        return stop, None
    # What git starts from its configuration (hooks, a text converter, a verifier) is trusted only because
    # that configuration is protected: in another repository it may be one the agent wrote.
    if not own_repo(ctx):
        return top("git in a repository other than the checkout"), None
    if any(n == "\0lazy" for n, _ in ctx.remotes):
        return top("git whose configuration may make it fetch or write where the guard cannot place: a partial "
                   "clone, a trace target, or a file the guard does not read"), None
    pos, _, rest_flags = getopt(rest, r.takes)
    if kind == "read":
        return (top("git remote with an operand") if verb == "remote" and pos else formats(rest)), None
    if kind == "config":
        return configured(rest), None
    if kind == "branch" and not pos and all(set(f[1:]) <= set("avrl") or f in BRANCH_LISTS for f in rest_flags):
        return set(), None
    # git's write of the repository it runs in is the granted git verb; `outside` judges its place only.
    out = worktrees(ctx)
    if kind in ("paths", "stage"):
        out |= pathspecs(verb, pos, ctx)
        folders = any(re.fullmatch(r"\.{1,2}|.*[/\\]\.{0,2}", w.text) for w in pos) \
            or native(ctx) and any(e[0] == "write" and os.path.isdir(e[1]) for e in out)
        if kind == "stage" and folders:
            return top(f"git {verb} of a folder, which stages every untracked file below it"), None
    elif kind == "worktree":
        # git matches a relative `remove` operand as the tail of any registered worktree's path; `C:/x` is
        # relative on POSIX.
        absolute = r"[A-Za-z]:[\\/]|/" if ctx.windows else "/"
        if not pos or verb == "worktree remove" and not re.match(absolute, pos[0].text):
            return top(f"git {verb} without an absolute path"), None
        found = paths("delete" if verb == "worktree remove" else "write", pos[0], ctx)
        out |= {("worktree", e[1]) if e[0] == "delete" and linked(e[1], ctx) else e for e in found}
    elif kind == "push":
        out |= reached(verb, pos, ctx)
        if NO_REDIRECTS not in settings(args, i) or "--no-recurse-submodules" not in rest_flags:
            out |= top(f"git {verb} without `-c {NO_REDIRECTS}` and `--no-recurse-submodules`")
    return out, None
