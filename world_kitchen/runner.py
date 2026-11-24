"""Isolated cooking production queue. Renders reviewed input only; never posts.

One entry per KST publication date. A pending/ambiguous attempt is not rerun.
"""
import argparse
from datetime import date, datetime
import json
import fcntl
import os
from tempfile import NamedTemporaryFile
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
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError('expected canonical KST date YYYY-MM-DD')
    state_path=Path(state_path)
    state_path.parent.mkdir(parents=True,exist_ok=True)
    # Lock a stable inode; the state file itself is atomically replaced.
    lock_path=state_path.with_name(state_path.name+'.lock')
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state=json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
        if day in state:
            return {'status':'already_attempted','date':day,'previous':state[day]['status'],'uploaded':False}
        manifest=json.loads(Path(entry['manifest']).read_text(encoding='utf-8'))
        validate(manifest)
        target=scheduled_at(day)
        def save(status,**extra):
            state[day]={'status':status,'target_publish_at':target,'uploaded':False,**extra}
            with NamedTemporaryFile(mode='w',encoding='utf-8',dir=state_path.parent,
                                    prefix='.'+state_path.name+'.',delete=False) as temp:
                temp_path=Path(temp.name)
                try:
                    json.dump(state,temp,ensure_ascii=False,indent=2)
                    temp.flush()
                    os.fsync(temp.fileno())
                    temp_path.replace(state_path)
                finally:
                    temp_path.unlink(missing_ok=True)
        save('render_started')
    # A concurrent worker sees render_started while this worker is rendering.
    status='render_failed_requires_reconciliation'
    extra={}
    try:
        video=renderer(manifest,Path(output_root)/day)
        status='rendered_requires_visual_audio_and_caption_review'
        extra={'video':str(video)}
    finally:
        # Re-read under lock to preserve reservations and outcomes for other dates.
        with lock_path.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state=json.loads(state_path.read_text(encoding='utf-8'))
            save(status,**extra)
    return state[day]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--queue',default='world_kitchen/queue.json')
    p.add_argument('--state',default='world_kitchen/state.json')
    p.add_argument('--output',default='runs/world_kitchen')
    p.add_argument('--date',default=datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat())
    a=p.parse_args()
    print(json.dumps(run(json.loads(Path(a.queue).read_text(encoding='utf-8')),a.state,a.output,a.date),ensure_ascii=False))
if __name__=='__main__':
    main()
