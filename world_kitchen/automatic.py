"""Bounded research -> assets -> TTS -> render -> review -> private upload -> captions -> schedule.

No retries after uncertain provider submission. All outbound methods injected in tests.
The runtime is OFF unless KITCHEN_GENERATION_ENABLED=true; publishing is independently gated.
"""
from __future__ import annotations
import argparse, base64, copy, hashlib, json, os, re, subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from .pipeline import TEMPLATE, SIGNATURE, validate, render, scheduled_at

CHANNEL=TEMPLATE['channel_id']
MAX_IMAGES=8
MAX_CHARACTERS=1200


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def source_bound(recipe,evidence):
    observed={x['url'] for x in evidence if x.get('url','').startswith('https://')}
    if not recipe.get('sources') or not set(recipe['sources']).issubset(observed):
        raise ValueError('Recipe sources not observed by research tool')
    if len(recipe.get('scenes',[]))>MAX_IMAGES:
        raise ValueError('Image budget exceeded')


def align_scenes(recipe,alignment,duration):
    narration=' '.join(s['narration'].strip() for s in recipe['scenes'])
    if ''.join(alignment.get('characters',[]))!=narration:
        raise ValueError('Narration alignment mismatch')
    starts=alignment['character_start_times_seconds']
    ends=alignment['character_end_times_seconds']
    if len(starts)!=len(narration) or len(ends)!=len(narration):raise ValueError('Alignment length mismatch')
    if any(not 0<=a<=b<=duration for a,b in zip(starts,ends)) or starts!=sorted(starts):
        raise ValueError('Invalid alignment times')
    offset=0;boundaries=[]
    for s in recipe['scenes']:
        boundaries.append(starts[offset]);offset+=len(s['narration'].strip())+1
    boundaries[0]=0;boundaries.append(duration)
    for i,s in enumerate(recipe['scenes']):s['duration']=boundaries[i+1]-boundaries[i]
    validate(recipe)


class GithubJournal:
    """Dedicated compare-and-swap journal, separate from the news/corgi ledger."""
    def __init__(self):
        import cloud_runner as c
        self.c=c
        repo=os.environ.get('GITHUB_REPOSITORY','')
        if repo!='jinny84848484-beep/jinny':raise ValueError('Unexpected journal repository')
        self.url='https://api.github.com/repos/'+repo+'/contents/world_kitchen/production-state.json'
        self.sha=None
        result=c.http(self.url,headers=c.gh_headers())
        self.sha=result['sha'];self.data=json.loads(base64.b64decode(result['content']))
    def save(self):
        body={'message':'Update cooking production journal [skip ci]','sha':self.sha,
              'content':base64.b64encode(json.dumps(self.data,ensure_ascii=False,indent=2).encode()).decode()}
        self.sha=self.c.http(self.url,body,self.c.gh_headers(),'PUT')['content']['sha']


