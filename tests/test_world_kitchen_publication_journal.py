from copy import deepcopy
from dataclasses import replace
import unittest

from world_kitchen.publication import CHANNEL_ID, CheckedRelease, PublicationHold
from world_kitchen.publication_journal import CookingJournal

DAY = "2026-11-20"
NEXT = "2026-11-21"
V = "abcdefghijk"
LEASE = "one-kitchen-lease-1"
RELEASE = CheckedRelease(DAY, CHANNEL_ID, DAY + "T10:00:00Z", "first", "final",
                         "a"*64, "b"*64, "c"*64)
class Backend:
    def __init__(self):
        self.doc = {"version":1, "channel_id":CHANNEL_ID, "days":{}}
        self.rev = 1
        self.calls = 0
        self.ambiguous = False
    def read(self):
        return deepcopy(self.doc), str(self.rev)
    def write(self, doc, revision):
        self.calls += 1
        if revision != str(self.rev):
            raise ValueError("conflict")
        self.doc = deepcopy(doc)
        self.rev += 1
        if self.ambiguous:
            raise TimeoutError("possible write")
        return str(self.rev)

class CookingJournalTests(unittest.TestCase):
    def setUp(self):
        self.back = Backend()
        self.new_id = 0
        def next_lease():
            self.new_id += 1
            return f"new-kitchen-lease-{self.new_id}"
        self.next_lease = next_lease
        self.journal = self.fresh()
    def fresh(self, confirmed=True):
        return CookingJournal(self.back.read,self.back.write,new_lease=self.next_lease,
                              approval_is_current=lambda _: confirmed)
    def pending(self):
        lease = self.journal.reserve(RELEASE)
        self.journal.checkpoint(lease,"upload_requested",release=RELEASE)
        self.journal.checkpoint(lease,"private_id",release=RELEASE,video_id=V)
        return lease
    def test_restart_only_known_private_and_current_review(self):
        lease = self.pending()
        with self.assertRaises(PublicationHold):
            self.fresh(False).load_pending(lease)
        other = self.fresh()
        self.assertEqual(other.load_pending(lease)["video_id"], V)
        other.checkpoint(lease,"schedule_requested",release=RELEASE,video_id=V)
        with self.assertRaises(PublicationHold):
            self.fresh().load_pending(lease)
        other.checkpoint(lease,"schedule_verified",release=RELEASE,video_id=V)
        self.assertEqual(self.back.doc["days"][DAY]["phase"], "schedule_verified")
    def test_no_duplicate_or_wrong_release(self):
        self.pending()
        calls = self.back.calls
        with self.assertRaises(PublicationHold):
            self.fresh().reserve(RELEASE)
        with self.assertRaises(PublicationHold):
            self.fresh().reserve(replace(RELEASE,day=NEXT,publish_at=NEXT+"T10:00:00Z"))
        with self.assertRaises(PublicationHold):
            self.fresh().reserve(replace(RELEASE,channel_id="other"))
        with self.assertRaises(PublicationHold):
            self.fresh().reserve(replace(RELEASE,publish_at=DAY+"T09:00:00Z"))
        self.assertEqual(self.back.calls,calls)
    def test_ambiguous_intent_never_replays(self):
        lease = self.pending()
        self.back.ambiguous = True
        with self.assertRaises(PublicationHold):
            self.journal.checkpoint(lease,"schedule_requested",release=RELEASE,video_id=V)
        self.back.ambiguous = False
        calls = self.back.calls
        with self.assertRaises(PublicationHold):
            self.journal.checkpoint(lease,"schedule_requested",release=RELEASE,video_id=V)
        with self.assertRaises(PublicationHold):
            self.fresh().load_pending(lease)
        self.assertEqual(self.back.calls,calls)
    def test_stale_revision_blocks_effect_intent(self):
        lease = self.pending()
        self.back.rev += 1
        with self.assertRaises(PublicationHold):
            self.journal.checkpoint(lease,"schedule_requested",release=RELEASE,video_id=V)
        self.assertEqual(self.back.doc["days"][DAY]["phase"], "private_id")
    def test_upload_request_no_restart_when_video_unknown(self):
        lease = self.journal.reserve(RELEASE)
        self.journal.checkpoint(lease,"upload_requested",release=RELEASE)
        with self.assertRaises(PublicationHold):
            self.fresh().load_pending(lease)
        self.assertIsNone(self.back.doc["days"][DAY]["video_id"])
    def test_wrong_id_can_never_write_scheduler_intent(self):
        lease = self.pending()
        calls = self.back.calls
        with self.assertRaises(PublicationHold):
            self.journal.checkpoint(lease,"schedule_requested",release=RELEASE,video_id="12345678901")
        self.assertEqual(self.back.calls,calls)
    def test_corrupt_headers_fail_closed(self):
        self.back.doc["version"] = True
        with self.assertRaises(PublicationHold):
            self.journal.reserve(RELEASE)
        self.assertEqual(self.back.calls,0)
    def test_transport_composition_uses_one_current_intent_for_each_send(self):
        from unittest.mock import Mock
        from world_kitchen.publication_journal import JournaledReleaseAdapter
        upload, schedule = Mock(return_value=V), Mock()
        adapter = JournaledReleaseAdapter(self.journal, check_clear=lambda _: True,
                upload_private=upload, read=lambda _: None, schedule_private=schedule)
        lease = adapter.reserve(RELEASE)
        with self.assertRaises(PublicationHold):
            adapter.upload_private(RELEASE,b'clip',{})
        adapter.checkpoint(lease,"upload_requested",release=RELEASE)
        self.assertEqual(adapter.upload_private(RELEASE,b'clip',{}),V)
        with self.assertRaises(PublicationHold):
            adapter.upload_private(RELEASE,b'clip',{})
        adapter.checkpoint(lease,"private_id",release=RELEASE,video_id=V)
        adapter.checkpoint(lease,"schedule_requested",release=RELEASE,video_id=V)
        adapter.schedule_private(RELEASE,V)
        with self.assertRaises(PublicationHold):
            adapter.schedule_private(RELEASE,V)
        self.assertEqual(upload.call_count,1)
        self.assertEqual(schedule.call_count,1)

    def test_current_approval_is_checked_again_before_transport(self):
        from unittest.mock import Mock
        from world_kitchen.publication_journal import JournaledReleaseAdapter
        journal = self.fresh(confirmed=False)
        upload = Mock()
        adapter = JournaledReleaseAdapter(journal, check_clear=lambda _: True,
                upload_private=upload, read=lambda _: None, schedule_private=Mock())
        lease = adapter.reserve(RELEASE)
        adapter.checkpoint(lease,"upload_requested",release=RELEASE)
        with self.assertRaises(PublicationHold):
            adapter.upload_private(RELEASE,b'clip',{})
        upload.assert_not_called()
