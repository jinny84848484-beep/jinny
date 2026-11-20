from dataclasses import replace
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_world_kitchen import episode
from world_kitchen.pipeline import TEMPLATE, english_srt
from world_kitchen.publication import (
    CHANNEL_ID, FirstReview, FinalReview, PublicationHold, metadata_digest
)
from world_kitchen.release_runner import (
    RenderBinding, TrustedReviewBundle, run_release
)

DAY="2026-11-21"
WHEN="2026-11-21T10:00:00Z"
NOW=datetime.fromisoformat("2026-11-21T08:45:00+00:00")
VIDEO="Abcde123456"


class ReleaseRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.video_bytes=subprocess.check_output(["ffmpeg","-v","error",
            "-f","lavfi","-i","color=c=black:s=720x1280:r=5",
            "-f","lavfi","-i","anullsrc=r=8000:cl=mono",
            "-t","12","-c:v","libx264","-preset","ultrafast","-c:a","aac",
            "-movflags","+frag_keyframe+empty_moov","-f","mp4","pipe:1"])
        # Use the real producer MP4 layout (moov moved to front).
        with tempfile.TemporaryDirectory() as p:
            src=Path(p)/"source.mp4"; src.write_bytes(cls.video_bytes)
            dst=Path(p)/"out.mp4"
            subprocess.run(["ffmpeg","-v","error","-i",str(src),"-c","copy",
                            "-movflags","+faststart",str(dst)],check=True)
            cls.video_bytes=dst.read_bytes()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.output=self.root/"runs"
        self.daydir=self.output/DAY
        self.daydir.mkdir(parents=True)
        self.manifest=self.root/"episode.json"
        e=episode()
        self.manifest.write_text(json.dumps(e,ensure_ascii=False),encoding="utf-8")
        (self.daydir/"video.mp4").write_bytes(self.video_bytes)
        (self.daydir/"english.srt").write_text(english_srt(e),encoding="utf-8")
        (self.daydir/"description.txt").write_text(e["description"],encoding="utf-8")
        (self.daydir/"result.json").write_text(json.dumps({
            "rendered":True,"template_version":TEMPLATE["version"],
            "video_sha256":sha256(self.video_bytes).hexdigest(),
            "english_track_created":True,"english_track_uploaded":False,
            "uploaded":False,"scheduled":False}))
        self.queue=[{"date":DAY,"reviewed":True,"manifest":"episode.json"}]
        self.calls=[]
        self.current_time=NOW
        self.publish=None
        self.status="succeeded"
        self.refuse_upload=False
        self.claimed=False
        self.overwrite_bundle=lambda x:x

    def trust(self, candidate):
        self.calls.append("trust")
        self.candidate=candidate
        meta=dict(title=candidate.title,description=candidate.description,
                  made_for_kids=False,contains_synthetic_media=candidate.contains_synthetic_media)
        first=FirstReview("recipe",DAY,CHANNEL_ID,candidate.recipe_sha256,
                         True,True,NOW-timedelta(hours=1))
        final=FinalReview("final","recipe",DAY,CHANNEL_ID,candidate.recipe_sha256,
            candidate.media_sha256,metadata_digest(meta),WHEN,True,True,True,NOW)
        render=RenderBinding(DAY,CHANNEL_ID,candidate.recipe_sha256,candidate.media_sha256,
                             candidate.english_srt_sha256,NOW-timedelta(minutes=10))
        return self.overwrite_bundle(TrustedReviewBundle(render,first,final,candidate.english_srt_sha256,meta))

    def check_clear(self, release):
        self.calls.append("clear")
        return True

    def reserve(self, release):
        self.calls.append("reserve")
        if not self.claimed:
            self.claimed=True
            return "lease"

    def checkpoint(self, lease, event, **state):
        self.calls.append(event)

    def upload_private(self,release,media,metadata):
        self.calls.append("upload")
        self.assertEqual(media,self.video_bytes)
        self.assertEqual(release.media_sha256,sha256(media).hexdigest())
        if self.refuse_upload:
            raise TimeoutError
        return VIDEO

    def read(self,video_id):
        self.calls.append("read")
        b=self.trust_for_assets()
        return dict(id=video_id,channel_id=CHANNEL_ID,privacy="private",owner_verified=True,
                    never_public=True,media_sha256=self.candidate.media_sha256,
                    metadata_sha256=metadata_digest(b),processing_status=self.status,publish_at=self.publish)

    def trust_for_assets(self):
        c=self.candidate
        return dict(title=c.title,description=c.description,made_for_kids=False,
                    contains_synthetic_media=c.contains_synthetic_media)

    def schedule_private(self,release,video_id):
        self.calls.append("schedule")
        self.assertEqual(video_id,VIDEO)
        self.publish=release.publish_at

    def run_day(self,**kwargs):
        return run_release(self.queue,DAY,repo_root=self.root,output_root=self.output,
                           clock=lambda:self.current_time,**kwargs)

    def test_empty_unreviewed_and_duplicate_no_callbacks(self):
        original=self.queue
        for queue,status in (([],"no_ready_episode"),([dict(date=DAY)],"awaiting_recipe_and_rights_review")):
            self.queue=queue
            self.assertEqual(self.run_day(live=True,adapter=self,load_trusted=self.trust)["status"],status)
        self.queue=original*2
        with self.assertRaises(PublicationHold):self.run_day(load_trusted=self.trust)
        self.assertEqual(self.calls,[])

    def test_local_default_dry_run_only(self):
        result=self.run_day(adapter=self)
        self.assertEqual(result["status"],"dry_run_local_checks_only_approval_required")
        self.assertFalse(result["scheduled"])
        self.assertEqual(self.calls,[])

    def test_verified_default_dry_run_still_no_external_callbacks(self):
        result=self.run_day(adapter=self,load_trusted=self.trust)
        self.assertEqual(result["publish_at"],WHEN)
        self.assertEqual(result["status"],"dry_run_reviewed_inputs_verified_not_scheduled")
        self.assertEqual(self.calls,["trust"])

    def test_explicit_injected_live_mock_preserves_exact_asset(self):
        r=self.run_day(live=True,adapter=self,load_trusted=self.trust)
        self.assertEqual((r["status"],r["video_id"],r["publish_at"]),("schedule_verified",VIDEO,WHEN))
        self.assertEqual(self.calls,["trust","clear","reserve","upload_requested","upload","private_id",
                                    "read","schedule_requested","schedule","read","schedule_verified"])

    def test_exact_artifacts_fail_closed(self):
        mutations={"description.txt":b"wrong", "english.srt":b"wrong",
                   "result.json":b"{bad", "video.mp4":b"ftyp badly spoofed"}
        for filename,bad in mutations.items():
            asset=self.daydir/filename; previous=asset.read_bytes()
            with self.subTest(filename=filename):
                asset.write_bytes(bad)
                try:
                    with self.assertRaises(PublicationHold):self.run_day(load_trusted=self.trust)
                finally:
                    asset.write_bytes(previous)
        self.assertEqual(self.calls,[])

    def test_path_traversal_and_symlink(self):
        real=self.manifest
        self.queue[0]["manifest"]="../episode.json"
        with self.assertRaises(PublicationHold):self.run_day()
        symlink=self.root/"link.json"; symlink.symlink_to(real)
        self.queue[0]["manifest"]="link.json"
        with self.assertRaises(PublicationHold):self.run_day()
        self.queue[0]["manifest"]="episode.json"
        self.daydir.rename(self.output/"elsewhere")
        self.daydir.symlink_to(self.output/"elsewhere",target_is_directory=True)
        with self.assertRaises(PublicationHold):self.run_day()

    def test_trusted_binding_mismatch_no_external_write(self):
        self.overwrite_bundle=lambda b:replace(b,final_caption_sha256="e"*64)
        with self.assertRaises(PublicationHold):self.run_day(live=True,adapter=self,load_trusted=self.trust)
        self.assertEqual(self.calls,["trust"])

    def test_live_requires_all_wiring(self):
        for args in (dict(live=True,adapter=self),dict(live=True,load_trusted=self.trust)):
            with self.assertRaises(PublicationHold):self.run_day(**args)
        self.assertEqual(self.calls,[])

    def test_at_cutoff_cannot_upload(self):
        self.current_time=datetime.fromisoformat("2026-11-21T09:40:00+00:00")
        with self.assertRaises(PublicationHold):self.run_day(live=True,adapter=self,load_trusted=self.trust)
        self.assertEqual(self.calls,["trust"])

    def test_uncertain_upload_does_not_retry(self):
        self.refuse_upload=True
        for _ in range(2):
            with self.assertRaises(PublicationHold):self.run_day(live=True,adapter=self,load_trusted=self.trust)
        self.assertEqual(self.calls.count("upload"),1)
        self.assertNotIn("schedule",self.calls)


if __name__=="__main__":
    unittest.main()
