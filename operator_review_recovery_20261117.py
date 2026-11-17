"""Pinned, independently reviewed preview-only recovery for 2026-11-17.
Explicit manual dispatch only: no OpenAI requests and no upload path. Existing
failed submissions are preserved; previews stop for independent final AV QA.
"""
import copy,hashlib,json
from pathlib import Path
import cloud_runner as c
import daily_graphics as g
DAY='2026-11-17'
RECORD_HASH='6c7a7c9992833b9c6696e4775893dcf54f39de7a94645bb21299245f28c3f7e3'
# Exact independent pre-render package; final audio/video QA remains pending.
APPROVED_PACKAGE_SHA256='789f30b4d4bcb7a7ec4b14e4d76c352de3aec4cbea1f515a1139dab9d005afec'
LIVE_ENABLED=True
CONFIG_HASH='72f1c0d860f1d61acd45ad2a88a2b72ccb5f1a0a2012ca6e9d0b6442fdc01292'
CHECKS={'facts','source_independence','freshness','duplicate_event','rights',
        'visual_content','legibility','sensitive_claims','channel_scope'}

def verify_package(raw, ledger, asset_root):
    if APPROVED_PACKAGE_SHA256 is None or hashlib.sha256(raw).hexdigest()!=APPROVED_PACKAGE_SHA256:
        raise ValueError('No exact independently reviewed package is pinned')
    data=json.loads(raw)
    record=ledger.get('days',{}).get(DAY,{})
    if c.fingerprint(record)!=RECORD_HASH or record.get('status')!='draft_submitted':
        raise ValueError('Original submitted record changed; no automatic resume')
    if any(str(x.get('at','')).startswith(DAY) or str(x.get('job_id','')).startswith(DAY)
           for x in ledger.get('publications',[])):
        raise ValueError('Existing publication conflicts with replacement')
    c.assert_no_unresolved_additional(ledger)
    jobs=data.get('jobs');review=data.get('review',{});media=data.get('media',{})
    if data.get('day')!=DAY or not isinstance(jobs,list) or len(jobs)!=2:
        raise ValueError('Wrong production scope')
    if not review.get('reviewer') or review.get('reviewer')==data.get('creator'):
        raise ValueError('Independent reviewer required')
    if review.get('status')!='PASS_PRE_RENDER_ONLY' or review.get('issues',[])!=[] or review.get('final_video_audio_qa')!='PENDING' or review.get('publication_authorization') is not False or set(review.get('pre_render_checks',{}))!=CHECKS or any(v is not True for v in review['pre_render_checks'].values()):
        raise ValueError('Independent review incomplete')
    if review.get('jobs_sha256')!=c.fingerprint(jobs) or review.get('media_sha256')!=c.fingerprint(media):
        raise ValueError('Reviewed input changed')
    when=c.dt.datetime.fromisoformat(review['checked_at'])
    if when.tzinfo is None or not 0 <= (c.p.now()-when).total_seconds() <= 36*3600:
        raise ValueError('Review expired or future dated')
    root=Path(asset_root).resolve();expected=set()
    for job in jobs:
        if job.get('channel')!='ojirap':raise ValueError('Wrong channel')
        for i,scene in enumerate(job['scenes']):
            key=g.media_key(job,i);expected.add(key)
            item=media.get(key,{})
            if scene.get('media')!=key or item.get('policy')!=g.POLICY or item.get('path')!=g.relative_path(job,i) or item.get('scene_index')!=i or item.get('channel')!='ojirap' or item.get('topic_key')!=job['topic_key']:
                raise ValueError('Graphic binding mismatch')
            path=root/item['path']
            if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
                raise ValueError('Unsafe or missing graphic')
            if hashlib.sha256(path.read_bytes()).hexdigest()!=item.get('sha256'):
                raise ValueError('Reviewed PNG changed')
            text_hash=hashlib.sha256(json.dumps(g.card_text(job,i),ensure_ascii=False,sort_keys=True).encode()).hexdigest()
            if text_hash!=item.get('text_hash'):raise ValueError('Graphic text changed')
    if set(media)!=expected:raise ValueError('Unexpected media')
    # Structural validator mutates verification stamps; never use these as proof
    # of independent review. Run only on disposable copies after the audit above.
    c.validate_batch(copy.deepcopy(jobs),DAY,c.recent_topics(ledger))
    return copy.deepcopy(data)

def preflight_without_openai():
    """Read-only included-usage, configured voice, channel and renderer checks."""
    required=['ELEVENLABS_API_KEY','YOUTUBE_OJIRAP_OAUTH','GITHUB_TOKEN','GITHUB_REPOSITORY']
    if any(not c.os.environ.get(key) for key in required):raise RuntimeError('Missing production configuration')
    voices={voice['voice_id'] for voice in c.p.list_voices()}
    if c.p.cfg()['channels']['ojirap']['voice_id'] not in voices:raise RuntimeError('Configured voice unavailable')
    if c.fingerprint(c.p.cfg())!=CONFIG_HASH:raise RuntimeError('Reviewed voice/model/config changed')
    c.verify_channel('ojirap',c.youtube_token('ojirap'))
    sub=c.p.subscription()
    if type(sub.get('character_limit')) is not int or type(sub.get('character_count')) is not int:raise RuntimeError('Included quota unavailable')
    if sub['character_limit']-sub['character_count']<1800:raise RuntimeError('Insufficient included narration reserve')
    c.media_renderer.font(30);c.p.ffmpeg()

