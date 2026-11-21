from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import unittest

from world_kitchen.publication import (
    CHANNEL_ID, FirstReview, FinalReview, PublicationHold,
    metadata_digest, prepare_day, resume_known_private
)

DAY='2026-11-21'
TARGET='2026-11-21T10:00:00Z'
NOW=datetime.fromisoformat('2026-11-21T08:45:00+00:00')
VIDEO='Abcde123456'


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.now=NOW
        self.calls=[]
        self.queue=[{'date':DAY,'reviewed':True}]
        meta={'title':'밥숟가락 한입','description':'정확한 검수 문구',
              'made_for_kids':False,'contains_synthetic_media':True}
        media=b'fixture media exact bytes'
        recipe='a'*64
        first=FirstReview('recipe',DAY,CHANNEL_ID,recipe,True,True,NOW-timedelta(hours=1))
        final=FinalReview('final','recipe',DAY,CHANNEL_ID,recipe,sha256(media).hexdigest(),
                          metadata_digest(meta),TARGET,True,True,True,NOW)
        self.packet=dict(media=media,metadata=meta,first=first,final=final,
                         recipe_sha256=recipe,rendered_at=NOW-timedelta(minutes=30))
        self.row=dict(id=VIDEO,channel_id=CHANNEL_ID,privacy='private',owner_verified=True,
                      never_public=True,processing_status='succeeded',publish_at=None,
                      media_sha256=sha256(media).hexdigest(),metadata_sha256=metadata_digest(meta))
        self.reservation_allowed=True
        self.occupied=False
        self.upload_raises=False
        self.schedule_raises=False
        self.schedule_effect=True

    def load(self,entry):
        self.calls.append('load')
        return self.packet

    def clear(self,release):
        self.calls.append('clear')
        return not self.occupied

    def reserve(self,release):
        self.calls.append('reserve')
        if self.reservation_allowed:
            self.reservation_allowed=False
            return 'lease'

    def checkpoint(self,lease,event,**kw):
        self.calls.append(event)
        self.latest=dict(phase=event,lease=lease,**kw)

    def upload(self, release, media, metadata):
        self.calls.append('upload')
        self.assertEqual(release.media_sha256,sha256(media).hexdigest())
        self.assertEqual(release.metadata_sha256,metadata_digest(metadata))
        if self.upload_raises:
            raise TimeoutError
        return VIDEO

    def read(self,video_id):
        self.calls.append('read')
        return self.row.copy()

    def schedule(self,release,video_id):
        self.calls.append('schedule')
        self.assertEqual(release.publish_at,TARGET)
        self.assertEqual(video_id,VIDEO)
        if self.schedule_effect:
            self.row['publish_at']=release.publish_at
        if self.schedule_raises:
            raise TimeoutError

    def run_day(self,day=DAY):
        return prepare_day(self.queue,day,clock=lambda:self.now,
            load_reviewed=self.load, reserve=self.reserve,checkpoint=self.checkpoint,
            check_clear=self.clear,upload_private=self.upload,read=self.read,
            schedule_private=self.schedule)

    def test_no_content_empty_queue(self):
        self.queue=[]
        self.assertEqual(self.run_day()['status'],'no_ready_episode')
        self.assertEqual(self.calls,[])

    def test_unreviewed_and_duplicate(self):
        self.queue[0]['reviewed']=False
        self.assertEqual(self.run_day()['status'],'awaiting_recipe_and_rights_review')
        self.queue*=2
        with self.assertRaises(PublicationHold): self.run_day()
        self.assertEqual(self.calls,[])

    def test_exact_1900_utc_and_readback(self):
        result=self.run_day()
        self.assertEqual(result['publish_at'],TARGET)
        self.assertEqual(result['video_id'],VIDEO)
        self.assertTrue(result['scheduled'])
        self.assertEqual(self.calls,['load','clear','reserve','upload_requested','upload','private_id',
                                    'read','schedule_requested','schedule','read','schedule_verified'])

    def test_not_at_1700_or_after_1840_or_naive(self):
        for now in ('2026-11-21T07:59:59+00:00','2026-11-21T09:40:00+00:00',
                    '2026-11-21T10:00:00+00:00','2026-11-21T08:30:00'):
            self.now=datetime.fromisoformat(now)
            with self.assertRaises(PublicationHold): self.run_day()
        self.assertEqual(self.calls,[])

    def test_wrong_canonical_date(self):
        with self.assertRaises(PublicationHold): self.run_day('20261121')

    def test_immutable_media_and_exact_digest(self):
        self.packet['media']=b'replaced media'
        with self.assertRaises(PublicationHold): self.run_day()
        self.assertEqual(self.calls,['load'])

    def test_cannot_substitute_metadata(self):
        self.packet['metadata']['title']='다른 제목'
        with self.assertRaises(PublicationHold): self.run_day()
        self.assertEqual(self.calls,['load'])

    def test_wrong_channel_or_out_of_order_approvals(self):
        orig=self.packet['first']
        for row in (replace(orig,channel_id='other'),replace(orig, rights_cleared=False),
                    replace(orig,reviewed_at=NOW)):
            self.packet['first']=row
            with self.assertRaises(PublicationHold): self.run_day()
        self.assertEqual(self.calls,['load']*3)

    def test_no_publish_without_final_right_date_and_flags(self):
        orig=self.packet['final']
        for row in (replace(orig,publish_at='2026-11-21T12:00:00Z'),
                    replace(orig,captions_passed=False),replace(orig,approved_for_publication=False)):
            self.packet['final']=row
            with self.assertRaises(PublicationHold): self.run_day()
        self.assertEqual(self.calls,['load']*3)

    def test_clear_collision_before_reservation(self):
        self.occupied=True
        with self.assertRaises(PublicationHold): self.run_day()
        self.assertEqual(self.calls,['load','clear'])

    def test_pending_processing_no_schedule_or_reupload(self):
        self.row['processing_status']='processing'
        self.assertEqual(self.run_day()['status'],'private_processing_pending')
        with self.assertRaises(PublicationHold): self.run_day()
        self.assertEqual(self.calls.count('upload'),1)
        self.assertNotIn('schedule',self.calls)

    def test_resume_known_processing_uses_same_id_only(self):
        self.row['processing_status']='processing'
        self.assertEqual(self.run_day()['status'],'private_processing_pending')
        self.row['processing_status']='succeeded'
        arg=dict(clock=lambda:self.now,load_pending=lambda lease:self.latest,
                 checkpoint=self.checkpoint,read=self.read,schedule_private=self.schedule)
        self.assertEqual(resume_known_private(DAY,'lease',**arg)['status'],'schedule_verified')
        with self.assertRaises(PublicationHold): resume_known_private(DAY,'lease',**arg)
        self.assertEqual(self.calls.count('upload'),1)
        self.assertEqual(self.calls.count('schedule'),1)

    def test_readback_wrong_media_or_metadata_identity(self):
        for name in ('media_sha256','metadata_sha256'):
            with self.subTest(name=name):
                original=self.row[name]
                self.row[name]='b'*64
                self.reservation_allowed=True
                with self.assertRaises(PublicationHold): self.run_day()
                self.row[name]=original
        self.assertNotIn('schedule',self.calls)

    def test_unknown_upload_no_replay(self):
        self.upload_raises=True
        with self.assertRaisesRegex(PublicationHold,'upload uncertain'): self.run_day()
        with self.assertRaises(PublicationHold): self.run_day()
        self.assertEqual(self.calls.count('upload'),1)
        self.assertNotIn('schedule',self.calls)

    def test_unknown_schedule_no_replay(self):
        self.schedule_raises=True
        with self.assertRaisesRegex(PublicationHold,'update uncertain'): self.run_day()
        with self.assertRaises(PublicationHold): self.run_day()
        self.assertEqual(self.calls.count('schedule'),1)
        self.assertEqual(self.calls.count('upload'),1)
        self.assertNotIn('schedule_verified',self.calls)

    def test_schedule_confirmation_must_match(self):
        self.schedule_effect=False
        with self.assertRaisesRegex(PublicationHold,'readback mismatch'): self.run_day()
        self.assertNotIn('schedule_verified',self.calls)

    def test_private_not_itself_never_public_proof(self):
        self.row['never_public']=None
        with self.assertRaises(PublicationHold): self.run_day()
        self.assertNotIn('schedule',self.calls)

    def test_disagreeing_upload_status(self):
        self.row['processing_status']='processed'
        with self.assertRaises(PublicationHold): self.run_day()
        self.assertNotIn('schedule',self.calls)

    def test_cutoff_rechecked_after_durable_intent(self):
        def close_after_intent(*args,**kwargs):
            self.now=datetime.fromisoformat('2026-11-21T09:40:00+00:00')
        self.checkpoint=close_after_intent
        with self.assertRaises(PublicationHold): self.run_day()
        self.assertNotIn('upload',self.calls)

if __name__=='__main__': unittest.main()
