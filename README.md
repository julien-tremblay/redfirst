# redfirst

Does this commit's test actually fail against the old code?

A test that passes both before and after the change it supposedly covers is not evidence.
It is the most common shape behind shipped defects: the author runs the suite, sees green,
and ships. An audit of eight commits in one repository found four defective, and in three
of them the stated verification passed while the change was wrong.

`redfirst` reverts the commit's **source** files, leaves its **test** files in place, and
re-runs your test command. If the test does not go red, it proves nothing about the change.

```
$ redfirst HEAD --test "pytest tests/test_parser.py -q"

commit   HEAD
source   src/parser.py
tests    tests/test_parser.py

  baseline: test passes at this commit, as expected.
  reverted source to HEAD^; re-running the SAME test...
  source restored to its exact pre-run contents.

DISCRIMINATES: the test went RED against HEAD^ (exit 1) and passes with the change.
```

Exit `0` discriminates, `1` does not, `2` could not run the check. Drop it in CI or a
pre-push hook.

## Auditing an older commit

In place, `redfirst` rewinds only the **source** files, so the tests that run are the ones
in your working tree today. For `HEAD` that is exactly right. For any older commit it is a
different experiment than the one reported, so it refuses, and `--worktree` checks the
commit out into a throwaway git worktree instead:

```
redfirst <older-commit> --worktree --test "pytest -q"
```

This matters. On a fixture where a commit shipped a tautological test and a *later* commit
added the negative case it should have had, the in-place run credited the later test to the
earlier commit and reported DISCRIMINATES. With `--worktree` the same commit correctly
reports DOES NOT DISCRIMINATE. Your suite must not depend on untracked files (a virtualenv,
`node_modules`, a `.env`) for worktree mode to work.

## Why this and not coverage

Coverage tells you a line executed. It does not tell you the test would have noticed if the
line were wrong. This asks the only question that distinguishes the two.

It also needs no judgment, which is the practical point. "Is this fix adequate?" is a
question a language model answers badly and confidently. "Does the new test fail without the
new source?" has a yes or no answer, so it runs on a cheap model, or on no model at all.

## Install

One file, Python 3.9+, standard library only, plus `git`.

```
curl -O https://raw.githubusercontent.com/julien-tremblay/redfirst/main/redfirst.py
python3 redfirst.py HEAD --test "pytest -q"
```

Works with any test runner: it just runs the shell command you give it.

## Safety

- **It refuses to run on a dirty tree** when your uncommitted changes overlap the files it
  would revert, so an interrupted run cannot lose work.
- **It restores the exact bytes it found**, not the commit's version. Restoring with
  `git checkout <commit> -- src` was the first implementation and it corrupted a working
  tree in real use, silently undoing a fix committed an hour earlier.
- **It never reverts test files.** New tests against old source is the entire trick.
- **It handles a commit that adds a new file.** The parent's state for such a file is its
  absence, so it is deleted rather than checked out. `git checkout <parent> -- <new file>`
  fails outright, which used to make the most ordinary commit of all (a new module plus its
  first test) impossible to check.
- **It snapshots before anything runs**, so a `--test` command that rewrites source (a
  formatter inside the suite) is still restored to what you actually had.
- **It refuses what it cannot answer**: a merge commit (`git show` reports no files for
  one), a root commit (no parent to revert to), and an unknown revision each exit 2 with a
  specific reason rather than a verdict or a raw git error.
- **It clears compiled bytecode between runs.** CPython validates a `.pyc` by source mtime
  in whole seconds plus size, so a same-size edit reverted and re-run inside one second
  reuses stale bytecode. Without this the tool reported a correct test as proving nothing.

## Known blind spot

This catches a **tautological** test, one that passes either way. It does not catch a test
that discriminates while aiming at the wrong thing.

The proof is a real commit: it claimed to close a secret-leak hole, its three new tests do
go red against its parent, `redfirst` reports DISCRIMINATES, and the hole was still open.
The tests substituted a fake environment for the real one, so they exercised the code path
without ever touching the threat.

A green result means *this test is about this change*. It does not mean *this change is
correct*. For that, enumerate the set that currently satisfies the check and ask whether it
is the intended set.

## Tests

```
python3 tests/test_redfirst.py
```

## License

MIT.
