#!/usr/bin/env python3
"""End-to-end tests on throwaway git repositories. Both directions, because a checker
whose positive and negative cases coincide cannot fail."""
import importlib.util
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time

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


def refused(name, repo, commit, why):
    """A refusal is exit 2 AND the reason. Exit 2 alone is also what any run gets when the
    baseline cannot start, so on a machine without pytest these checks all passed while
    nothing was being tested."""
    out = subprocess.run([sys.executable, str(RF), commit, "--test", CMD, "--repo", str(repo)],
                         capture_output=True, text=True)
    check(name, int(out.returncode == 2 and why in out.stderr), 1)


# Every fixture runs pytest. Without it the baseline fails, every run exits 2, and each check
# expecting 2 passed: 7 of 13 "passed" on a machine with no pytest. Stop instead.
if subprocess.run("python3 -m pytest --version", shell=True, capture_output=True).returncode:
    sys.exit("The fixture repositories run pytest under `python3`: pip install pytest")


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
refused("unknown commit exits 2, saying so", r, "deadbeefdeadbeef", "is not a commit")
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
refused("non-HEAD commit is refused in place, saying so", r, target, "is not HEAD")
got = subprocess.run([sys.executable, str(RF), target, "--worktree", "--test", CMD,
                      "--repo", str(r)], capture_output=True, text=True).returncode
check("--worktree gives the TRUE verdict on that commit", got, 1)
shutil.rmtree(r, ignore_errors=True)

# 6. Structural refusals must be specific, not a generic "no source files".
r = mkrepo("def add(a,b):\n    return a + b\n", "def add(a,b):\n    return a + b\n", SMOKE)
root = gitq(r, "rev-list", "--max-parents=0", "HEAD").stdout.split()[0]
refused("root commit is refused, saying so", r, root, "root commit")
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



# --- round 2 --------------------------------------------------------------------------

# 8. Without -z, git QUOTES any path outside ASCII: `src/mod\u00e8le.py` comes back as the
#    literal escaped string "src/mod\\303\\250le.py", which names no file. The revert
#    silently did nothing and a correct fix with a correct test was reported DOES NOT
#    DISCRIMINATE -- a false negative telling you a good test is worthless. Paths with
#    spaces were never affected; accents are, which for a French-language repo is routine.
r = pathlib.Path(tempfile.mkdtemp())
(r / "src").mkdir(); (r / "tests").mkdir()
g = lambda *a: subprocess.run(["git", "-C", str(r), *a], capture_output=True)
g("init", "-q", "."); g("config", "user.email", "t@t"); g("config", "user.name", "t")
acc = "src/mod\u00e8le.py"
(r / acc).write_text("def add(a,b):\n    return a - b\n")
(r / "tests/test_m.py").write_text(
    "import os, importlib.util\n"
    "def test_add():\n"
    "    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), %r)\n"
    "    spec = importlib.util.spec_from_file_location('m', p)\n"
    "    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
    "    assert m.add(2,3) == 5\n" % acc)
g("add", "-A"); g("commit", "-qm", "before")
(r / acc).write_text("def add(a,b):\n    return a + b\n")
g("add", "-A"); g("commit", "-qm", "after")
check("a non-ASCII path is reverted, not skipped", run_rf(r), 0)
shutil.rmtree(r, ignore_errors=True)

# 9. A --source path is used to unlink and rewrite files. One that escapes the repo would do
#    that to something the user never pointed this at, and a run killed between the unlink
#    and the restore loses it outright.
r = mkrepo("def add(a,b):\n    return a - b\n", "def add(a,b):\n    return a + b\n", TEST)
esc = subprocess.run([sys.executable, str(RF), "HEAD", "--test", CMD, "--repo", str(r),
                      "--source", "../" * 12 + "tmp/x"], capture_output=True, text=True)
check("--source escaping the repo is refused, saying so",
      int(esc.returncode == 2 and "must stay inside" in esc.stderr), 1)
