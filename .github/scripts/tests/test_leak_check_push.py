"""Tests for leak-check.sh --push mode — the check the orchestrator runs before pushing HEAD.

The push-mode scan greps the lines a push of HEAD to <branch> would add, against $PATTERN plus a
WORKSTATION_PATTERN that applies to a push range only. It must fail closed: an unreachable remote,
an empty diff, or --push paired with --warn all exit 2, never a silent pass.

A throwaway repo commits one line at a time (amended, so the diff against the base is exactly that
line); a bare repo beside it is the remote; a copy of the script runs in `--push <url> <branch>`.
Literals are assembled at runtime so this file never matches the pattern it tests.

unittest, so pytest collects it with nothing installed beyond the standard library. Needs git.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "leak-check.sh"
#: An already-caught $PATTERN line: the positive control that proves the scan reports a hit.
PATTERN_CONTROL = "/root/" + ".secrets/probe"
#: The WORKSTATION_PATTERN alternatives, assembled so this file does not contain them literally.
WORKSTATION_ALTS = (
    "/opt/" + "lanes/",
    "/opt/" + "agentoolkit",
    "/tmp/" + "claude-",
    "/root/" + ".claude",
    # The ruling (2026-11-12) narrows the file:// alternative to file:///(opt|root|tmp)/, so the
    # probe must be a file URL into a workstation path; a bare file:// is no longer a hit.
    "file:///" + "opt/",
)
SYNTHETIC_WORKSTATION_PATTERN = (
    "|".join(WORKSTATION_ALTS[:4]).replace(".claude", r"\.claude")
    + "|file://(localhost)?/(opt|root|tmp)/"
)


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    )


class PushMode(unittest.TestCase):
    """A clean push exits 0; a leaked line exits 1; a broken check exits 2 (fails closed)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        # Keep the copy outside the scratch repo: the script names its own paths, and a tracked
        # copy would be scanned along with the fixture line.
        self.script = self.root / "leak-check.sh"
        shutil.copy2(SCRIPT, self.script)

        # The remote: a bare repo the work repo pushes its base commit to on main.
        self.remote = self.root / "remote.git"
        _git("init", "-q", "--bare", str(self.remote), cwd=self.root)

        # The work repo: one base commit, pushed to main, so ls-remote finds a base object.
        self.work = Path(tempfile.mkdtemp(dir=self.root))
        _git("init", "-q", "-b", "main", str(self.work), cwd=self.root)
        (self.work / "notes.txt").write_text("base\n")
        _git("add", "-A", cwd=self.work)
        _git("commit", "-q", "-m", "base", cwd=self.work)
        _git("push", "-q", str(self.remote), "HEAD:refs/heads/main", cwd=self.work)
        self.base_short = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=self.work,
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    def _commit_one(self, line: str) -> None:
        """Amend so HEAD holds exactly one added line against the base — a deterministic diff."""
        (self.work / "notes.txt").write_text("base\n" + line + "\n")
        _git("add", "-A", cwd=self.work)
        _git("commit", "-q", "--amend", "-m", "change", cwd=self.work)

    def _push(self, branch: str = "main", *extra: str, remote: str | None = None, env: dict | None = None) -> subprocess.CompletedProcess[str]:
        url = str(self.remote if remote is None else remote)
        env = dict(env or os.environ)
        env.setdefault("TSB_LEAK_PATTERN", PATTERN_CONTROL)
        env.setdefault("TSB_LEAK_PUSH_PATTERN", SYNTHETIC_WORKSTATION_PATTERN)
        return subprocess.run(
            ["bash", str(self.script), "--push", url, branch, *extra],
            cwd=self.work, capture_output=True, text=True, env=env,
        )

    def _fake_tool(self, name: str, body: str) -> dict:
        tools = self.root / "tools"
        tools.mkdir(exist_ok=True)
        tool = tools / name
        tool.write_text("#!/usr/bin/env bash\n" + body)
        tool.chmod(0o755)
        return {**os.environ, "PATH": str(tools) + os.pathsep + os.environ["PATH"]}

    def test_long_added_line_is_a_hit(self):
        self._commit_one(PATTERN_CONTROL + "x" * (1024 * 1024))
        # Model an early matching grep closing stdin before its writer finishes.
        grep = shlex.quote(shutil.which("grep"))
        body = (f'if [ "$1" = -qiE ]; then\n'
                '  if [ "$#" -ge 3 ]; then exec < "$3"; fi\n  IFS= read -r -n 64 prefix\n'
                f'  if [[ "$prefix" == *probe* ]]; then exit 0; fi\n'
                f'  printf "%s\\n" "$prefix" | {grep} "$@"\n'
                f'else exec {grep} "$@"; fi\n')
        proc = self._push(env=self._fake_tool("grep", body))
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr[-200:])

    def test_long_line_with_unwritable_tmpdir_never_reports_clean(self):
        self._commit_one(PATTERN_CONTROL + "x" * (128 * 1024))
        unwritable = self.root / "unwritable"
        unwritable.mkdir(mode=0o500)
        self.addCleanup(unwritable.chmod, 0o700)
        # Bash here-string storage still succeeds here; root also bypasses mode bits.
        # Use the permitted fake-tool fallback to force a temporary allocation failure.
        env = self._fake_tool("mktemp", "exit 1\n")
        env["TMPDIR"] = str(unwritable)
        proc = self._push(env=env)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr[-200:])
        self.assertNotIn("clean", proc.stdout)
        self.assertIn("temporary", proc.stderr)

    def test_added_line_write_failure_fails_closed(self):
        self._commit_one(PATTERN_CONTROL + "x" * (128 * 1024))
        real_mktemp = shlex.quote(shutil.which("mktemp"))
        full = self.root / "full"
        full.symlink_to("/dev/full")
        counter = shlex.quote(str(self.root / "allocations"))
        body = (f'count=0; [ ! -f {counter} ] || read -r count < {counter}\n'
                f'count=$((count+1)); echo "$count" > {counter}\n'
                f'if [ "$count" -eq 4 ]; then echo {shlex.quote(str(full))}; exit 0; fi\n'
                f'exec {real_mktemp} "$@"\n')
        proc = self._push(env=self._fake_tool("mktemp", body))
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr[-200:])
        self.assertIn("added-line", proc.stderr)
        self.assertNotIn("clean", proc.stdout)

    def test_mktemp_failure_fails_closed(self):
        self._commit_one("clean")
        proc = self._push(env=self._fake_tool("mktemp", "exit 1\n"))
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("temporary", proc.stderr)

    def test_added_line_grep_error_fails_closed(self):
        self._commit_one("clean")
        proc = self._push(env=self._fake_tool("grep", "exit 2\n"))
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("added-line", proc.stderr)

    def test_file_name_grep_error_fails_closed(self):
        (self.work / "empty").touch()
        self._commit_plain("empty file")
        proc = self._push(env=self._fake_tool("grep", "exit 2\n"))
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("file-name", proc.stderr)

    def test_partial_rev_list_failure_fails_closed(self):
        self._commit_one("clean")
        git = shlex.quote(shutil.which("git"))
        body = (f'if [ "$1" = rev-list ] && [ "$2" != --merges ]; then\n'
                f'  if [ /dev/stdout -ef /dev/null ]; then exec {git} "$@"; fi\n'
                f'  {git} rev-parse HEAD\n  exit 2\nfi\nexec {git} "$@"\n')
        proc = self._push(env=self._fake_tool("git", body))
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("rev-list", proc.stderr)

    def test_remote_credentials_are_not_in_errors(self):
        self._commit_one("clean")
        # A wrapper models git repeating its argument in a transport failure.
        git = shlex.quote(shutil.which("git"))
        body = (f'if [ "$1" = ls-remote ]; then\n'
                '  echo "$2" >&2\n  exit 2\nfi\n'
                f'exec {git} "$@"\n')
        for credentials in ("user:secret", "secret", "query"):
            with self.subTest(credentials=credentials):
                authority = "invalid"
                url = ("https://" + authority + "/repo?access_token=SECRET"
                       if credentials == "query"
                       else "https://" + credentials + "@" + authority + "/repo")
                proc = self._push(remote=url, env=self._fake_tool("git", body))
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertNotIn("secret", proc.stderr)
                self.assertNotIn("SECRET", proc.stderr)

    def test_a_clean_push_exits_zero_and_names_the_base(self):
        self._commit_one("relative/place is fine")
        proc = self._push()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn(f"clean against {self.base_short}", proc.stdout)

    def test_a_pattern_line_exits_one_and_is_named(self):
        self._commit_one(f"token file: {PATTERN_CONTROL}")
        proc = self._push()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn(PATTERN_CONTROL, proc.stderr)

    def test_each_workstation_alternative_exits_one(self):
        for alt in WORKSTATION_ALTS:
            with self.subTest(alt=alt):
                self._commit_one(f"path: {alt}probe")
                proc = self._push()
                self.assertEqual(proc.returncode, 1, f"{alt}: {proc.stdout + proc.stderr}")
                self.assertIn(alt, proc.stderr)

    def test_a_legitimate_osc7_file_url_exits_zero(self):
        # The vendored engine headers ("OSC 7 (file:// URI)") and OSC 7 test strings add lines like
        # this. The old bare `file://` alternative refused them, so this is red on d872bb9 (exit 1).
        # The ruling narrows the file:// alternative to file:///(opt|root|tmp)/, so a file URL into a
        # non-workstation path is a clean added line. Assembled so this file does not hold it literally.
        osc7 = "\x1b]7;file:" + "//" + "/work\x1b\\"
        self._commit_one(osc7)
        proc = self._push()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_a_file_url_into_a_workstation_path_exits_one_and_is_named(self):
        # A file URL that points into a workstation path is what the narrowed pattern must still catch.
        # The probe is assembled from WORKSTATION_ALTS[0] so this file never holds the literal path.
        probe = "file:" + "//" + WORKSTATION_ALTS[0] + "probe"
        self._commit_one(probe)
        proc = self._push()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn(probe, proc.stderr)

    def test_a_file_url_with_a_localhost_authority_into_a_workstation_path_exits_one_and_is_named(
        self,
    ):
        # The ruling (2026-11-12, Qodo round 1) makes the file:// alternative match both an empty
        # authority and a `localhost` authority: file://(localhost)?/(opt|root|tmp)/. A file URL
        # naming `localhost` is the same leak. The probe uses a made-up scratch directory under the
        # root (rig-diag-), so no other alternative matches it, and both the authority and the path
        # are assembled so this file never holds the literal probe.
        authority = "local" + "host"
        probe = "file:" + "//" + authority + "/" + "root/" + "rig-diag-" + "probe"
        self._commit_one(probe)
        proc = self._push()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn(probe, proc.stderr)

    def test_a_branch_absent_on_the_remote_is_checked_against_main(self):
        self._commit_one("relative/place is fine")
        proc = self._push("feature")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        # The base named in the output is main's short sha, not some feature ref.
        self.assertIn(f"clean against {self.base_short}", proc.stdout)

    def test_an_unreachable_remote_exits_two(self):
        self._commit_one("relative/place is fine")
        proc = self._push("main", remote=self.root / "nope.git")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        # Fail closed for the right reason: the base could not be resolved, so the scan did not run.
        self.assertIn("ls-remote", proc.stderr)
        self.assertIn("the scan did not run", proc.stderr)

    def test_an_empty_diff_exits_two(self):
        # No commit after the base push: HEAD equals the base, so there is nothing to push.
        proc = self._push()
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("nothing to push", proc.stderr)

    def test_push_with_warn_exits_two(self):
        self._commit_one("relative/place is fine")
        proc = self._push("main", "--warn")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        # Refused for the right reason, not by the generic usage line.
        self.assertIn("--warn does not apply to --push", proc.stderr)

    def _commit_plain(self, message: str) -> None:
        """Commit the staged tree as a new commit (not amended), so a range holds several commits."""
        _git("add", "-A", cwd=self.work)
        _git("commit", "-q", "-m", message, cwd=self.work)

    def test_a_path_added_in_one_commit_and_removed_in_the_next_exits_one(self):
        # The end state (HEAD vs base) is clean: the leak line is added then removed. Only a scan of
        # each commit by its own diff sees the commit that added it. A benign net change keeps the
        # end-state diff non-empty, so an end-state-only scan reports a clean push, not "nothing".
        leak = "path: " + WORKSTATION_ALTS[0] + "probe"
        (self.work / "notes.txt").write_text("base\n" + leak + "\n")
        (self.work / "keep.txt").write_text("kept\n")
        self._commit_plain("adds the leak line")
        (self.work / "notes.txt").write_text("base\nkept line\n")
        self._commit_plain("removes the leak line")
        proc = self._push()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn(WORKSTATION_ALTS[0], proc.stderr)

    def test_an_added_line_whose_content_starts_with_a_plus_exits_one(self):
        # File content that itself starts with '+' shows in the diff as '++...'. The old added-line
        # filter ('^\\+[^+]') skipped it; every added line counts now.
        self._commit_one("+" + WORKSTATION_ALTS[0] + "probe")
        proc = self._push()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn(WORKSTATION_ALTS[0], proc.stderr)

    def test_a_binary_file_holding_a_workstation_path_exits_one(self):
        # A unified diff of a binary file has no added text lines; the content is scanned at the commit.
        data = b"\x00blob\x00" + WORKSTATION_ALTS[0].encode() + b"probe\x00"
        (self.work / "blob.bin").write_bytes(data)
        self._commit_plain("adds a binary file")
        proc = self._push()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn(WORKSTATION_ALTS[0], proc.stderr)

    def test_a_binary_file_with_several_matching_lines_exits_one(self):
        # A binary whose content holds the workstation path on many lines. The old scan piped
        # `git cat-file -p | grep ... | head -1`: head exits after the first match, grep dies on the
        # broken pipe (SIGPIPE 141), and with pipefail that pipeline status read as a failed grep —
        # exit 2 "cannot check", not a hit. Writing the blob to a file and grepping it with -m1 fixes
        # it: exit 1, names the file. Many lines so grep's output exceeds the pipe buffer and head
        # closes it mid-stream; the NUL at the start makes git treat the file as binary (numstat '-').
        alt = WORKSTATION_ALTS[0]
        data = b"\x00" + b"".join(b"line %d %sprobe\n" % (i, alt.encode()) for i in range(5000))
        (self.work / "multi.bin").write_bytes(data)
        self._commit_plain("adds a multi-match binary")
        proc = self._push()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr[-200:])
        self.assertIn(alt, proc.stderr)
        self.assertIn("multi.bin", proc.stderr)

    def test_a_file_whose_new_name_holds_a_workstation_path_exits_one(self):
        # The file's content is clean; only its new name carries the path. A name scan (added, renamed,
        # copied) catches it: the recorded path nests the workstation lanes directory under a top dir,
        # so its path contains the pattern even though no added line does.
        name = "a" + WORKSTATION_ALTS[0] + "probe"
        (self.work / name).parent.mkdir(parents=True, exist_ok=True)
        (self.work / name).write_text("clean content\n")
        self._commit_plain("adds a file with a leaked name")
        proc = self._push()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn(name, proc.stderr)

    def _push_head_to_main(self) -> None:
        """Re-baseline: push HEAD to the remote's main, so the next push's range starts here."""
        _git("push", "-q", str(self.remote), "HEAD:refs/heads/main", cwd=self.work)

    def test_a_merge_commit_in_the_range_exits_two_and_names_it(self):
        # A merge's combined diff cannot be scanned truthfully line by line, and the orchestrator
        # pushes rebased branches. The range holds a merge (all content clean), so it fails closed:
        # exit 2 naming the merge commit's short sha. The end-state scan on HEAD's script saw no hit
        # and reported a clean push (exit 0), so this is red on 1c1f5fa.
        _git("checkout", "-q", "-b", "side", cwd=self.work)
        (self.work / "side.txt").write_text("side line\n")
        self._commit_plain("side commit")
        _git("checkout", "-q", "main", cwd=self.work)
        (self.work / "main.txt").write_text("main line\n")
        self._commit_plain("main commit")
        _git("merge", "-q", "--no-ff", "side", "-m", "merge side", cwd=self.work)
        merge_short = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=self.work,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        proc = self._push()
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("merge commit", proc.stderr)
        self.assertIn(merge_short, proc.stderr)

    def test_added_content_starting_with_two_pluses_exits_one(self):
        # File content that itself starts with '++ ' shows in the diff as '+++ <path>'. On HEAD's
        # script a '+++ ' line was treated as a file header anywhere it appeared, so this added
        # content was skipped. A '+++ ' line is a header only inside a file's header block; inside a
        # hunk every '+' line is content and is scanned, so the leak is caught (red on 1c1f5fa).
        self._commit_one("++ " + WORKSTATION_ALTS[0] + "probe")
        proc = self._push()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn(WORKSTATION_ALTS[0], proc.stderr)

    def test_a_push_that_only_deletes_a_binary_exits_zero(self):
        # A binary added at the base, then deleted in HEAD: the only change is a deletion. HEAD's
        # script read the deletion's numstat '-' row and ran git cat-file on the path at that commit,
        # which does not exist — exit 2 "cannot check". Deletions are skipped (status D, new id all
        # zeros), so a clean delete-only push exits 0 (red on 1c1f5fa).
        (self.work / "blob.bin").write_bytes(b"\x00bin\x00relative/clean\x00")
        self._commit_plain("adds a clean binary")
        self._push_head_to_main()
        (self.work / "blob.bin").unlink()
        self._commit_plain("deletes the binary")
        proc = self._push()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_a_renamed_and_changed_binary_exits_one(self):
        # A binary added at the base, then renamed and its content changed. HEAD's script read the
        # rename as a delete of the source path plus an add, ran git cat-file on the deleted source
        # path at that commit (which does not exist), and exited 2. The destination path is what the
        # name scan reads; the content is read by the new blob id, so the leak in the renamed file's
        # content is caught (red on 1c1f5fa).
        alt = WORKSTATION_ALTS[0]
        (self.work / "blob.bin").write_bytes(b"\x00bin\x00" + alt.encode() + b"probe\x00")
        self._commit_plain("adds a binary")
        self._push_head_to_main()
        (self.work / "blob.bin").rename(self.work / "renamed.bin")
        (self.work / "renamed.bin").write_bytes(b"\x00bin2\x00" + alt.encode() + b"probe2\x00")
        _git("add", "-A", cwd=self.work)
        self._commit_plain("renames and changes the binary")
        proc = self._push()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn(alt, proc.stderr)
        self.assertIn("renamed.bin", proc.stderr)

    def test_a_binary_with_a_tab_and_quote_in_its_name(self):
        # An unusual name: a tab and a double quote. Records are NUL-separated, so no path is decoded
        # or unquoted: the real name is read. HEAD's script matched numstat's quoted path against
        # git cat-file -p "$c:<path>", which failed on the quoted name — exit 2 for both subtests (red
        # on 1c1f5fa). Content read by blob id: a hit exits 1 naming the file; clean content exits 0.
        tab = chr(9)
        quote = chr(34)
        name = "weird" + tab + "name" + quote + ".bin"
        alt = WORKSTATION_ALTS[0]
        for hit in (True, False):
            with self.subTest(hit=hit):
                content = b"\x00blob\x00" + (alt.encode() + b"probe\x00" if hit else b"relative/clean\x00")
                (self.work / name).write_bytes(content)
                self._commit_plain("adds an unusual-named binary")
                proc = self._push()
                if hit:
                    self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                    self.assertIn(alt, proc.stderr)
                    self.assertIn(name, proc.stderr)
                else:
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                # Re-baseline so the next subtest's range holds only its own commit.
                self._push_head_to_main()

    def test_a_file_marked_no_diff_in_gitattributes_is_still_scanned(self):
        # A text file marked -diff in .gitattributes: git show -p prints "Binary files ... differ"
        # with no added lines, so HEAD's line scan misses a workstation path added there, and the
        # blob scan skips it too (no NUL byte). The patch is now produced with --text --no-textconv
        # --no-ext-diff, so the added lines show and are scanned. Red on 3cd8da9: exit 0 clean.
        (self.work / ".gitattributes").write_text("probe.txt -diff\n")
        (self.work / "probe.txt").write_text("base line\npath: " + WORKSTATION_ALTS[0] + "probe\n")
        self._commit_plain("adds a -diff-marked file with a leak")
        proc = self._push()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn(WORKSTATION_ALTS[0], proc.stderr)
        self.assertIn("probe.txt", proc.stderr)

    def _side_pin(self, message: str) -> str:
        """A commit id in a side repo, absent from the work repo — what a gitlink points at."""
        side = self.root / "side"
        if not (side / ".git").exists():
            _git("init", "-q", "-b", "main", str(side), cwd=self.root)
        _git("commit", "-q", "--allow-empty", "-m", message, cwd=side)
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=side, capture_output=True, text=True, check=True,
        ).stdout.strip()

    def test_a_push_that_only_moves_a_submodule_pin_exits_zero(self):
        # A gitlink (mode 160000) is the commit a submodule is pinned to; it lives in another
        # repository, so its id is absent from this one. HEAD's script ran git cat-file -p on that
        # new blob id, which fails — exit 2 "cannot check". A raw record whose new mode is 160000 is
        # skipped by the blob scan; its path is read by the name scan. A pin move (status M, not an
        # add/rename/copy) adds no name to scan, so a clean move exits 0. Red on 3cd8da9: exit 2.
        pin_a = self._side_pin("pin A")
        _git("update-index", "--add", "--cacheinfo", f"160000,{pin_a},program", cwd=self.work)
        _git("commit", "-q", "-m", "adds the program gitlink", cwd=self.work)
        self._push_head_to_main()          # re-baseline: the add commit becomes the base
        pin_b = self._side_pin("pin B")
        _git("update-index", "--cacheinfo", f"160000,{pin_b},program", cwd=self.work)
        _git("commit", "-q", "-m", "moves the program pin", cwd=self.work)
        proc = self._push()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
