import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest


class MemoryGitHub:
    def __init__(self):
        self.releases = {}
        self.blobs = {}
        self.corrupt = False

    def ensure_release(self, tag, target):
        if tag not in self.releases:
            self.releases[tag] = {"id": len(self.releases) + 1, "tag_name": tag, "draft": True,
                                  "immutable": False, "assets": []}
        return self.releases[tag]

    def upload(self, release, name, body):
        for asset in release["assets"]:
            if asset["name"] == name:
                if self.blobs[asset["id"]] != body:
                    raise ValueError("asset conflict")
                return asset
        if not release["draft"]:
            raise ValueError("immutable release")
        asset = {"id": len(self.blobs) + 1, "name": name, "size": len(body)}
        self.blobs[asset["id"]] = body
        release["assets"].append(asset)
        return asset

    def download(self, asset):
        return b"corrupt" if self.corrupt else self.blobs[asset["id"]]

    def publish(self, release):
        release.update(draft=False, immutable=True)
        return release

    def get_release(self, tag):
        return self.releases[tag]


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("pipeline.archive"), "archive implementation missing")
        from pipeline import archive
        self.archive = archive
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "original"
        self.data = Path(self.temp.name) / "data"
        self.root.mkdir(); self.data.mkdir()
        self.gh = MemoryGitHub()
        self.context = {"codeCommit": "a" * 40, "origin": "legacy", "batchId": "example"}

    def add(self, name, body):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
        return p

    def writer(self, batch="example"):
        return self.archive.Archive(self.root, self.data, self.gh,
                                    {**self.context, "batchId": batch})

    def test_roundtrip_preserves_binary_json_partial_and_revisions(self):
        self.add("forecasts/bodies/abc.bin", bytes(range(256)))
        self.add("observations/attempt-a.json", b'{"receivedAt":null,"state":"partial"}\n')
        self.add("observations/attempt-b.json", b'{"receivedAt":"2026-10-21T12:00:00Z"}\n')
        a = self.writer(); a.checkpoint(); record = a.finish()
        output = self.data.parent / "restored"
        self.archive.materialize(self.data, output, self.gh)
        for p in self.root.rglob('*'):
            if p.is_file(): self.assertEqual(p.read_bytes(), (output / p.relative_to(self.root)).read_bytes())
        self.assertEqual(record["context"]["origin"], "legacy")
        self.assertTrue(self.gh.get_release(record["tag"])["immutable"])

    def test_duplicate_content_paths_skip_upload_but_keep_new_receipts(self):
        self.add("forecasts/bodies/abc.bin", b"original")
        self.writer().finish()
        self.add("ticks/second.json", b'{"attemptId":"second"}')
        second = self.writer("second").finish()
        self.assertEqual(list(second["files"]), ["ticks/second.json"])

    def test_conflicting_existing_file_never_overwrites(self):
        p = self.add("ticks/first.json", b"original")
        self.writer().finish(); p.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "conflict"):
            self.writer("other").checkpoint()

    def test_readback_mismatch_does_not_publish(self):
        self.add("ticks/first.json", b"original")
        a = self.writer(); self.gh.corrupt = True
        with self.assertRaises(ValueError): a.finish()
        self.assertTrue(all(r["draft"] for r in self.gh.releases.values()))
        self.assertFalse(list((self.data / "catalog").glob('*.json')))

    def test_context_upload_failure_is_retried_before_products(self):
        a = self.writer()
        upload = self.gh.upload
        def fail(*args): raise OSError('upload unavailable')
        self.gh.upload = fail
        with self.assertRaises(OSError): a.start()
        self.gh.upload = upload
        a.finish()
        self.assertIn('context.json', [x['name'] for x in self.gh.get_release(a.tag)['assets']])

    def test_restore_rejects_symlink_parent(self):
        outside = self.data.parent / 'outside'; outside.mkdir()
        (self.data / 'ticks').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.archive.immutable(self.data / 'ticks' / 'escape.json', b'bad')
        self.assertFalse((outside / 'escape.json').exists())

    def test_orphan_publication_can_reconcile_without_original_files(self):
        self.add("ticks/first.json", b"original")
        a = self.writer(); a.checkpoint()
        # Simulate death after publish but before Git index persistence.
        original = self.archive.save_catalog
        def fail(*args): raise OSError("git disk unavailable")
        self.archive.save_catalog = fail
        try:
            with self.assertRaises(OSError): a.finish()
        finally: self.archive.save_catalog = original
        record = self.archive.reconcile(a.tag, self.data, self.gh)
        self.assertEqual(record["files"]["ticks/first.json"]["sha256"], hashlib.sha256(b"original").hexdigest())

    def test_draft_recovery_preserves_interrupted_status(self):
        self.add("ticks/first-start.json", b'{"startedAt":"2026-10-21T12:00:00Z"}')
        a = self.writer(); a.checkpoint()
        record = self.archive.reconcile(a.tag, self.data, self.gh)
        self.assertEqual(record["completion"], "recovered_interrupted")

    def test_unpack_rejects_traversal_symlinks_and_expansion(self):
        for name, kind, payload in [("../escape", tarfile.REGTYPE, b"x"),
                                    ("ticks/link", tarfile.SYMTYPE, b""),
                                    ("ticks/large", tarfile.REGTYPE, b"x" * 100)]:
            b = io.BytesIO()
            with tarfile.open(fileobj=b, mode='w:gz') as tf:
                info = tarfile.TarInfo(name); info.type = kind; info.size = len(payload)
                tf.addfile(info, io.BytesIO(payload))
            with self.assertRaises(ValueError):
                self.archive.unpack(b.getvalue(), max_bytes=50)

    def test_bundle_boundaries_and_snapshot_tamper(self):
        self.add("ticks/a.json", b"a" * 60)
        self.add("ticks/b.json", b"b" * 60)
        a = self.writer(); a.limit = 100; record = a.finish()
        self.assertEqual(len(record["bundles"]), 2)
        self.gh.corrupt = True
        with self.assertRaises(ValueError):
            self.archive.materialize(self.data, self.data.parent / "bad", self.gh)

    def test_interrupted_readback_bundles_can_overlap_identically(self):
        self.add('ticks/start.json', b'original')
        a = self.writer(); a.checkpoint()
        self.gh.upload(a.release, 'bundle-' + self.archive.sha(self.archive.pack({'ticks/start.json':b'original','ticks/end.json':b'end'})) + '.tar.gz', self.archive.pack({'ticks/start.json':b'original','ticks/end.json':b'end'}))
        record = self.archive.reconcile(a.tag, self.data, self.gh)
        self.assertEqual(len(record['files']),2)
        self.archive.materialize(self.data, self.data.parent/'restore',self.gh)

    def test_large_restore_uses_public_downloads_without_api_budget(self):
        self.add('ticks/one.json',b'one');record=self.writer().finish()
        for i in range(1001):
            (self.data/'catalog'/f'extra-{i}.json').write_bytes(self.archive.encoded(record))
        public = self.gh.download
        self.gh.download_public = public
        def fail(asset):raise AssertionError('public restore consumed authenticated API budget')
        self.gh.download = fail
        result=self.archive.materialize(self.data,self.data.parent/'restored',self.gh)
        self.assertEqual(result['files'],1002)