inside = subprocess.run([sys.executable, str(RF), "HEAD", "--test", CMD, "--repo", str(r),
                         "--source", "src/m.py"], capture_output=True, text=True)
check("--source inside the repo still works", inside.returncode, 0)
shutil.rmtree(r, ignore_errors=True)


# --- 2026-09-28 audit ------------------------------------------------------------------

# 10. The reverted run compiles the PARENT's source into src/__pycache__, and the restore puts
#     back a file of the same size, usually inside the same whole second -- so that bytecode is
#     a valid cache for the restored file and the user's NEXT test run executes the old code.
#     8 runs in 10 left a correct HEAD failing its own test. Nothing the reverted run compiled
#     may outlive it.
r = mkrepo("def add(a,b):\n    return a - b\n", "def add(a,b):\n    return a + b\n", TEST)
run_rf(r)
check("no bytecode from the reverted run survives it", len(list((r / "src").rglob("*.pyc"))), 0)
shutil.rmtree(r, ignore_errors=True)

# 11. THE SAME FALSE GREEN AS 5, AT HEAD. In place the tests come from the working tree, so an
#     uncommitted edit to the commit's own test -- the negative case being written right now --
#     was credited to the commit. HEAD shipped a tautological test; the tool said DISCRIMINATES.
r = mkrepo("def is_admin(u):\n    return True\n", "def is_admin(u):\n    return True\n",
           "def test_placeholder():\n    pass\n")
(r / "src/m.py").write_text("def is_admin(u):\n    return u.get('role')=='admin'\n")
(r / "tests/test_m.py").write_text(
    "from src.m import is_admin\ndef test_yes():\n    assert is_admin({'role':'admin'})\n")
gitq(r, "commit", "-qam", "fix + a test that cannot fail")
(r / "tests/test_m.py").write_text((r / "tests/test_m.py").read_text() +
    "def test_no():\n    assert not is_admin({'role':'guest'})\n")
out = subprocess.run([sys.executable, str(RF), "HEAD", "--test", CMD, "--repo", str(r)],
                     capture_output=True, text=True)
check("an uncommitted edit to the commit's own test is refused",
      int(out.returncode == 2 and "tests/test_m.py" in out.stderr), 1)
shutil.rmtree(r, ignore_errors=True)

# 12. With -z, a rename's ORIGINAL path arrives as a field of its own with no status prefix.
#     Slicing it like the others cut three characters off, so a staged `git mv` of a file this
#     would revert was never seen as uncommitted work.
r = mkrepo("def add(a,b):\n    return a - b\n", "def add(a,b):\n    return a + b\n", TEST)
gitq(r, "mv", "src/m.py", "src/m2.py")
out = subprocess.run([sys.executable, str(RF), "HEAD", "--test", "true", "--repo", str(r)],
                     capture_output=True, text=True)
check("a staged rename of a file it would revert is refused",
      int(out.returncode == 2 and "src/m.py" in out.stderr), 1)
shutil.rmtree(r, ignore_errors=True)

# 13. `git show` detects renames and lists only the NEW path. A commit that moved util.py to
#     helpers.py (and touched nothing that matters) was "reverted" by deleting helpers.py
#     without putting util.py back, so everything importing it went red for a reason the
#     commit never introduced: DISCRIMINATES about a test that passes against the parent.
r = mkrepo("def add(a,b):\n    return a + b\n", "def add(a,b):\n    return a + b\n", SMOKE)
(r / "src/util.py").write_text("def two():\n    return 2\n")
(r / "src/core.py").write_text("from src.util import two\ndef f():\n    return two()\n")
(r / "tests/test_core.py").write_text("from src.core import f\ndef test_f():\n    assert f() in (2, 3)\n")
gitq(r, "add", "-A"); gitq(r, "commit", "-qm", "core")
gitq(r, "mv", "src/util.py", "src/helpers.py")
(r / "src/core.py").write_text("from src.helpers import two\ndef f():\n    return two()\n")
gitq(r, "commit", "-qam", "refactor: rename util to helpers")
got = subprocess.run([sys.executable, str(RF), "HEAD", "--test",
                      "python3 -m pytest tests/test_core.py -q", "--repo", str(r)],
                     capture_output=True, text=True).returncode
