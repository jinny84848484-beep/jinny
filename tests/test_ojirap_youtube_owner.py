from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
import unittest

from ojirap_morning_schedule import CHANNEL_ID, ReleaseWindow, ScheduleHold
from ojirap_youtube_owner import PrivateUpload, YouTubeOwnerAdapter

VIDEO = "abcDEF12345"
WINDOW = ReleaseWindow(date(2026, 11, 21))
FLAGS = {"license": "creativeCommon", "embeddable": False, "publicStatsViewable": False,
         "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True}


class TestOwnerAdapter(unittest.TestCase):
    def setUp(self):
        self.receipt = PrivateUpload(VIDEO, CHANNEL_ID, "demo", "job-1", WINDOW.publish_at, True, True)
        self.channels = {"items": [{"id": CHANNEL_ID}]}
        self.video = {"id": VIDEO, "snippet": {"channelId": CHANNEL_ID, "title": "untouched"},
                      "processingDetails": {"processingStatus": "succeeded"},
                      "status": {"uploadStatus": "processed", "privacyStatus": "private",
                                 "madeForKids": False, **FLAGS}}
        self.now = WINDOW.start
        self.authorized = True
        self.calls = []
        self.put_failure = False
        self.put_no_effect = False
        self.put_drops_flag = False
        self.adapter = YouTubeOwnerAdapter(window=WINDOW, reservation="demo", clock=lambda:self.now,
            http=self.transport, load_provenance=lambda _:self.receipt,
            authorize_write=lambda receipt,when:self.authorized)

    def transport(self, method, resource, query, body):
        self.calls.append((method, resource, deepcopy(query), deepcopy(body)))
        if (method, resource) == ("GET", "channels"):
            self.assertEqual(query, {"part": "id", "mine": "true", "maxResults": "2"})
            return deepcopy(self.channels)
        if (method, resource) == ("GET", "videos"):
            self.assertEqual(query, {"part": "snippet,status,processingDetails", "id": VIDEO})
            return {"items": [deepcopy(self.video)]}
        self.assertEqual((method,resource,query),("PUT","videos",{"part":"status"}))
        if not self.put_no_effect:
            self.video["status"].update(body["status"])
        if self.put_drops_flag:
            self.video["status"].pop("embeddable")
        if self.put_failure:
            raise TimeoutError("potentially committed HTTP write")
        return {"id": VIDEO}

    def write(self, at=WINDOW.publish_at):
        return self.adapter.schedule_existing(VIDEO, at)

    def puts(self):
        return [r for r in self.calls if r[0]=="PUT"]

    def test_update_only_status_with_preserved_flags_then_separate_readback(self):
        self.assertEqual(self.write()["publish_at"], WINDOW.publish_at)
        self.assertEqual(self.puts()[0][3], {"id": VIDEO, "status": {**FLAGS,
            "privacyStatus": "private", "publishAt": WINDOW.publish_at}})
        self.assertEqual([(c[0],c[1]) for c in self.calls], [
            ("GET","channels"),("GET","videos"),("PUT","videos"),("GET","channels"),("GET","videos")])

    def test_authoritative_exact_existing_schedule_no_duplicate_put(self):
        self.video["status"]["publishAt"] = WINDOW.publish_at.replace("Z",".000Z")
        self.assertEqual(self.write()["publish_at"], WINDOW.publish_at)
        self.assertEqual(self.puts(), [])

    def test_wrong_existing_schedule_refuses_to_replace(self):
        self.video["status"]["publishAt"] = "2026-11-21T10:00:00Z"
        with self.assertRaises(ScheduleHold): self.write()
        self.assertEqual(self.puts(), [])

    def test_request_rejects_past_wrong_and_late(self):
        with self.assertRaises(ScheduleHold): self.write("2025-11-21T10:00:00Z")
        self.now = WINDOW.cutoff
        with self.assertRaises(ScheduleHold): self.write()
        self.assertEqual(self.calls, [])

    def test_rechecks_clock_after_authorization(self):
        def closes(*args):
            self.now=WINDOW.cutoff
            return True
        self.adapter.authorize_write=closes
        with self.assertRaises(ScheduleHold): self.write()
        self.assertEqual(self.puts(), [])

    def test_never_public_cannot_be_inferred_from_private(self):
        self.receipt=replace(self.receipt,never_public=False)
        with self.assertRaises(ScheduleHold): self.write()
        self.assertEqual(self.calls, [])

    def test_wrong_reservation_provenance(self):
        self.receipt=replace(self.receipt,reservation="other-day")
        with self.assertRaises(ScheduleHold): self.write()
        self.assertEqual(self.calls, [])

    def test_wrong_owner_or_video_channel(self):
        self.channels["items"][0]["id"]="UCwrong"
        with self.assertRaises(ScheduleHold): self.write()
        self.channels["items"][0]["id"]=CHANNEL_ID
        self.video["snippet"]["channelId"]="UCwrong"
        with self.assertRaises(ScheduleHold): self.write()
        self.assertEqual(self.puts(), [])

    def test_ambiguous_owner_or_video_id_fails(self):
        self.channels["nextPageToken"]="next"
        with self.assertRaises(ScheduleHold): self.write()
        del self.channels["nextPageToken"]
        self.video["id"]="DIFFERENT01"
        with self.assertRaises(ScheduleHold): self.write()
        self.assertEqual(self.puts(), [])

    def test_private_pending_only_and_strict_processing_enum(self):
        self.video["status"]["uploadStatus"]="uploaded"
        for state in ("processing","processed","success",None,{}):
            self.video["processingDetails"]["processingStatus"]=state
            if state=="processing":
                self.assertEqual(self.adapter.read_youtube(VIDEO)["processing_status"],state)
            with self.assertRaises(ScheduleHold): self.write()
        self.assertEqual(self.puts(), [])

    def test_malformed_writable_flag_and_missing_intent(self):
        self.video["status"]["embeddable"]=0
        with self.assertRaises(ScheduleHold): self.write()
        self.video["status"]["embeddable"]=False
        self.authorized=False
        with self.assertRaises(ScheduleHold): self.write()
        self.assertEqual(self.puts(), [])

    def test_lost_write_response_not_retried(self):
        self.put_failure=True
        with self.assertRaisesRegex(ScheduleHold, "write unconfirmed"): self.write()
        self.assertEqual(len(self.puts()),1)
        self.assertEqual(self.adapter.read_youtube(VIDEO)["publish_at"],WINDOW.publish_at)

    def test_put_response_alone_not_confirmation(self):
        self.put_no_effect=True
        with self.assertRaisesRegex(ScheduleHold, "readback differs"): self.write()
        self.assertEqual(len(self.puts()),1)

    def test_discarded_flag_after_put_stops(self):
        self.put_drops_flag=True
        with self.assertRaisesRegex(ScheduleHold, "readback differs"): self.write()
        self.assertEqual(len(self.puts()),1)


if __name__=="__main__":
    unittest.main()
