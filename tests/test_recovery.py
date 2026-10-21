import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pipeline.archive import Archive, encoded, immutable
from pipeline.recovery import recover
from pipeline.selection import primary_view
from test_archive import MemoryGitHub
from test_hosted import Store


class RecoveryTests(unittest.TestCase):
    def test_unrecoverable_import_does_not_block_completed_hosted_draft(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);store=Store(p/'data');store.path.mkdir();backend=MemoryGitHub()
            backend.ensure_release('receipts-bad','a'*40)
            context={'origin':'github','batchId':'good','codeCommit':'a'*40,'runId':'10'}
            immutable(store.path/'runs/good-start.json',encoded({**context,'state':'started','startedAt':'2026-10-21T12:00:00Z'}))
            archive=Archive(p/'raw',store.path,backend,context);archive.start()
            backend.all_releases=lambda:list(backend.releases.values())
            backend.api=lambda path:{'status':'completed'}
            result=recover(store,backend,stopped_import_tag='receipts-bad')
            self.assertEqual(len(result['errors']),1)
            self.assertEqual(result['recovered'][0]['tag'],'receipts-good')

    def test_active_hosted_draft_is_never_published(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);store=Store(p/'data');store.path.mkdir();backend=MemoryGitHub()
            context={'origin':'github','batchId':'active','codeCommit':'a'*40,'runId':'11'}
            immutable(store.path/'runs/active-start.json',encoded({**context,'state':'started','startedAt':'2026-10-21T12:00:00Z'}))
            archive=Archive(p/'raw',store.path,backend,context);archive.start()
            backend.all_releases=lambda:list(backend.releases.values())
            backend.api=lambda path:{'status':'in_progress'}
            result=recover(store,backend)
            self.assertEqual(result['deferred'],['receipts-active'])
            self.assertTrue(backend.get_release('receipts-active')['draft'])

    def test_shared_content_bodies_do_not_create_ambiguous_acquisition_origin(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);raw=p/'raw';data=p/'data'
            immutable(raw/'forecasts/bodies/same.bin',b'same')
            for origin in ('legacy','github'):
                immutable(data/f'catalog/{origin}.json',encoded({'context':{'origin':origin},'files':{'forecasts/bodies/same.bin':{'sha256':'same','bytes':4}}}))
            primary_view(raw,data,p/'view',{'boundaryAt':None})
            self.assertEqual((p/'view/forecasts/bodies/same.bin').read_bytes(),b'same')

    def test_replay_rejects_disappearing_scores(self):
        from pipeline.replay import verify
        reference={'manifestInventory':{},'scores':{},'selectedInputSha256':'','labelsSha256':''}
        with tempfile.TemporaryDirectory() as temp:
            reference.update(truthAsOf='2026-10-17T01:47:57.294377Z')
            with patch('pipeline.replay.json.loads',return_value=reference), patch('pipeline.replay.report.build',return_value={'inputs':[],'labels':{}}):
                with self.assertRaisesRegex(ValueError,'required scientific fields'):verify(Path(temp))

    def test_synthetic_release_namespace_never_enters_research(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp));backend=MemoryGitHub()
            backend.all_releases=lambda:[{'tag_name':'receipts-test-roundtrip','draft':False}]
            result=recover(store,backend)
            self.assertEqual(result['recovered'],[])
            self.assertEqual(result['errors'],[])