check("a rename is reverted as a rename, not as a deletion", got, 1)
shutil.rmtree(r, ignore_errors=True)

# 14. A submodule's old code lives in another repository and cannot be reverted from here.
#     It crashed reading the submodule directory as a file, and a crash exits 1: the code
#     for DOES NOT DISCRIMINATE.
lib = pathlib.Path(tempfile.mkdtemp())
gitq(lib, "init", "-q", "."); gitq(lib, "config", "user.email", "t@t"); gitq(lib, "config", "user.name", "t")
(lib / "v.py").write_text("V = 1\n"); gitq(lib, "add", "-A"); gitq(lib, "commit", "-qm", "v1")
r = mkrepo("def add(a,b):\n    return a - b\n", "def add(a,b):\n    return a + b\n", TEST)
gitq(r, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(lib), "vendor/lib")
gitq(r, "commit", "-qm", "vendor lib")
(lib / "v.py").write_text("V = 2\n"); gitq(lib, "commit", "-qam", "v2")
gitq(r / "vendor/lib", "pull", "-q", "origin", "HEAD")
(r / "src/m.py").write_text("def add(a,b):\n    return a + b + 0\n")
gitq(r, "commit", "-qam", "bump lib and touch source")
out = subprocess.run([sys.executable, str(RF), "HEAD", "--test", CMD, "--repo", str(r)],
                     capture_output=True, text=True)
check("a commit that moves a submodule is refused, by name",
      int(out.returncode == 2 and "vendor/lib" in out.stderr and "submodule" in out.stderr), 1)
shutil.rmtree(r, ignore_errors=True); shutil.rmtree(lib, ignore_errors=True)

# 15. A fixture added with the fix lives in conftest.py, which was classified as SOURCE and
#     reverted: the new test then errored against the parent for want of its fixture, and that
#     error counted as red -- DISCRIMINATES about a docstring-only change. Test infrastructure
#     belongs with the tests.
r = mkrepo("def add(a,b):\n    return a + b\n", "def add(a,b):\n    return a + b\n", SMOKE)
(r / "src/m.py").write_text('def add(a,b):\n    """doc only"""\n    return a + b\n')
(r / "conftest.py").write_text("import pytest\n@pytest.fixture\ndef four():\n    return 4\n")
(r / "tests/test_m.py").write_text("from src.m import add\ndef test_add(four):\n    assert add(2,2)==four\n")
gitq(r, "add", "-A"); gitq(r, "commit", "-qm", "doc + a fixture-based test")
check("conftest.py travels with the tests, not the source", run_rf(r), 1)
shutil.rmtree(r, ignore_errors=True)

# 16. Misfiling a test as source reverts it and destroys the check, so every common runner's
#     convention must land on the TEST side -- and ordinary source must not.
spec = importlib.util.spec_from_file_location("redfirst_under_test", RF)
rfmod = importlib.util.module_from_spec(spec); spec.loader.exec_module(rfmod)
TESTS = ["src/parser.spec.ts", "src/__tests__/parser.js", "test/parser.js", "lib/foo_spec.rb",
         "src/test/java/FooTest.java", "App.Tests/FooTests.cs", "FooIT.java", "conftest.py",
         "jest.config.js", "pkg/testdata/in.json", "base/foo_unittest.cc", "tests/test_m.py"]
SOURCE = ["src/latest.py", "src/Contest.java", "src/attestation.py", "AUDIT.md", "src/m.py"]
wrong = [p for p in TESTS if rfmod.classify([p])[0]] + [p for p in SOURCE if rfmod.classify([p])[1]]
check("test conventions classify as tests, source as source" + (f" -- wrong: {wrong}" if wrong else ""),
      len(wrong), 0)

