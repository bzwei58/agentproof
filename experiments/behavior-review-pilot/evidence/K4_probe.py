import shutil
import subprocess
import unittest

from probe_support import ProbeCase

STORE = ".agentproof"


class RestoreValidationTests(ProbeCase):
    def snapshot_file(self, ident, name):
        return self.root / STORE / "snapshots" / ident / "files" / name

    def test_directory_conflict_refusal_leaves_all_project_files_unchanged(self):
        ident = self.start({"a.py": "one\n", "b.py": "two\n"})
        self.write("a.py", "edited\n")
        (self.root / "b.py").unlink()
        (self.root / "b.py").mkdir()
        (self.root / "b.py" / "keep.txt").write_text("inner\n")
        code, _ = self.cli("restore", ident)
        self.assertNotEqual(code, 0)
        code, _ = self.cli("restore", ident, "--apply")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.read("a.py"), "edited\n")
        self.assertEqual(self.read("b.py/keep.txt"), "inner\n")

    def test_blocked_parent_path_refused_before_any_write(self):
        ident = self.start({"src/util.py": "helper\n", "top.py": "top\n"})
        self.write("top.py", "edited\n")
        shutil.rmtree(self.root / "src")
        self.write("src", "now a file\n")
        code, _ = self.cli("restore", ident, "--apply")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.read("top.py"), "edited\n")
        self.assertEqual(self.read("src"), "now a file\n")

    def test_corrupted_snapshot_content_refused_before_any_write(self):
        ident = self.start({"a.py": "one\n", "b.py": "two\n"})
        self.write("a.py", "edited\n")
        self.snapshot_file(ident, "b.py").write_text("tampered\n")
        code, _ = self.cli("restore", ident, "--apply")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.read("a.py"), "edited\n")
        self.assertEqual(self.read("b.py"), "two\n")

    def test_symlink_target_refusal_preserves_symlink_and_victim(self):
        ident = self.start({"app.py": "v1\n", "outside.txt": "safe\n"})
        (self.root / "app.py").unlink()
        (self.root / "app.py").symlink_to(self.root / "outside.txt")
        code, _ = self.cli("restore", ident, "--apply")
        self.assertNotEqual(code, 0)
        self.assertTrue((self.root / "app.py").is_symlink())
        self.assertEqual(self.read("outside.txt"), "safe\n")

    def test_missing_directories_recreated_and_later_files_kept(self):
        ident = self.start({"src/deep/util.py": "helper\n", "root.txt": "r\n"})
        shutil.rmtree(self.root / "src")
        self.write("later.txt", "keep\n")
        code, _ = self.cli("restore", ident, "--apply")
        self.assertEqual(code, 0)
        self.assertEqual(self.read("src/deep/util.py"), "helper\n")
        self.assertEqual(self.read("root.txt"), "r\n")
        self.assertEqual(self.read("later.txt"), "keep\n")

    def test_git_index_unchanged_by_refused_and_applied_restore(self):
        self.write("tracked.txt", "base\n")
        # git plumbing only: list argv, no shell.
        subprocess.run(["git", "-C", str(self.root), "add", "tracked.txt"], check=True)
        ident = self.start({"tracked.txt": "base\n", "a.py": "one\n"})
        index_before = (self.root / ".git" / "index").read_bytes()
        staged_before = subprocess.check_output(
            ["git", "-C", str(self.root), "ls-files", "--stage"])
        (self.root / "a.py").unlink()
        (self.root / "a.py").mkdir()
        (self.root / "a.py" / "x.txt").write_text("x\n")
        code, _ = self.cli("restore", ident, "--apply")
        self.assertNotEqual(code, 0)
        self.assertEqual((self.root / ".git" / "index").read_bytes(), index_before)
        shutil.rmtree(self.root / "a.py")
        self.write("a.py", "edited\n")
        code, _ = self.cli("restore", ident, "--apply")
        self.assertEqual(code, 0)
        self.assertEqual(self.read("a.py"), "one\n")
        self.assertEqual((self.root / ".git" / "index").read_bytes(), index_before)
        self.assertEqual(subprocess.check_output(
            ["git", "-C", str(self.root), "ls-files", "--stage"]), staged_before)
