#!/usr/bin/env python3
"""redfirst: does this commit's test actually fail against the old code?

A test that passes both before and after the change it supposedly covers is not evidence.
It is the single most common shape behind shipped defects. A 2026-09-01 audit of one repository
found four defective commits out of eight, and in three of them the author's stated
verification passed while the change was wrong. Two were caught by running exactly this
check by hand -- revert the source hunk, re-run the changed test, require RED.

The point is that it needs no judgment. "Is this fix adequate?" is a question a context
holding the plan answers badly; "does the new test fail without the new source?" is a
command with a yes/no answer, which is why it transfers to a cheap model, or to CI.

  redfirst <commit> --test "pytest path/to/test_x.py -q"
  redfirst <commit> --test "..." -k some_test_name
  redfirst HEAD --test "..." --repo /path/to/other/repo

Exit 0 = DISCRIMINATES (test went red against the old source). Exit 1 = it did not, which
means the test proves nothing about this change. Exit 2 = could not run the check.

It reverts SOURCE files only, never the test files -- that is the whole trick: new tests
against old source.

KNOWN BLIND SPOT, measured rather than guessed. This catches a TAUTOLOGICAL test (one that
passes either way). It does NOT catch a test that discriminates while aiming at the wrong
thing. The proof is the worst defect the 2026-09-01 audit found: `4e03ab5` claimed to close
a secret-leak hole, its three new tests DO go red against its parent -- this tool reports
DISCRIMINATES on it -- and the hole was still wide open, because the tests substituted a
fake environment for the real one and so exercised the code path while never touching the
threat. A green result here means "this test is about this change", not "this change is
correct". For that second question the cheap move is enumeration: list the set that
currently satisfies the check and ask whether it is the intended set. That, not this, is
what caught 4e03ab5.

Restoration is unconditional; the working tree is checked for modifications to the affected
paths first and the run is refused if any exist, so an interrupted run can never silently
leave old source in place.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def git(args, repo, check=True):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        print(f"git {' '.join(args)} failed: {r.stderr.strip()}", file=sys.stderr)
        sys.exit(2)
    return r.stdout


def _drop_stale_bytecode(repo, paths):
    """Delete compiled caches for the reverted files.

    CPython validates a .pyc by the source's mtime IN WHOLE SECONDS plus its size. A
    one-character fix (`a - b` -> `a + b`) does not change the size, and a fast suite
    reverts and re-runs inside the same second, so the recorded mtime still matches and
    the stale bytecode is reused. The reverted source then never executes and the tool
    reports DOES NOT DISCRIMINATE for a test that genuinely does. Measured 2026-09-01 on
    a two-commit fixture: without this, a correct fix with a correct test was reported as
    proving nothing.
    """
    seen = set()
    for f in paths:
        d = (Path(repo) / f).parent
        if d in seen:
            continue
        seen.add(d)
        shutil.rmtree(d / "__pycache__", ignore_errors=True)
    # Nested packages keep their own caches; the reverted module may be imported through
    # any of them, so clear the whole tree rather than guess which.
    for pc in Path(repo).rglob("__pycache__"):
        shutil.rmtree(pc, ignore_errors=True)


def classify(paths):
    """Split a commit's files into (source, tests). A path is a TEST if any component
    looks like one -- `tests/`, `test_x.py`, `x_test.go`, `spec/`. Deliberately generous:
    misfiling a test as source would revert it and destroy the check's meaning, while
    misfiling source as a test only makes the check weaker and visible."""
    src, tests = [], []
    for p in paths:
        low = p.lower()
        parts = low.split("/")
        name = parts[-1]
        is_test = ("tests" in parts or "spec" in parts or name.startswith("test_")
                   or name.startswith("test-") or "_test." in name or ".test." in name
                   or name.endswith("_spec.py"))
        (tests if is_test else src).append(p)
    return src, tests


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("commit", help="the commit whose test/source pair is being checked")
    ap.add_argument("--test", required=True, help="command that runs the test(s)")
    ap.add_argument("--repo", default=".", help="repository root (default: cwd)")
    ap.add_argument("--source", action="append", default=None,
                    help="explicit source path to revert (repeatable); "
                         "default: every non-test file the commit touched")
    ap.add_argument("--worktree", action="store_true",
                    help="check the commit out into a throwaway git worktree and run "
                         "there. Required for any commit that is not HEAD. Slower, and "
                         "your suite must not depend on untracked files (a venv, "
                         "node_modules, a .env).")
    args = ap.parse_args()
    repo = args.repo

    head = git(["rev-parse", "HEAD"], repo).strip()
    rev = subprocess.run(["git", "-C", repo, "rev-parse", "--verify", "--quiet",
                          args.commit + "^{commit}"], capture_output=True, text=True)
    if rev.returncode != 0:
        print(f"REFUSED: {args.commit!r} is not a commit in {repo}.", file=sys.stderr)
        return 2
    target = rev.stdout.strip()
    nparents = len(git(["rev-list", "--parents", "-n", "1", target], repo).split()) - 1
    if nparents == 0:
        print(f"REFUSED: {args.commit} is the root commit -- it has no parent, so there "
              f"is no old code to discriminate against.", file=sys.stderr)
        return 2
    if nparents > 1:
        print(f"REFUSED: {args.commit} is a merge commit. `git show` reports no files for "
              f"a merge, so there is nothing to revert. Check the individual commits on "
              f"the branch instead.", file=sys.stderr)
        return 2

    # THE TESTS COME FROM THE WORKING TREE, NOT FROM THE COMMIT. In place, this tool only
    # rewinds the SOURCE files; whatever the tree currently holds is what runs. For HEAD
    # that is exactly right. For any older commit it is a different experiment than the one
    # reported, and it gives WRONG ANSWERS: measured 2026-09-02 on a fixture where a commit
    # shipped a tautological test and a LATER commit added the missing negative case, this
    # printed "DISCRIMINATES: it tests the change" about a test that, as written at that
    # commit, passed against its own parent. A false green on the only question it asks.
    if target != head and not args.worktree:
        print(f"REFUSED: {args.commit} is not HEAD ({head[:8]}). In place, the tests that\n"
              f"run are the ones in your working tree TODAY, not the ones this commit\n"
              f"shipped, so a later commit that strengthened the test would be credited to\n"
              f"this one. Re-run with --worktree to check the commit as it actually was.",
              file=sys.stderr)
        return 2

    origin = repo
    tmp = None
    if args.worktree:
        tmp = tempfile.mkdtemp(prefix="redfirst-")
        wt = str(Path(tmp) / "wt")
        git(["worktree", "add", "--detach", "--quiet", wt, target], origin)
        repo = wt
    try:
        return _run(args, repo, target)
    finally:
        if tmp:
            git(["worktree", "remove", "--force", str(Path(tmp) / "wt")], origin, check=False)
            shutil.rmtree(tmp, ignore_errors=True)


def _run(args, repo, target):
    files = [f for f in git(["show", "--name-only", "--format=", target], repo).split("\n") if f]
    src, tests = classify(files)
    if args.source:
        src = args.source
    if not src:
        print(f"REFUSED: {target[:8]} touches no source files "
              f"({len(tests)} test file(s)) -- nothing to revert, so there is no "
              f"old code to discriminate against.", file=sys.stderr)
        return 2

    # A throwaway worktree is clean by construction and holds nothing of the user's.
    dirty = [] if args.worktree else [
        ln[3:] for ln in git(["status", "--porcelain"], repo).split("\n") if ln]
    clash = sorted(set(dirty) & set(src))
    if clash:
        print("REFUSED: uncommitted changes in the files this would revert; commit or "
              "stash them first so an interrupted run cannot lose them:", file=sys.stderr)
        for c in clash:
            print(f"    {c}", file=sys.stderr)
        return 2

    # Snapshot the exact bytes on disk BEFORE anything runs, and restore THOSE. Restoring
    # with `git checkout <commit> -- src` was wrong and corrupted the working tree the first
    # time this tool was run in anger: for any commit that is not HEAD it puts the file back
    # at THAT COMMIT's content, silently reverting whatever the tree actually had. It did
    # exactly that to scripts/guard-secret-leak.py, undoing a fix committed an hour earlier.
    # A checker that damages the tree it is auditing is worse than no checker.
    # Taken before the BASELINE and not after it: a --test command that rewrites source (a
    # formatter in the suite) would otherwise be snapshotted in its rewritten state and
    # "restored" to it, under a message promising the pre-run contents.
    snapshot = {}
    for f in src:
        fp = Path(repo) / f
        snapshot[f] = fp.read_bytes() if fp.exists() else None

    print(f"commit   {target[:8]}")
    print(f"source   {', '.join(src)}")
    print(f"tests    {', '.join(tests) or '(none in this commit)'}")
    print(f"running  {args.test}\n")

    before = subprocess.run(args.test, shell=True, cwd=repo)
    if before.returncode != 0:
        print(f"\nREFUSED: the test command already fails at {target[:8]} "
              f"(exit {before.returncode}). Fix that first -- a check that starts red "
              f"cannot tell you anything about the old code.", file=sys.stderr)
        return 2
    print("\n  baseline: test passes at this commit, as expected.\n")

    parent = f"{target}^"
    # A file the commit ADDED does not exist in the parent, and `git checkout <parent> -- <new>`
    # fails outright -- it does not partially apply -- so the whole run died with a raw
    # "pathspec did not match" and exit 2. That made the most ordinary shape of all
    # uncheckable: a new module plus its first test. The parent's state for such a file is
    # its ABSENCE, so delete it.
    in_parent = [f for f in src if subprocess.run(
        ["git", "-C", repo, "cat-file", "-e", f"{parent}:{f}"],
        capture_output=True).returncode == 0]
    added = [f for f in src if f not in in_parent]
    try:
        if in_parent:
            git(["checkout", parent, "--", *in_parent], repo)
        for f in added:
            (Path(repo) / f).unlink(missing_ok=True)
        _drop_stale_bytecode(repo, src)
        detail = f"{len(in_parent)} reverted"
        if added:
            detail += f", {len(added)} deleted (added by this commit)"
        print(f"  source at parent {parent[:8]}^ ({detail}); re-running the SAME test...\n")
        after = subprocess.run(args.test, shell=True, cwd=repo)
    finally:
        for f, blob in snapshot.items():
            fp = Path(repo) / f
            if blob is None:
                fp.unlink(missing_ok=True)
            else:
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_bytes(blob)
        git(["restore", "--staged", *src], repo, check=False)
        print("\n  source restored to its exact pre-run contents.")

    if after.returncode != 0:
        print(f"\nDISCRIMINATES: the test went RED against {target[:8]}^ "
              f"(exit {after.returncode}) and passes with the change. It tests the change.")
        return 0
    print(f"\nDOES NOT DISCRIMINATE: the test passes BOTH with and without the change.\n"
          f"It proves nothing about {target[:8]}. Either it exercises a code path the\n"
          f"change did not alter, or -- the common case -- it pins the implementation\n"
          f"rather than the threat: the setup replaced the very state the bug lives in.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
