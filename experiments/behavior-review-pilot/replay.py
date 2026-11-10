"""Known-answer replay, deliberately separate from model discovery and scoring."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile


def replay():
    from agentproof.cli import main
    def cli(*args):
        output=io.StringIO()
        with contextlib.redirect_stdout(output),contextlib.redirect_stderr(output):
            code=main(list(args))
        return code,output.getvalue()
    previous=Path.cwd()
    with tempfile.TemporaryDirectory(prefix='agentproof-known-replay-') as tmp:
        root=Path(tmp).resolve()
        subprocess.run(['git','init','-q',str(root)],check=True,capture_output=True)
        os.chdir(root)
        try:
            (root/'a.txt').write_text('snapshot version',encoding='utf-8')
            (root/'z-folder').mkdir()
            (root/'z-folder/worker.py').write_text('snapshot module',encoding='utf-8')
            code,_=cli('init','--goal','Check the frozen restore contract')
            assert code==0
            ident=json.loads((root/'.agentproof/task.json').read_text())['checkpoints'][0]['id']
            (root/'a.txt').write_text('important later edits',encoding='utf-8')
            (root/'z-folder/worker.py').unlink()
            (root/'z-folder').rmdir()
            (root/'z-folder').write_text('blocking file',encoding='utf-8')
            before={name:(root/name).read_text() for name in ('a.txt','z-folder')}
            code,output=cli('restore',ident,'--apply')
            after={name:(root/name).read_text() for name in ('a.txt','z-folder')}
            return {'exit_code':code,'before':before,'after':after,'workspace_unchanged':before==after,
                    'stdout':output,'scope':'known-answer historical replay, not model-discovered evidence'}
        finally:
            os.chdir(previous)


if __name__=='__main__':
    print(json.dumps(replay(),ensure_ascii=False,indent=2))
