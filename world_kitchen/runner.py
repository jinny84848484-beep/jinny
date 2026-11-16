"""Isolated cooking production queue. Renders reviewed input only; never posts.

One entry per KST publication date. A pending/ambiguous attempt is not rerun.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo
from .pipeline import render, scheduled_at, validate


def run(queue, state_path, output_root, day, renderer=render):
    entries=[x for x in queue if x['date']==day]
    if not entries:
        return {'status':'no_ready_episode','date':day,'uploaded':False}
    if len(entries)!=1:
        raise ValueError('duplicate cooking date')
    entry=entries[0]
    if entry.get('reviewed') is not True:
        return {'status':'awaiting_recipe_and_rights_review','date':day,'uploaded':False}
    state_path=Path(state_path)
    state=json.loads(state_path.read_text()) if state_path.exists() else {}
    if day in state:
        return {'status':'already_attempted','date':day,'previous':state[day]['status'],'uploaded':False}
    manifest=json.loads(Path(entry['manifest']).read_text())
    validate(manifest)
    state_path.parent.mkdir(parents=True,exist_ok=True)
    def save(status,**extra):
        state[day]={'status':status,'target_publish_at':scheduled_at(day),'uploaded':False,**extra}
        temp=state_path.with_suffix('.tmp')
        temp.write_text(json.dumps(state,ensure_ascii=False,indent=2))
        temp.replace(state_path)
    save('render_started')
    try:
        video=renderer(manifest,Path(output_root)/day)
    except Exception:
        save('render_failed_requires_reconciliation')
        raise
    save('rendered_requires_visual_audio_and_caption_review',video=str(video))
    return state[day]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--queue',default='world_kitchen/queue.json')
    p.add_argument('--state',default='world_kitchen/state.json')
    p.add_argument('--output',default='runs/world_kitchen')
    p.add_argument('--date',default=datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat())
    a=p.parse_args()
    print(json.dumps(run(json.loads(Path(a.queue).read_text()),a.state,a.output,a.date),ensure_ascii=False))
if __name__=='__main__':
    main()
