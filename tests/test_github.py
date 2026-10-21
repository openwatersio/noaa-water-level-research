import unittest
from pipeline.github import GitHub


class GitHubTests(unittest.TestCase):
    def test_starter_asset_is_retained_and_retry_uses_new_name(self):
        b=GitHub('owner/repo')
        release={'tag_name':'receipts-test','draft':True,'assets':[{'id':1,'name':'context.json','state':'starter','size':0}]}
        b.get_release=lambda tag:release
        def command(args, body=None):
            from pathlib import Path
            self.assertNotIn('--clobber',args)
            path=Path(args[3]);self.assertEqual(path.read_bytes(),b'context')
            release['assets'].append({'id':2,'name':path.name,'state':'uploaded','size':7})
            return b''
        b.command=command
        a=b.upload(release,'context.json',b'context')
        self.assertTrue(a['name'].startswith('context.json.retry-'))
        self.assertEqual(release['assets'][0]['state'],'starter')

    def test_draft_release_is_addressed_by_id_before_tag_exists(self):
        backend = GitHub('owner/repo')
        calls = []
        def api(path, method='GET', payload=None):
            calls.append(path)
            if path.startswith('releases?'): return [{'id':9,'tag_name':'receipts-test','draft':True}]
            if path == 'releases/9': return {'id':9,'tag_name':'receipts-test','draft':True}
            if path.startswith('releases/9/assets?'): return []
            raise RuntimeError('404: draft has no published tag')
        backend.api = api
        self.assertTrue(backend.get_release('receipts-test')['draft'])
        self.assertFalse(any('/tags/' in p for p in calls))
