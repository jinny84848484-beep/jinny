import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cloud_runner as oj
from world_kitchen import providers as wk


class PrivateUploadMode(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name)
        self.video = self.path / 'short.mp4'
        self.video.write_bytes(b'test-video')
        self.title = '[비공개 테스트] fixture'
        oj.p.write(self.path / 'upload-metadata.json',
                   {'title': self.title, 'description': 'fixture', 'language': 'ko'})

    def verify_request(self, module, channel_id, upload):
        receipt = {'id': '0123456789A', 'snippet': {'channelId': channel_id},
                   'status': {'privacyStatus': 'private'}}
        with patch.object(module.urllib.request, 'urlopen') as init, \
             patch.object(module, 'http', return_value=receipt) as send:
            init.return_value.__enter__.return_value.headers = {
                'Location': 'https://www.googleapis.com/upload-example'}
            self.assertEqual(upload(), receipt['id'])
            status = json.loads(init.call_args.args[0].data)['status']
            self.assertEqual(status['privacyStatus'], 'private')
            self.assertNotIn('publishAt', status)
            init.assert_called_once()
            send.assert_called_once()
            receipt['status']['publishAt'] = '2026-11-29T10:00:00Z'
            with self.assertRaisesRegex(RuntimeError, 'schedule|visibility'):
                upload()

    def test_ojirap_same_transport_for_private_fixture(self):
        cid = oj.p.cfg()['channels']['ojirap']['channel_id']
        job = {'channel':'ojirap', 'channel_id':cid, 'category':'society'}
        self.verify_request(oj, cid, lambda: oj.upload(self.path, job, 'test', private_test=True))

    def test_kitchen_same_transport_for_private_fixture(self):
        cid = wk.TEMPLATE['channel_id']
        episode = {'title': self.title, 'description':'fixture'}
        self.verify_request(wk, cid, lambda: wk.upload(self.video, episode, 'test', None, private_test=True))

    def test_kitchen_production_cannot_silently_upload_unscheduled(self):
        with patch.object(wk.urllib.request, 'urlopen') as init:
            with self.assertRaisesRegex(RuntimeError, 'Production upload requires'):
                wk.upload(self.video, {'title':'episode'}, 'test', None)
            init.assert_not_called()
