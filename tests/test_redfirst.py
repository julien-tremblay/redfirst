#!/usr/bin/env python3
"""End-to-end tests on throwaway git repositories. Both directions, because a checker
whose positive and negative cases coincide cannot fail."""
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
RF = ROOT / "redfirst.py"
FAILS = []


def mkrepo(before_src, after_src, test_src):
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "src").mkdir(); (d / "tests").mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(d), *a], capture_output=True)
    run("init", "-q", ".")
    run("config", "user.email", "t@t"); run("config", "user.name", "t")
    (d / "src/m.py").write_text(before_src)
    (d / "tests/test_m.py").write_text(test_src)
    run("add", "-A"); run("commit", "-qm", "before")
    (d / "src/m.py").write_text(after_src)
    run("add", "-A"); run("commit", "-qm", "after")
    return d


def check(name, got, want):
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}  (exit {got}, want {want})")
    if not ok:
        FAILS.append(name)


TEST = "from src.m import add\ndef test_add():\n    assert add(2,2)==4\n"
SMOKE = "from src.m import add\ndef test_smoke():\n    assert add is not None\n"
CMD = "python3 -m pytest tests/test_m.py -q"


def run_rf(repo, commit="HEAD"):
    return subprocess.run([sys.executable, str(RF), commit, "--test", CMD, "--repo", str(repo)],
                          capture_output=True, text=True).returncode


# 1. A real fix with a real test MUST discriminate. This failed before the stale-bytecode
#    fix: `a - b` and `a + b` are the same size, the revert and re-run land in the same
#    whole second, and CPython reused the .pyc, so the reverted source never ran.
r = mkrepo("def add(a,b):\n    return a - b\n", "def add(a,b):\n    return a + b\n", TEST)
check("real fix + real test discriminates", run_rf(r), 0)
shutil.rmtree(r, ignore_errors=True)

# 2. A test that cannot see the change must be caught. Without this case the suite would
#    pass a checker that simply returns 0 always.
r = mkrepo("def add(a,b):\n    return a + b\n",
           'def add(a,b):\n    """doc only"""\n    return a + b\n', SMOKE)
check("tautological test is caught", run_rf(r), 1)
shutil.rmtree(r, ignore_errors=True)

# 3. A bad commit ref cannot be reported as a verdict.
r = mkrepo("def add(a,b):\n    return a + b\n", "def add(a,b):\n    return a + b\n", SMOKE)
check("unknown commit exits 2", run_rf(r, "deadbeefdeadbeef"), 2)
shutil.rmtree(r, ignore_errors=True)


# --- 2026-09-02 adversarial pass -------------------------------------------------------
def gitq(d, *a):
    return subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True)


# 4. A commit that ADDS a source file. `git checkout <parent> -- <new file>` fails outright,
#    so the whole run died with a raw pathspec error and exit 2 -- and "new module plus its
#    first test" is the most ordinary commit there is. The parent's state for such a file is
#    its absence, so it must be deleted, not checked out.
r = mkrepo("def add(a,b):\n    return a + b\n", "def add(a,b):\n    return a + b\n", SMOKE)
(r / "src/new.py").write_text("def mul(a,b):\n    return a * b\n")
(r / "tests/test_new.py").write_text("from src.new import mul\ndef test_mul():\n    assert mul(3,4)==12\n")
gitq(r, "add", "-A"); gitq(r, "commit", "-qm", "feat: new module")
got = subprocess.run([sys.executable, str(RF), "HEAD", "--test",
                      "python3 -m pytest tests/test_new.py -q", "--repo", str(r)],
                     capture_output=True, text=True).returncode
check("commit that adds a new source file is checkable", got, 0)
shutil.rmtree(r, ignore_errors=True)

# 5. THE FALSE GREEN. In place, the tests that run come from the working tree, not from the
#    commit. Here commit A ships a tautological test and a LATER commit adds the negative
#    case A should have had. Ground truth: A's test passed against A's parent, so A DOES NOT
#    discriminate -- but running today's tests against A's parent goes red, and the tool
#    reported "DISCRIMINATES: it tests the change". A false green on its only question.
r = mkrepo("def is_admin(u):\n    return True\n",
           "def is_admin(u):\n    return u.get('role')=='admin'\n",
           "from src.m import is_admin\ndef test_yes():\n    assert is_admin({'role':'admin'}) is True\n")
target = gitq(r, "rev-parse", "HEAD").stdout.strip()
(r / "tests/test_m.py").write_text(
    "from src.m import is_admin\n"
    "def test_yes():\n    assert is_admin({'role':'admin'}) is True\n"
    "def test_no():\n    assert is_admin({'role':'guest'}) is False\n")
gitq(r, "add", "-A"); gitq(r, "commit", "-qm", "test: the negative case nobody wrote")
check("non-HEAD commit is refused in place", run_rf(r, target), 2)
got = subprocess.run([sys.executable, str(RF), target, "--worktree", "--test", CMD,
                      "--repo", str(r)], capture_output=True, text=True).returncode
check("--worktree gives the TRUE verdict on that commit", got, 1)
shutil.rmtree(r, ignore_errors=True)

# 6. Structural refusals must be specific, not a generic "no source files".
r = mkrepo("def add(a,b):\n    return a + b\n", "def add(a,b):\n    return a + b\n", SMOKE)
root = gitq(r, "rev-list", "--max-parents=0", "HEAD").stdout.split()[0]
check("root commit is refused", run_rf(r, root), 2)
gitq(r, "checkout", "-q", "-b", "side", root)
(r / "side.txt").write_text("x"); gitq(r, "add", "-A"); gitq(r, "commit", "-qm", "side")
gitq(r, "checkout", "-q", "-")
gitq(r, "merge", "-q", "--no-ff", "side", "-m", "merge")
out = subprocess.run([sys.executable, str(RF), "HEAD", "--test", CMD, "--repo", str(r)],
                     capture_output=True, text=True)
check("merge commit is refused", out.returncode, 2)
check("merge refusal says WHY", int("merge commit" in out.stderr), 1)
shutil.rmtree(r, ignore_errors=True)

# 7. A --test command that writes to the source (a formatter in the suite) must not be
#    snapshotted in its rewritten state and then "restored" to it under a message
#    promising the pre-run contents.
r = mkrepo("def add(a,b):\n    return a - b\n", "def add(a,b):\n    return a + b\n", TEST)
pre = (r / "src/m.py").read_text()
subprocess.run([sys.executable, str(RF), "HEAD", "--test",
                "python3 -c \"open('src/m.py','a').write('# CLOBBER\\n')\"",
                "--repo", str(r)], capture_output=True, text=True)
check("a source-clobbering test leaves the tree intact",
      int((r / "src/m.py").read_text() == pre), 1)
shutil.rmtree(r, ignore_errors=True)


print(f"\n{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILED: ' + ', '.join(FAILS)}")
sys.exit(0 if not FAILS else 1)