# 17. --repo (default: cwd) was assumed to be the top of the repository. Git reports paths from
#     the top, so from a subdirectory -- a service folder in a monorepo -- every file operation
#     pointed below it at nothing. A commit adding a new module was "reverted" by deleting a
#     file that did not exist, the test still passed, and a good test read as worthless.
r = pathlib.Path(tempfile.mkdtemp())
gitq(r, "init", "-q", "."); gitq(r, "config", "user.email", "t@t"); gitq(r, "config", "user.name", "t")
(r / "svc/src").mkdir(parents=True); (r / "svc/tests").mkdir()
(r / "svc/src/m.py").write_text("def add(a,b):\n    return a + b\n")
(r / "svc/tests/test_m.py").write_text(TEST)
gitq(r, "add", "-A"); gitq(r, "commit", "-qm", "svc")
(r / "svc/src/new.py").write_text("def mul(a,b):\n    return a * b\n")
(r / "svc/tests/test_new.py").write_text("from src.new import mul\ndef test_mul():\n    assert mul(3,4)==12\n")
gitq(r, "add", "-A"); gitq(r, "commit", "-qm", "feat: new module")
for extra in ([], ["--worktree"]):
    got = subprocess.run([sys.executable, str(RF), "HEAD", "--test", "python3 -m pytest tests/test_new.py -q",
                          "--repo", str(r / "svc"), *extra], capture_output=True, text=True).returncode
    check("from a subdirectory" + (" with --worktree" if extra else "") + ", a new module is reverted", got, 0)
shutil.rmtree(r, ignore_errors=True)

# 18. --source was compared with `git status` output as typed, so `./src/m.py` never matched
#     `src/m.py` and uncommitted work in it walked straight past the dirty-tree refusal.
r = mkrepo("def add(a,b):\n    return a - b\n", "def add(a,b):\n    return a + b\n", TEST)
(r / "src/m.py").write_text("def add(a,b):\n    return a + b  # uncommitted\n")
got = subprocess.run([sys.executable, str(RF), "HEAD", "--test", CMD, "--repo", str(r),
                      "--source", "./src/m.py"], capture_output=True, text=True).returncode
check("--source ./path with uncommitted work is refused", got, 2)
shutil.rmtree(r, ignore_errors=True)

# 19. A --source path that is a file in neither the commit nor its parent was taken for one the
#     commit ADDED and "reverted" by deleting it. A typo reverted nothing, the test passed, and
#     a good test read as worthless. A directory crashed -- and a crash exits 1, the same verdict.
r = mkrepo("def add(a,b):\n    return a - b\n", "def add(a,b):\n    return a + b\n", TEST)
for bad in ("src/mm.py", "src"):
    out = subprocess.run([sys.executable, str(RF), "HEAD", "--test", CMD, "--repo", str(r),
                          "--source", bad], capture_output=True, text=True)
    check(f"--source {bad} (no such file at the commit or its parent) is refused",
          int(out.returncode == 2 and "REFUSED" in out.stderr), 1)
shutil.rmtree(r, ignore_errors=True)

