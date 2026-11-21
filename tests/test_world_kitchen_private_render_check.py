from hashlib import sha256
import json
from pathlib import Path
import subprocess
import tempfile
from unittest import TestCase, mock

from world_kitchen.private_render_check import render_and_check
from world_kitchen.publication import PublicationHold
from test_world_kitchen import episode

DAY = "2026-11-21"


class PrivateRenderCheckTests(TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        parent=Path(folder.name)
        self.inputs=parent/"inputs"
        self.outputs=parent/"outputs"
        self.inputs.mkdir(mode=0o700)
        self.outputs.mkdir(mode=0o700)
        self.queue=[{"date":DAY,"manifest":"recipe.json","reviewed":True}]
        self.recipe=episode()
        self.recipe.update(logo="logo.png",narration_audio="voice.wav")

    def run_it(self):
        (self.inputs/"queue.json").write_text(json.dumps(self.queue))
        return render_and_check(private_input=self.inputs,private_output=self.outputs,day=DAY)

    def write_recipe(self):
        raw=json.dumps(self.recipe,ensure_ascii=False).encode()
        (self.inputs/"recipe.json").write_bytes(raw)
        return raw

    def test_empty_unreviewed_and_duplicate_make_no_attempt(self):
        item=self.queue[0]
        self.queue=[]
        self.assertEqual(self.run_it()["status"],"no_ready_episode")
        self.queue=[{**item,"reviewed":False}]
        self.assertEqual(self.run_it()["status"],"awaiting_recipe_and_rights_review")
        self.queue=[item,item]
        with self.assertRaises(PublicationHold): self.run_it()
        self.assertEqual(list(self.outputs.iterdir()),[])

    def test_insecure_directory_and_symlink_rejected(self):
        self.write_recipe()
        self.outputs.chmod(0o755)
        with self.assertRaises(PublicationHold): self.run_it()
        self.outputs.chmod(0o700)
        (self.inputs/"link.json").symlink_to(self.inputs/"recipe.json")
        self.queue[0]["manifest"]="link.json"
        with self.assertRaises(PublicationHold): self.run_it()
        self.assertEqual(list(self.outputs.iterdir()),[])

    def test_existing_output_never_overwritten(self):
        self.write_recipe()
        (self.outputs/DAY).mkdir()
        original=self.outputs/DAY/"video.mp4"
        original.write_bytes(b"must not be touched")
        self.assertEqual(self.run_it()["status"],"existing_attempt_requires_reconciliation")
        self.assertEqual(original.read_bytes(),b"must not be touched")

    def test_narration_url_or_renamed_playlist_is_not_a_media_input(self):
        from PIL import Image
        Image.new("RGB",(64,64)).save(self.inputs/"logo.png")
        for scene in self.recipe["scenes"]:
            Image.new("RGB",(64,64)).save(self.inputs/scene["image"])
        for index,entry in enumerate(("https://example.invalid/private.wav","voice.wav")):
            if entry=="voice.wav":
                (self.inputs/entry).write_bytes(b"#EXTM3U\nhttps://example.invalid/fetch\n")
            self.recipe["narration_audio"]=entry
            self.write_recipe()
            with mock.patch("world_kitchen.private_render_check.runner.run") as call:
                with self.assertRaisesRegex(PublicationHold,"not auto-retried"): self.run_it()
                call.assert_not_called()
            # Separate fixture per case; production never clears an attempt automatically.
            n=self.inputs.parent/f"output-{index}"
            n.mkdir(mode=0o700)
            self.outputs=n

    def test_real_offline_path_pins_two_hashes_and_no_repeat(self):
        from PIL import Image
        Image.new("RGBA",(64,32)).save(self.inputs/"logo.png")
        for i,scene in enumerate(self.recipe["scenes"]):
            Image.new("RGB",(80,60),(10+i,90,80)).save(self.inputs/scene["image"])
        subprocess.run(["ffmpeg","-v","error","-f","lavfi","-i","anullsrc=r=44100:cl=mono",
                        "-t","12","-c:a","pcm_s16le",str(self.inputs/"voice.wav")],check=True)
        source=self.write_recipe()
        outcome=self.run_it()
        self.assertEqual(outcome["status"],"rendered_and_locally_checked_review_required")
        self.assertIs(outcome["scheduled"],False)
        self.assertIs(outcome["uploaded"],False)
        source_hash=sha256(source).hexdigest()
        attempt=self.outputs/".attempts"/DAY
        staged_hash=sha256((attempt/"execution.json").read_bytes()).hexdigest()
        self.assertEqual(outcome["source_manifest_sha256"],source_hash)
        self.assertEqual(outcome["staged_manifest_sha256"],staged_hash)
        self.assertEqual(outcome["manifest_sha256"],staged_hash)
        self.assertNotEqual(source_hash,staged_hash)
        self.assertEqual((attempt/"source.json").read_bytes(),source)
        mp4=self.outputs/DAY/"video.mp4"
        self.assertEqual(outcome["video_sha256"],sha256(mp4.read_bytes()).hexdigest())
        before=mp4.stat().st_mtime_ns
        self.assertEqual(self.run_it()["status"],"existing_attempt_requires_reconciliation")
        self.assertEqual(mp4.stat().st_mtime_ns,before)

    def test_failed_attempt_without_mp4_still_blocks_second_attempt(self):
        self.write_recipe()
        with self.assertRaisesRegex(PublicationHold,"not auto-retried"): self.run_it()
        self.assertEqual(self.run_it()["status"],"existing_attempt_requires_reconciliation")
