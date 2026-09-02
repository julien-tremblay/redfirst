# Contributing

Criticism is more useful than code right now, and disagreement about the premise is as
welcome as a bug report.

## The most valuable thing you can send

**A commit where the verdict is wrong.** Two kinds, both worth reporting:

- It said **DISCRIMINATES** about a test that does not actually test the change.
- It said **DOES NOT DISCRIMINATE** about a test that does. This is the quieter failure and
  the more damaging one, because it tells you a good test is worthless. One shipped: a
  non-ASCII filename made `git show` quote the path, so the revert silently did nothing.

Also useful: a commit it **refuses** to check that you believe it should handle.

## Running things

```
pip install pytest
python3 tests/test_redfirst.py      # builds throwaway git repos, no network, ~20s
```

That is what CI runs. Every case in it is a defect that actually shipped.

## What I am least sure about

1. **The blind spot may be larger than documented.** It catches a tautological test. It does
   not catch a test that discriminates while aiming at the wrong thing, and the README gives
   a real example. I do not know how much of the useful space that leaves.
2. **The addressable surface may be too small to matter.** Across five repositories and 221
   code commits, 25 shipped tests alongside the code: 11%. If that ratio holds generally,
   this tool can only ever speak to one commit in nine. If it does not hold in your
   repository, I would like to know.
3. **`--worktree` is fragile in ways I have probably not enumerated.** It checks out tracked
   files only, so an editable install, a virtualenv or `node_modules` will not be there. The
   README gives the `PYTHONPATH` workaround. Tell me what else breaks.
4. **An errored test and a failed test are treated identically.** Both count as red. For a
   commit that adds a new module, an `ImportError` against the parent is arguably the
   correct form of red. It may also be hiding something.

## If you send a patch

Every fix needs a test that fails without it, which is the tool's own thesis applied to
itself. `tests/test_redfirst.py` is a plain script and each case names the defect it locks
down. Standard library plus `git`; the suite additionally needs `pytest` because the fixture
repositories use it.
