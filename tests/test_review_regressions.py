import json
from pathlib import Path
import tempfile
import unittest
from pipeline.archive import encoded, immutable, reconcile
from pipeline.github import GitHub
from pipeline.hosted import run_hosted_tick, recover_terminals
from pipeline.selection import primary_view
from pipeline.evaluate_request import arguments
from test_archive import MemoryGitHub
from test_hosted import Store

class ReviewTests(unittest.TestCase):
    def test_hosted_release_creation_needs_no_admin_permission(self):
        b=GitHub('owner/repo')
        def api(path,method='GET',payload=None):
            if path=='immutable-releases':raise PermissionError('administration read unavailable')
            if path.startswith('releases?'):return []
            if path=='releases':return {'id':1,'tag_name':payload['tag_name']}
            raise AssertionError(path)
        b.api=api
        self.assertEqual(b.ensure_release('receipts-new','a'*40)['id'],1)

    def test_recovery_keeps_failure_history_but_restores_archived_status(self):
        from pipeline.health import resolved_runs
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);store=Store(p/'data');store.path.mkdir();b=MemoryGitHub()
            context={'batchId':'batch','origin':'github','codeCommit':'a'*40}
            publish=b.publish
            def fail(release):raise OSError('temporary publication failure')
            b.publish=fail
            def collect(root,**kwargs):
                tick={'exitCode':0,'state':'complete','finishedAt':'2026-10-21T12:51:00Z','observations':[],'forecasts':[]}
                immutable(root/'ticks/tick.json',encoded(tick));return tick
            original=run_hosted_tick(context,p/'raw',store,b,collect)
            self.assertEqual(original['state'],'publication_failed')
            b.publish=publish;reconcile('receipts-batch',store.path,b)
            self.assertEqual(recover_terminals(store),['batch'])
            self.assertEqual(json.loads((store.path/'runs/batch.json').read_text())['state'],'publication_failed')
            self.assertEqual(resolved_runs(store.path)[0]['state'],'complete')
            self.assertEqual(resolved_runs(store.path)[0]['finishedAt'],'2026-10-21T12:51:00Z')
            self.assertEqual(recover_terminals(store),[])

    def test_empty_draft_recovers_context_from_durable_start(self):
        with tempfile.TemporaryDirectory() as temp:
            data=Path(temp);b=MemoryGitHub();context={'batchId':'empty','origin':'github','codeCommit':'a'*40}
            immutable(data/'runs/empty-start.json',encoded({**context,'startedAt':'2026-10-21T12:50:00Z','state':'started'}))
            b.ensure_release('receipts-empty','a'*40)
            record=reconcile('receipts-empty',data,b)
            self.assertEqual(record['context'],context)
            self.assertEqual(record['completion'],'recovered_interrupted')

    def test_shadow_ticks_and_empty_manifests_are_excluded_from_primary(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);raw=p/'raw';data=p/'data'
            for origin in ('legacy','github'):
                files={f'ticks/{origin}.json':{'startedAt':'2026-10-21T12:50:00Z'},
                       f'observations/{origin}.json':{'record':{'receivedAt':'2026-10-21T12:51:00Z'}},
                       f'forecasts/{origin}-start.json':{'startedAt':'2026-10-21T12:50:00Z'}}
                for name,body in files.items():immutable(raw/name,encoded(body))
                immutable(data/f'catalog/{origin}.json',encoded({'context':{'origin':origin},'files':{name:{} for name in files}}))
            primary_view(raw,data,p/'view',{'boundaryAt':None})
            self.assertTrue((p/'view/ticks/legacy.json').exists())
            self.assertFalse((p/'view/ticks/github.json').exists())
            self.assertFalse((p/'view/forecasts/github-start.json').exists())
            self.assertFalse((p/'view/observations/github.json').exists())

    def test_formal_requests_require_explicit_data_and_truth(self):
        class Backend:
            def api(self,*args):return {'object':{'sha':'a'*40}}
        for request in ({'phase':'calibration'}, {'phase':'confirmation','data_sha':'a'*40},
                        {'phase':'calibration','auto':True,'data_sha':'a'*40,'truth_as_of':'2026-12-03T13:00:00Z'}):
            with self.assertRaises(ValueError):arguments(request,Backend(),Path('/tmp/unused'))

    def test_width_publication_must_precede_confirmation(self):
        from pipeline.publication import validate_widths_publication
        body=encoded({'frozenAt':'2026-12-03T13:00:00Z'})
        class Backend:
            def get_release(self,tag):return {'immutable':True,'published_at':self.at,'assets':[{'name':'widths.json','id':1,'size':len(body)}]}
            def download(self,asset):return body
        backend=Backend();backend.at='2026-12-03T13:01:00Z'
        self.assertEqual(validate_widths_publication(backend,'report-test',body)['publishedAt'],backend.at)
        backend.at='2026-12-04T02:00:00Z'
        with self.assertRaises(ValueError):validate_widths_publication(backend,'report-test',body)

if __name__=='__main__':unittest.main()
