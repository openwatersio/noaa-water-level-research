import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from pipeline import archive
from test_archive import MemoryGitHub

class DeploymentFixTests(unittest.TestCase):
    def test_changed_import_is_not_published_or_reconciled_as_valid(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);raw=p/'raw';data=p/'data'
            archive.immutable(raw/'ticks/original.json',b'{}')
            expected=archive.inventory(raw)
            context={'batchId':'import','origin':'legacy','codeCommit':'a'*40,'importInventorySha256':archive.sha(archive.encoded(expected)),'importFileCount':1}
            b=MemoryGitHub()
            a=archive.Archive(raw,data,b,context,expected_inventory=expected)
            a.checkpoint()
            archive.immutable(raw/'ticks/extra.json',b'{}')
            with self.assertRaisesRegex(ValueError,'import inventory'): a.finish()
            self.assertTrue(b.get_release(a.tag)['draft'])
            with self.assertRaisesRegex(ValueError,'import inventory'): archive.reconcile(a.tag,data,b)
            self.assertFalse(list((data/'catalog').glob('*.json')))

    def test_partial_import_can_preserve_only_expected_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);raw=p/'raw';data=p/'data'
            archive.immutable(raw/'ticks/one.json',b'{}')
            expected={**archive.inventory(raw),'ticks/two.json':{'sha256':archive.sha(b'{}'),'bytes':2}}
            context={'batchId':'partial','origin':'legacy','codeCommit':'a'*40,'importInventorySha256':archive.sha(archive.encoded(expected)),'importFileCount':2}
            b=MemoryGitHub();a=archive.Archive(raw,data,b,context,expected_inventory=expected)
            a.checkpoint();r=archive.reconcile(a.tag,data,b)
            self.assertEqual(r['completion'],'recovered_interrupted')
            self.assertEqual(len(r['files']),1)

    def test_full_audit_failure_survives_shallow_audit_until_success(self):
        from pipeline import health
        check=getattr(health,'full_integrity',None);self.assertIsNotNone(check)
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)
            def result():return {'asOf':'2026-10-21T20:00:00Z','dataCommit':'a'*40,'state':'healthy','problems':[]}
            r=result()
            with patch('pipeline.health.materialize',side_effect=ValueError('bad hash')):
                check(r,p,p,p/'raw',None,True)
            self.assertEqual(r['state'],'action_required')
            archive.immutable(p/'health/first.json',archive.encoded(r))
            later=result();check(later,p,p,p/'raw',None,False)
            self.assertEqual(later['state'],'action_required')
            with patch('pipeline.health.materialize',return_value={'files':2}):
                fixed=result();check(fixed,p,p,p/'raw',None,True)
            self.assertEqual(fixed['state'],'healthy')
            self.assertEqual(fixed['fullVerification']['state'],'complete')

    def test_strict_json_rejects_nonfinite_values(self):
        with self.assertRaises(ValueError):archive.encoded({'value':float('nan')})

    def test_public_storage_retry_is_bounded_and_does_not_retry_404(self):
        from pipeline.github import GitHub
        from urllib.error import HTTPError
        from unittest.mock import MagicMock
        b=GitHub('owner/repo');a={'size':2,'browser_download_url':'https://github.com/owner/repo/releases/download/tag/body.bin'}
        response=MagicMock();response.__enter__.return_value.read.return_value=b'{}'
        transient=HTTPError(a['browser_download_url'],503,'temporary',None,None)
        with patch('pipeline.github.urlopen',side_effect=[transient,response]) as request, patch('pipeline.github.time.sleep'):
            self.assertEqual(b.download_public(a),b'{}');self.assertEqual(request.call_count,2)
        missing=HTTPError(a['browser_download_url'],404,'missing',None,None)
        with patch('pipeline.github.urlopen',side_effect=missing) as request, patch('pipeline.github.time.sleep'):
            with self.assertRaises(HTTPError):b.download_public(a)
            self.assertEqual(request.call_count,1)
        with patch('pipeline.github.urlopen',side_effect=transient) as request, patch('pipeline.github.time.sleep'):
            with self.assertRaises(HTTPError):b.download_public(a)
            self.assertEqual(request.call_count,3)

    def test_failed_full_audit_is_persisted_and_notified_by_cli(self):
        import contextlib, io, sys
        from types import SimpleNamespace
        from unittest.mock import Mock
        from pipeline.__main__ import main
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);store=SimpleNamespace(path=p/'data');reports=Mock();reports.path=p/'reports'
            result={'latestRelease':None,'state':'healthy','problems':[],'asOf':'2026-10-21T20:00:00Z'}
            with patch.object(sys,'argv',['pipeline','audit','--full','--notify']), patch('pipeline.__main__.GitHub'), patch('pipeline.__main__.pinned_data',return_value=(store,'a'*40)), patch('pipeline.__main__.audit',return_value=result), patch('pipeline.health.materialize',side_effect=ValueError('bad hash')), patch('pipeline.__main__.store_for',return_value=reports), patch('pipeline.__main__.context',return_value={'batchId':'audit'}), patch('pipeline.__main__.update_health_issue') as issue, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(),1)
                self.assertEqual(reports.write.call_count,1)
                saved=json.loads(reports.write.call_args.args[1])
                self.assertEqual(saved['fullVerification']['state'],'failed')
                self.assertEqual(issue.call_args.args[1]['state'],'action_required')

    def test_audit_stays_enabled_when_collection_is_paused(self):
        workflow=(Path(__file__).resolve().parents[1]/'.github/workflows/audit.yml').read_text()
        self.assertNotIn('COLLECTION_ENABLED',workflow)

    def test_health_issue_does_not_rewrite_untrusted_users_issue(self):
        from pipeline.__main__ import update_health_issue
        from unittest.mock import Mock
        backend=Mock()
        backend.api.side_effect=[[{'number':1,'title':'Collection health','body':'<!-- health:fake -->','user':{'login':'someone'},'author_association':'NONE'}],{}]
        update_health_issue(backend,{'problems':['stale_collection'],'asOf':'now'})
        self.assertEqual(backend.api.call_args.args[:2],('issues','POST'))
