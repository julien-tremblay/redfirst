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
  the pre-fork name HEAD --test "..." --repo ~/ai

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
    args = ap.parse_args()
    repo = args.repo

    files = [f for f in git(["show", "--name-only", "--format=", args.commit], repo).split("\n") if f]
    src, tests = classify(files)
    if args.source:
        src = args.source
    if not src:
        print(f"REFUSED: {args.commit} touches no source files "
              f"({len(tests)} test file(s)) -- nothing to revert, so there is no "
              f"old code to discriminate against.", file=sys.stderr)
        return 2

    dirty = [ln[3:] for ln in git(["status", "--porcelain"], repo).split("\n") if ln]
    clash = sorted(set(dirty) & set(src))
    if clash:
        print("REFUSED: uncommitted changes in the files this would revert; commit or "
              "stash them first so an interrupted run cannot lose them:", file=sys.stderr)
        for c in clash:
            print(f"    {c}", file=sys.stderr)
        return 2

    print(f"commit   {args.commit}")
    print(f"source   {', '.join(src)}")
    print(f"tests    {', '.join(tests) or '(none in this commit)'}")
    print(f"running  {args.test}\n")

    before = subprocess.run(args.test, shell=True, cwd=repo)
    if before.returncode != 0:
        print(f"\nREFUSED: the test command already fails at {args.commit} "
              f"(exit {before.returncode}). Fix that first -- a check that starts red "
              f"cannot tell you anything about the old code.", file=sys.stderr)
        return 2
    print("\n  baseline: test passes at this commit, as expected.\n")

    # Snapshot the exact bytes on disk, and restore THOSE. Restoring with
    # `git checkout <commit> -- src` was wrong and corrupted the working tree the first
    # time this tool was run in anger: for any commit that is not HEAD it puts the file
    # back at THAT COMMIT's content, silently reverting whatever the tree actually had.
    # It did exactly that to scripts/guard-secret-leak.py, undoing a fix committed an hour
    # earlier. A checker that damages the tree it is auditing is worse than no checker.
    parent = f"{args.commit}^"
    snapshot = {}
    for f in src:
        fp = Path(repo) / f
        snapshot[f] = fp.read_bytes() if fp.exists() else None
    try:
        git(["checkout", parent, "--", *src], repo)
        _drop_stale_bytecode(repo, src)
        print(f"  reverted source to {parent}; re-running the SAME test...\n")
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
        print(f"\nDISCRIMINATES: the test went RED against {parent} "
              f"(exit {after.returncode}) and passes with the change. It tests the change.")
        return 0
    print(f"\nDOES NOT DISCRIMINATE: the test passes BOTH with and without the change.\n"
          f"It proves nothing about {args.commit}. Either it exercises a code path the\n"
          f"change did not alter, or -- the common case -- it pins the implementation\n"
          f"rather than the threat: the setup replaced the very state the bug lives in.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
