import unittest
from world_kitchen import google_transport as g

class FixedHostTests(unittest.TestCase):
    def test_google_hosts_only(self):
        for url in ['http://www.googleapis.com/x','https://evil.test/x','https://user@www.googleapis.com/x','https://www.googleapis.com:444/x','https://www.googleapis.com/x#secret']:
            with self.subTest(url=url), self.assertRaises(ValueError):g.validate(url)
        g.validate('https://www.googleapis.com/upload/youtube/v3/videos')
    def test_redirect_rejected(self):
        with self.assertRaises(RuntimeError):g.NoRedirect().redirect_request(None,None,None,None,None,None)
