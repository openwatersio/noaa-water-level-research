"""A single append-only evidence/report checkout, separate from executable code."""

from pathlib import Path, PurePosixPath
import subprocess

from pipeline.archive import immutable


class GitStore:
    def __init__(self, path, remote, branch):
        if branch not in ('data', 'reports'):
            raise ValueError('not a data branch')
        self.path, self.remote, self.branch = Path(path), remote, branch

    def git(self, *args, check=True):
        command = ['git', '-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential',
                   '-C', str(self.path), *args]
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
        if check and result.returncode:
            raise RuntimeError(f'git {args[0]} failed: {result.stderr[-2000:]}')
        return result

    def open(self):
        self.path.mkdir(parents=True, exist_ok=True)
        if (self.path / '.git').exists():
            if (self.git('remote', 'get-url', 'origin').stdout.strip() != self.remote or
                    self.git('branch', '--show-current').stdout.strip() != self.branch):
                raise ValueError('existing checkout identity differs')
            return
        self.git('init', '-b', self.branch)
        self.git('config', 'user.name', 'NOAA research collector')
        self.git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
        self.git('remote', 'add', 'origin', self.remote)
        refs = self.git('ls-remote', '--heads', 'origin', f'refs/heads/{self.branch}').stdout
        if refs.strip():
            self.git('fetch', '--depth=1', 'origin', self.branch)
            self.git('checkout', '-B', self.branch, 'FETCH_HEAD')

    def write(self, name, body):
        p = PurePosixPath(name)
        if p.is_absolute() or '..' in p.parts or str(p) != name or '.git' in p.parts:
            raise ValueError('unsafe Git store path')
        immutable(self.path / name, body)

    def commit(self, message):
        self.git('add', '--all')
        if self.git('diff', '--cached', '--quiet', check=False).returncode:
            self.git('commit', '-m', message)
        for attempt in range(3):
            result = self.git('push', 'origin', f'HEAD:refs/heads/{self.branch}', check=False)
            if result.returncode == 0:
                return self.git('rev-parse', 'HEAD').stdout.strip()
            if attempt < 2:
                self.git('fetch', 'origin', self.branch)
                self.git('rebase', 'FETCH_HEAD')
        raise RuntimeError('Git publication failed; local commits retained for recovery')
