import shutil
import unittest

from probe_support import ProbeCase


class RestoreValidationTests(ProbeCase):
    def _tamper_stored_bytes(self, old, new):
        replaced = False
        for path in sorted((self.root / ".agentproof").rglob("*")):
            if path.is_file() and path.read_bytes() == old:
                path.write_bytes(new)
                replaced = True
        return replaced

    def test_corrupt_snapshot_refusal_leaves_all_project_files_unchanged(self):
        cid = self.start({"a.py": "A original", "b.py": "B original"})
        self.write("a.py", "A changed")
        self.assertTrue(
            self._tamper_stored_bytes(b"B original", b"B tampered"),
            "precondition: snapshot blob for b.py located and corrupted")
        code, _ = self.cli("restore", cid, "--apply")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.read("a.py"), "A changed")
        self.assertEqual(self.read("b.py"), "B original")

    def test_parent_path_replaced_by_file_refusal_changes_nothing(self):
        (self.root / "d").mkdir()
        cid = self.start({"a.py": "A original", "d/b.py": "B original"})
        self.write("a.py", "A changed")
        shutil.rmtree(self.root / "d")
        (self.root / "d").write_text("user data replacing the directory")
        code, _ = self.cli("restore", cid, "--apply")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.read("a.py"), "A changed")
        self.assertTrue((self.root / "d").is_file())
        self.assertEqual((self.root / "d").read_text(),
                         "user data replacing the directory")

    def test_directory_where_snapshot_file_expected_refusal_changes_nothing(self):
        cid = self.start({"a.py": "A original", "b.py": "B original"})
        self.write("a.py", "A changed")
        (self.root / "b.py").unlink()
        (self.root / "b.py").mkdir()
        (self.root / "b.py" / "nested.txt").write_text("inside")
        code, _ = self.cli("restore", cid, "--apply")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.read("a.py"), "A changed")
        self.assertTrue((self.root / "b.py" / "nested.txt").exists())
        self.assertEqual((self.root / "b.py" / "nested.txt").read_text(), "inside")

    def test_missing_directory_is_recreated(self):
        (self.root / "src").mkdir()
        cid = self.start({"src/app.py": "app original"})
        shutil.rmtree(self.root / "src")
        code, _ = self.cli("restore", cid, "--apply")
        self.assertEqual(code, 0)
        self.assertEqual(self.read("src/app.py"), "app original")

    def test_post_snapshot_files_kept_and_git_index_untouched(self):
        cid = self.start({"a.py": "A original"})
        self.write("a.py", "A changed")
        self.write("new.py", "keep me")
        index = self.root / ".git" / "index"
        before = index.read_bytes() if index.exists() else None
        code, _ = self.cli("restore", cid, "--apply")
        self.assertEqual(code, 0)
        self.assertEqual(self.read("a.py"), "A original")
        self.assertEqual(self.read("new.py"), "keep me")
        after = index.read_bytes() if index.exists() else None
        self.assertEqual(after, before)
