import unittest
from pathlib import Path
from unittest.mock import patch
import cloud_runner as c

class RetiredNewsChannel(unittest.TestCase):
    def test_key_and_identity_are_blocked_before_file_or_network_access(self):
        for job in ({'channel':'korean'}, {'channel':'ojirap','channel_id':c.RETIRED_NEWS_CHANNEL_ID}):
            with self.subTest(job=job), patch.object(c.p,'read') as read, patch.object(c.urllib.request,'urlopen') as network:
                with self.assertRaisesRegex(RuntimeError,'retired by owner'):
                    c.upload(Path('/nonexistent'),job,'not-a-token')
                read.assert_not_called(); network.assert_not_called()

    def test_ojirap_identity_is_not_blocked(self):
        self.assertFalse(c.news_publication_blocked({'channel':'ojirap','channel_id':'UCZj1FlWDcoiEQNLfpwc5qtw'}))
