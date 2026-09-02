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

print(f"\n{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILED: ' + ', '.join(FAILS)}")
sys.exit(0 if not FAILS else 1)
