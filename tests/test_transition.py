import unittest
from pipeline import selection
from pipeline.archive import encoded

class TransitionTests(unittest.TestCase):
    def test_boundary_requires_prior_immutable_publication_of_exact_rule(self):
        rule={'boundaryAt':'2026-10-24T02:00:00Z','publicationTag':'transition-20261023','primaryBeforeBoundary':'legacy','primaryAtAndAfterBoundary':'github'}
        class Backend:
            published='2026-10-23T02:00:00Z'
            mutable=False
            body=encoded(rule)
            def get_release(self,tag): return {'draft':False,'immutable':not self.mutable,'published_at':self.published,'assets':[{'name':'collector-transition.json','size':len(self.body)}]}
            def download_public(self,asset): return self.body
        b=Backend()
        validate=getattr(selection,'validate_transition_publication',None)
        self.assertIsNotNone(validate)
        validate(rule,encoded(rule),b)
        b.published=rule['boundaryAt']
        with self.assertRaises(ValueError): validate(rule,encoded(rule),b)
        b.published='2026-10-23T02:00:00Z';b.mutable=True
        with self.assertRaises(ValueError): validate(rule,encoded(rule),b)
        b.mutable=False;b.body=b'{}'
        with self.assertRaises(ValueError): validate(rule,encoded(rule),b)
