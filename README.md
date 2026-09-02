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
reports DOES NOT DISCRIMINATE.

Worktree mode checks out tracked files only, so an editable install (`pip install -e .`)
still points at your original checkout and the package will not import. The test command
runs with the worktree as its working directory, so a relative path fixes it:

```
redfirst <commit> --worktree --test "PYTHONPATH=src python3 -m pytest tests/test_x.py -q"
```

The same applies to anything else your suite needs that git does not track: a virtualenv,
`node_modules`, a `.env`.

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

- **`--source` may not escape the repository.** Those paths are used to unlink and rewrite
  files, and a run killed between the two loses whatever they named.
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

## How much of your history can it actually speak to

Less than you would hope, and the number is worth knowing before you adopt it.

`redfirst` needs a commit that changes source **and** ships a test. Across five repositories
and 221 code commits, 25 did: **11%**.

| Repository | code commits | with tests |
|---|---|---|
| A | 40 | 10 (25%) |
| B | 57 | 7 (12%) |
| C | 18 | 3 (17%) |
| D | 38 | 3 (8%) |
| E | 68 | 2 (3%) |

That is one sample of one developer's habits and it may say more about the sample than about
software. But it bounds the tool honestly: on this history, eight commits in nine are
outside what it can answer, and no amount of improvement to the tool changes that. If your
ratio is different, that is genuinely useful to know.

On the 3 commits from that sample that were actually checked, all three correctly reported
DISCRIMINATES. No defects found. A small sample, and the deflating half of the result.

## What I would like you to break

1. **A commit where the verdict is wrong**, in either direction. The quieter and more
   damaging one is a false **DOES NOT DISCRIMINATE**, because it tells you a good test is
   worthless. One shipped: a non-ASCII filename made `git show` quote the path, so the
   revert silently did nothing and a correct fix read as untested.
2. **Tell me the blind spot is bigger than documented.** See below. I do not know how much
   of the useful space is left once you exclude tests that discriminate while aiming at the
   wrong thing.
3. **Tell me the 11% is unrepresentative.** If commits in your repository routinely pair
   source with tests, the ceiling above is wrong and the tool is more useful than I think.
4. **`--worktree` in a setup I have not seen.** It checks out tracked files only, so
   editable installs, virtualenvs and `node_modules` are absent. The workaround above covers
   the case I hit. There will be others.

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Tests

```
pip install pytest
python3 tests/test_redfirst.py
```

Offline, about twenty seconds. Every case is a defect that actually shipped. This is what CI
runs on every push, on Python 3.9 and 3.12.

## License

MIT.