def run(day,provider,journal,root,*,publish=False,clock=lambda:datetime.now(timezone.utc),renderer=render):
    root=Path(root)
    when=datetime.fromisoformat(scheduled_at(day))
    if when-clock()<timedelta(minutes=15):raise ValueError('Not enough lead for 19:00 publication')
    records=journal.data.setdefault('days',{})
    if day in records:return {'status':'already_attempted','previous':records[day]['status']}
    provider.preflight(publish=publish) # missing config/credits/auth blocks before paid calls
    records[day]={'status':'started','channel_id':CHANNEL,'publish_at':when.isoformat()}
    journal.save()
    root.mkdir(parents=True,exist_ok=False)
    rec=records[day]
    def step(name,call):
        rec['status']=name+'_submitted';journal.save()
        value=call()  # failures remain pending; never auto retry
        rec['status']=name+'_complete';journal.save()
        return value
    recent=[x.get('dish') for x in records.values() if x.get('dish')]
    recipe,evidence=step('research',lambda:provider.research(day,recent))
    source_bound(recipe,evidence)
    # Provider prose cannot choose arbitrary local audio files or external media URLs.
    for field in ('music','music_rights_source','narration_audio','logo_crop'):
        recipe.pop(field,None)
    # Do not trust model-provided approval flags. Independent review must pass.
    recipe.update(channel_id=CHANNEL,voice_provider='ElevenLabs',synthetic_disclosure=True,
                  extra_purchase_allowed=False,rights_reviewed=True,recipe_reviewed=True)
    recipe['logo']=provider.logo
    recipe.update(music=provider.music,music_rights_source=provider.music_rights_source)
    recipe['description']+='\n\nMusic: '+provider.music_credit+'\n'+provider.music_rights_source
    for i,s in enumerate(recipe['scenes']):s['image']=str(root/f'image_{i}.png')
    validate(recipe)
    review=step('recipe_review',lambda:provider.review(recipe,None))
    (root/'recipe_review.json').write_text(json.dumps(review,ensure_ascii=False,indent=2))
    if review.get('approved') is not True:
        rec['status']='recipe_rejected';journal.save();return rec
    if recipe['dish'] in recent:raise ValueError('Repeated recent dish')
    rec.update(dish=recipe['dish'],recipe_sha256=digest(recipe),sources=recipe['sources']);journal.save()
    for i,s in enumerate(recipe['scenes']):
        step('image_'+str(i),lambda s=s:provider.image(s,recipe))
    spoken=' '.join(s['narration'].strip() for s in recipe['scenes'])
    if len(spoken)>MAX_CHARACTERS:raise ValueError('Narration character budget exceeded')
    audio,alignment,duration=step('tts',lambda:provider.tts(spoken,root))
    recipe['narration_audio']=str(audio)
    align_scenes(recipe,alignment,duration)
    (root/'episode.json').write_text(json.dumps(recipe,ensure_ascii=False,indent=2))
    recipe['asr_transcript']=step('audio_transcription',lambda:provider.transcribe(audio))
    (root/'episode.json').write_text(json.dumps(recipe,ensure_ascii=False,indent=2))
    (root/'asr.txt').write_text(recipe['asr_transcript'])
    video=step('render',lambda:renderer(recipe,root/'render'))
    quality=step('final_review',lambda:provider.review(recipe,root/'render'))
    (root/'final_review.json').write_text(json.dumps(quality,ensure_ascii=False,indent=2))
    if quality.get('approved') is not True:
        rec['status']='final_review_rejected';journal.save();return rec
    rec.update(video_sha256=hashlib.sha256(Path(video).read_bytes()).hexdigest(),
               review_mode='automated_visual_text_review_not_human_listening')
    if not publish:
        rec['status']='rendered_reviewed_not_uploaded';journal.save();return rec
    provider.check_inventory(day)
    if when-clock()<timedelta(minutes=10):raise ValueError('Upload deadline passed; no late public upload')
    vid=step('private_upload',lambda:provider.upload(recipe,video))
    rec['video_id']=vid;journal.save() # retain remote ID before captions/schedule
    caption_id=step('english_captions',lambda:provider.captions(vid,root/'render'/'english.srt'))
    rec['caption_id']=caption_id;journal.save()
    if when-clock()<timedelta(minutes=5):raise ValueError('Schedule deadline passed; private video held')
    step('schedule',lambda:provider.schedule(vid,when.isoformat(),caption_id))
    step('verify',lambda:provider.verify(vid,when.isoformat(),caption_id))
    rec['status']='scheduled_verified';journal.save();return rec


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--preflight',action='store_true')
    p.add_argument('--date',default=datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat())
    a=p.parse_args()
    if not a.preflight and os.environ.get('KITCHEN_GENERATION_ENABLED')!='true':
        print('Cooking automatic generation disabled; no paid calls or upload.');return
    from .providers import Providers
    provider=Providers()
    publish=os.environ.get('KITCHEN_PUBLISH_ENABLED')=='true'
    if a.preflight:
        provider.preflight(publish=publish);print('Cooking preflight passed. No generation/upload.');return
    if os.environ.get('KITCHEN_GENERATION_ENABLED')!='true':
        print('Cooking automatic generation disabled; no paid calls or upload.');return
    result=run(a.date,provider,GithubJournal(),Path('runs/world_kitchen_auto')/a.date,publish=publish)
    print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
