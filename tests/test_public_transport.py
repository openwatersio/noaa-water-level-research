import unittest
from unittest.mock import patch
from pipeline.github import GitHub

class PublicPathTests(unittest.TestCase):
    def test_upload_records_final_public_url_instead_of_temporary_draft_url(self):
        b=GitHub('owner/repo')
        a={'id':1,'name':'context.json','size':2,'state':'uploaded','browser_download_url':'https://github.com/owner/repo/releases/download/untagged-temp/context.json'}
        r={'tag_name':'receipts-test','draft':True,'assets':[a]}
        b.get_release=lambda tag:r
        b.download=lambda asset:b'{}'
        found=b.upload(r,'context.json',b'{}')
        self.assertEqual(found['browser_download_url'],'https://github.com/owner/repo/releases/download/receipts-test/context.json')

class ParallelRestoreTests(unittest.TestCase):
    def test_bundle_downloads_have_bounded_parallelism(self):
        from pipeline.archive import record_files, pack, sha
        import threading, time
        class Backend:
            active=0; peak=0
            lock=threading.Lock()
            def download_public(self, asset):
                with self.lock:
                    self.active+=1; self.peak=max(self.peak,self.active)
                time.sleep(.03)
                with self.lock: self.active-=1
                return blobs[asset['id']]
        blobs={}; bundles=[]; files={}
        for i in range(12):
            name=f'ticks/{i}.json'; body=str(i).encode(); blob=pack({name:body})
            blobs[i]=blob
            bundles.append({'asset':{'id':i,'size':len(blob)},'sha256':sha(blob)})
            files[name]={'sha256':sha(body),'bytes':len(body)}
        b=Backend(); result=record_files({'tag':'receipts-test','files':files,'bundles':bundles},b)
        self.assertEqual(len(result),12)
        self.assertGreater(b.peak,1)
        self.assertLessEqual(b.peak,4)

if __name__ == '__main__': unittest.main()
