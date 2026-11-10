"""Small execution adapter; no scenario-specific assertions or defect hints."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class ProbeCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agentproof-behavior-probe-')
        self.root = Path(self.temp.name).resolve()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True, capture_output=True)
        self.previous = Path.cwd()
        os.chdir(self.root)

    def tearDown(self):
        os.chdir(self.previous)
        self.temp.cleanup()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def read(self, name):
        return (self.root / name).read_text(encoding='utf-8')

    def cli(self, *args):
        from agentproof.cli import main
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = main(list(args))
        return code, output.getvalue()

    def start(self, files):
        for name, text in files.items():
            self.write(name, text)
        code, output = self.cli('init', '--goal', 'Verify requested behavior')
        self.assertEqual(code, 0, output)
        return json.loads((self.root / '.agentproof/task.json').read_text(encoding='utf-8'))['checkpoints'][0]['id']
