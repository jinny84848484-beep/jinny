import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import cloud_runner as runner


class PublicMetricsVisibility(unittest.TestCase):
    def test_excludes_non_public_and_unknown_even_with_owner_oauth(self):
        channel = 'UCZj1FlWDcoiEQNLfpwc5qtw'
        ledger = {'publications': [
            {'video_id': i, 'channel': 'ojirap', **fields}
            for i, fields in [
                ('ledger-private', {'privacy_status': 'private'}),
                ('ledger-unlisted', {'privacy_status': 'unlisted'}),
                ('remote-private', {}),
                ('remote-unlisted', {}),
                ('missing-status', {}),
                ('public', {'privacy_status': 'public'}),
                ('legacy-public', {}),
            ]
        ]}

        def mock_fetch(url, *, headers):
            q = parse_qs(urlsplit(url).query)
            self.assertIn('status', q['part'][0].split(','))
            self.assertNotIn('ledger-private', q['id'][0].split(','))
            self.assertNotIn('ledger-unlisted', q['id'][0].split(','))
            rows = []
            for i, status in [('public', 'public'), ('legacy-public', 'public'),
                              ('remote-private', 'private'), ('remote-unlisted', 'unlisted'),
                              ('missing-status', None)]:
                row = {'id': i, 'snippet': {'channelId': channel,
                       'publishedAt': '2026-11-19T01:00:00Z'}, 'statistics': {'viewCount': '5'}}
                if status:
                    row['status'] = {'privacyStatus': status}
                rows.append(row)
            return {'items':rows}

        with patch.object(runner, 'youtube_token', return_value='fake'), \
             patch.object(runner, 'http', side_effect=mock_fetch), \
             patch.object(runner.p, 'write') as saved:
            runner.collect_metrics(ledger)

        rows = saved.call_args.args[1]['rows']
        self.assertEqual([r['video_id'] for r in rows], ['public', 'legacy-public'])