# 20. Interrupted DURING the reverted run. SIGTERM and SIGHUP kill without unwinding, so the
#     restore never ran: a CI timeout or a closed terminal left the PARENT's source in the tree.
#     And `git checkout <parent> -- src` had STAGED it, so the next `git commit` would ship
#     the revert. SIGTERM must unwind through the restore; SIGKILL cannot be caught, but must
#     still leave nothing in the index.
def interrupted(sig):
    r = mkrepo("def add(a,b):\n    return a - b\n", "def add(a,b):\n    return a + b\n", TEST)
    p = subprocess.Popen([sys.executable, str(RF), "HEAD", "--repo", str(r), "--test",
                          CMD + " || (touch reverted.flag; sleep 30)"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()
    while not (r / "reverted.flag").exists() and time.time() - t0 < 30:
        time.sleep(0.05)
    p.send_signal(sig); p.wait()
    return r
r = interrupted(signal.SIGTERM)
check("SIGTERM mid-run still restores the source",
      int("a + b" in (r / "src/m.py").read_text()), 1)
shutil.rmtree(r, ignore_errors=True)
r = interrupted(signal.SIGKILL)
check("SIGKILL mid-run leaves nothing staged",
      int(gitq(r, "diff", "--cached", "--name-only").stdout.strip() == ""), 1)
shutil.rmtree(r, ignore_errors=True)

# 21. "Restores the exact bytes it found" -- but not what else it found. write_bytes() kept the
#     mode of whatever was there, so a script the commit made executable, or added executable,
#     came back without +x; and exists() follows links, so a dangling symlink read as absent
#     and the restore deleted it. The tree must be exactly as it was.
r = mkrepo("def add(a,b):\n    return a - b\n", "def add(a,b):\n    return a + b\n", TEST)
(r / "run.sh").write_text("#!/bin/sh\n"); gitq(r, "add", "-A"); gitq(r, "commit", "-qm", "run.sh")
(r / "run.sh").write_text("#!/bin/sh\necho run\n"); (r / "run.sh").chmod(0o755)
(r / "tool.sh").write_text("#!/bin/sh\n"); (r / "tool.sh").chmod(0o755)
(r / "src/m.py").write_text("def add(a,b):\n    return a + b + 0\n")
os.symlink("../generated/cfg.py", r / "src/cfg.py")
gitq(r, "add", "-A"); gitq(r, "commit", "-qm", "modes and links")
run_rf(r)
left = gitq(r, "status", "--porcelain", "--untracked-files=no").stdout.split("\n")
check("modes and symlinks come back as they were" + (f" -- {[l for l in left if l]}" if any(left) else ""),
      len([l for l in left if l]), 0)
shutil.rmtree(r, ignore_errors=True)

# 22. An uncaught exception exits 1, and 1 is DOES NOT DISCRIMINATE: a crash read as a verdict
#     that a test is worthless. A filename that is not UTF-8 -- legacy Latin-1, legal on Linux --
#     crashed decoding git's output. Whatever breaks, the exit code must say "no verdict".
r = mkrepo("def add(a,b):\n    return a - b\n", "def add(a,b):\n    return a + b\n", TEST)
try:
    with open(os.fsencode(str(r / "src")) + b"/mod\xe8le.py", "w") as fh:
        fh.write("X = 1\n")
except OSError:
    print("  [SKIP] this filesystem refuses non-UTF-8 names")
else:
    (r / "src/m.py").write_text("def add(a,b):\n    return a + b + 0\n")
    gitq(r, "add", "-A"); gitq(r, "commit", "-qm", "latin-1 name")
    check("a crash exits 2, not the DOES NOT DISCRIMINATE code", run_rf(r), 2)
shutil.rmtree(r, ignore_errors=True)

# 23. Piped -- as in every CI log -- stdout is block-buffered, but the test command writes
#     straight to the descriptor. Both runs' output landed ABOVE redfirst's own header, so the
#     log could not say which run was the baseline and which the reverted one.
r = mkrepo("def add(a,b):\n    return a - b\n", "def add(a,b):\n    return a + b\n", TEST)
env = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}
out = subprocess.run([sys.executable, str(RF), "HEAD", "--test", CMD, "--repo", str(r)],
                     capture_output=True, text=True, env=env).stdout.splitlines()
first = lambda pred: next((i for i, l in enumerate(out) if pred(l)), len(out))
check("in a CI log, each run's output follows the line that announces it",
      int(first(lambda l: l.startswith("running")) < first(lambda l: "passed" in l)
          < first(lambda l: "re-running" in l) < first(lambda l: "failed" in l)), 1)
shutil.rmtree(r, ignore_errors=True)

print(f"\n{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILED: ' + ', '.join(FAILS)}")
sys.exit(0 if not FAILS else 1)