def run(package_path=None):
    # This flag must stay false until independent package/implementation review
    # and deployment have been explicitly instructed. Never bypass it via env.
    if not LIVE_ENABLED:raise RuntimeError('Offline preparation only: live recovery is not activated')
    if c.p.now().date().isoformat()!=DAY or c.os.environ.get('GITHUB_REF')!='refs/heads/main':
        raise ValueError('Recovery date/branch rejected')
    if c.os.environ.get('GITHUB_REPOSITORY')!='jinny84848484-beep/jinny' or c.os.environ.get('PUBLIC_UPLOADS_ENABLED')!='true':
        raise ValueError('Existing repository/publication gates required')
    if c.os.environ.get('GITHUB_EVENT_NAME')!='workflow_dispatch' or c.os.environ.get('GITHUB_RUN_ATTEMPT')!='1' or c.os.environ.get('RUN_MODE')!='operator_preview_20261117':raise ValueError('Explicit first-attempt preview dispatch required')
    ledger,sha=c.journal_get()
    raw=Path(package_path).read_bytes()
    data=verify_package(raw,ledger,c.p.ROOT)
    preflight_without_openai()
    # Verify the exact package/PNG bytes again after preflight, before any marker.
    if Path(package_path).read_bytes()!=raw:raise ValueError('Package changed during preflight')
    data=verify_package(raw,ledger,c.p.ROOT)
    original=copy.deepcopy(ledger['days'][DAY])
    rec=ledger['days'][DAY]
    rec['operator_review_recovery']={'source_run':'43984170067','previous_record':original,
        'package_sha256':APPROVED_PACKAGE_SHA256,'review':data['review'],
        'method':'independent_operator_review_original_graphics_no_openai',
        'previous_outcome':'HTTP429; no completed draft; prior billing unknown',
        'at':c.p.now().isoformat()}
    # Retain all old submitted checkpoints in place as well as the full snapshot.
    rec.update(status='operator_review_ready',jobs=data['jobs'],media=data['media'])
    if c.p.now().date().isoformat()!=DAY:raise ValueError('Date changed before CAS')
    sha=c.journal_put(ledger,sha)  # CAS failure stops before paid TTS or upload.
    c.p.write(c.p.ROOT/'runs'/DAY/'recovery'/'operator-original-record.json',original)
    c.p.write(c.p.ROOT/'runs'/DAY/'recovery'/'operator-review-package.json',data)
    # Exact scene choices were reviewed above; applying them cannot add new media.
    selection={j['id']:[s['media'] for s in j['scenes']] for j in data['jobs']}
    c.relevant_media.apply_review(data['jobs'],data['media'],selection)
    rec.update(status='reviewed',jobs=data['jobs'],media=data['media'],
        review=json.dumps({'status':'PASS','issues':[],'selection':selection}))
    sha=c.journal_put(ledger,sha)  # Second CAS must succeed before publish.
    # Pre-render PNG approval never substitutes for final audio/video QA.
    # Prepare both previews with existing render/TTS cache, never upload here.
    c.validate_batch(copy.deepcopy(data['jobs']),DAY,c.recent_topics(ledger))
    c.assert_additional_day_budget(ledger,DAY,data['jobs'])
    c.daily_graphics.ensure(data['jobs'],data['media'])
    c.download_media(data['jobs'])
    rec['operator_previews']={}
    for job in data['jobs']:
        path=c.p.ROOT/'runs'/DAY/'jobs'/(job['id']+'.json')
        # Existing validation populates timestamps only after the independently
        # pinned review was checked. Content is unchanged from the reviewed job.
        prepared=copy.deepcopy(job)
        prepared.update(channel_id=c.p.cfg()['channels']['ojirap']['channel_id'],
                        editorial_status='verified',verified_at=c.p.now().isoformat())
        for source in prepared['sources']:source['checked_at']=prepared['verified_at']
        c.p.validate(prepared)
        c.p.write(path,prepared)
        rec.update(status='tts_submitted',active_job=job['id'])
        sha=c.journal_put(ledger,sha)
        expected_job=c.fingerprint(prepared)
        narration=' '.join(scene['narration'].strip() for scene in prepared['scenes'])
        delivery=c.p.cfg()['channels']['ojirap'].get('delivery','')
        expected_speech=('['+delivery+'] ' if delivery else '')+narration
        if len(expected_speech)>900:raise ValueError('Pinned narration reserve exceeded')
        def included_only(actual_job,speech_text,config):
            if c.fingerprint(actual_job)!=expected_job or speech_text!=expected_speech or c.fingerprint(config)!=CONFIG_HASH:
                raise ValueError('TTS input/config changed')
            sub=c.p.subscription()
            if type(sub.get('character_limit')) is not int or type(sub.get('character_count')) is not int or sub['character_limit']-sub['character_count']<len(speech_text):
                raise RuntimeError('Insufficient included TTS quota; no overage allowed')
            if c.p.now().date().isoformat()!=DAY:raise RuntimeError('TTS date expired')
        c.media_renderer.render(path,billing_guard=included_only)
        rec['operator_previews'][job['id']]={'status':'rendered_pending_final_qa'}
        rec['status']='operator_preview_rendered'
        sha=c.journal_put(ledger,sha)
    rec['status']='operator_final_qa_pending'
    c.journal_put(ledger,sha)
    return {'status':'operator_final_qa_pending','uploads':0}


if __name__=='__main__':run(c.p.ROOT/'records/recovery/2026-11-17-operator-preview.json')
