import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest


def git(path, *args):
    return subprocess.check_output(['git', '-C', str(path), *args], stderr=subprocess.STDOUT).decode().strip()


class GitStoreTests(unittest.TestCase):
    def test_append_only_commit_recovers_nonconflicting_remote_push(self):
        self.assertIsNotNone(importlib.util.find_spec('pipeline.gitstore'), 'Git persistence missing')
        from pipeline.gitstore import GitStore
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            remote = p / 'remote.git'; remote.mkdir()
            git(remote, 'init', '--bare')
            a = GitStore(p / 'a', str(remote), 'data')
            a.open(); a.write('runs/a.json', b'first'); a.commit('first')
            b = GitStore(p / 'b', str(remote), 'data'); b.open()
            a.write('runs/second.json', b'second'); a.commit('second')
            b.write('runs/third.json', b'third'); b.commit('third')
            self.assertEqual(git(remote, 'show', 'data:runs/second.json'), 'second')
            self.assertEqual(git(remote, 'show', 'data:runs/third.json'), 'third')
            with self.assertRaises(ValueError): b.write('runs/a.json', b'changed')

    def test_no_change_does_not_create_commit(self):
        self.assertIsNotNone(importlib.util.find_spec('pipeline.gitstore'), 'Git persistence missing')
        from pipeline.gitstore import GitStore
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp); remote = p / 'remote.git'; remote.mkdir(); git(remote, 'init', '--bare')
            a = GitStore(p / 'a', str(remote), 'data'); a.open()
            a.write('runs/a.json', b'first'); a.commit('first')
            before = git(remote, 'rev-parse', 'data')
            a.commit('nothing')
            self.assertEqual(git(remote, 'rev-parse', 'data'), before)
