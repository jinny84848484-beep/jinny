"""Offline World Kitchen production contract and renderer. No generation/upload calls.

Run: python -m world_kitchen.pipeline episode.json --output output/episode
Inputs are reviewed local assets. No network access, credentials, or shell execution.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import re
import subprocess
from kitchen_guard import validate_recipe
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TEMPLATE = json.loads(Path(__file__).with_name('template.json').read_text())
SIGNATURE = TEMPLATE['signature_ko']


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(episode):
    require(episode.get('channel_id') == TEMPLATE['channel_id'], 'wrong channel')
    require(episode.get('title'), 'title required')
    require(episode.get('description'), 'description required')
    require(episode.get('voice_provider') == 'ElevenLabs', 'ElevenLabs narration required')
    require(episode.get('sources'), 'recipe sources required')
    require(episode.get('rights_reviewed') is True, 'asset rights review required')
    require(episode.get('recipe_reviewed') is True, 'recipe review required')
    require(episode.get('synthetic_disclosure') is True, 'synthetic disclosure required')
    require(episode.get('extra_purchase_allowed') is False, 'no extra purchases')
    scenes = episode.get('scenes', [])
    require(len(scenes) >= 4, 'need hero, ingredients, steps and signature')
    require(scenes[0].get('kind') == 'hero', 'finished dish must appear immediately')
    require(scenes[1].get('kind') == 'ingredients', 'ingredients follow hero')
    require(scenes[-1].get('kind') == 'signature', 'signature ending required')
    require(scenes[-1].get('narration') == SIGNATURE, 'fixed spoken signature required')
    require(scenes[-1].get('body') == SIGNATURE, 'fixed on-screen signature required')
    total = 0
    for i, scene in enumerate(scenes):
        duration = scene.get('duration', 0)
        require(isinstance(duration, (int, float)) and not isinstance(duration, bool)
                and math.isfinite(duration) and 0.25 <= duration <= 15, 'invalid scene duration')
        total += duration
        for field in ('image', 'title', 'body', 'footer', 'narration', 'english'):
            require(isinstance(scene.get(field), str) and scene[field].strip(), f'scene {i}: {field} required')
        require(not re.search(r'\d\s*[,，]\s*(그램|와트|초|분)', scene['narration']), 'broken number/unit pronunciation')
        if i and scene['body'] != scenes[i-1]['body']:
            require(scene['image'] != scenes[i-1]['image'] or scene.get('same_image_justification'),
                    'changed step needs matching new image or reviewed explanation')
        if scene.get('optional_ingredient_visual'):
            require('선택' in scene['footer'] or '추가 예시' in scene['footer'], 'label optional ingredient result')
    require(10 <= total <= 60, 'duration outside recipe short range')
    if episode.get('appliance') == 'microwave':
        heating = episode.get('heating', {})
        require(set(heating.get('seconds', {})) == {'700', '1000'}, 'both 700W and 1000W times required')
        require(heating.get('source') in episode['sources'], 'heating source must be cited')
        require(heating.get('basis') and heating.get('caveat'), 'quantity basis and device caveat required')
        for value in heating['seconds'].values():
            require(isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isfinite(value) and 0 < value <= 3600, 'invalid heating seconds')
        visible = '\n'.join(s['title']+' '+s['body'] for s in scenes)
        for watt, seconds in heating['seconds'].items():
            require(f'{watt}W' in visible and f'{seconds:g}초' in visible,
                    'visible wattage/time must match source-bound heating data')
    # Reuse the chief-of-staff safety guard against the SAME recipe/text, not a
    # second unchecked recipe copy. Keep its hook/steps/finish naming contract.
    kind_map = {'hero':'hook', 'signature':'finish', 'heating':'steps', 'step':'steps', 'finish':'finish'}
    guarded = dict(episode, channel='kitchen', ai_disclosure=episode['synthetic_disclosure'])
    guarded['scenes'] = [dict(s, kind=kind_map.get(s['kind'], s['kind']), headline=s['title']) for s in scenes]
    validate_recipe(guarded)
    return total


def timestamp(seconds):
    millis = round(seconds*1000)
    h, millis = divmod(millis, 3600000)
    m, millis = divmod(millis, 60000)
    s, millis = divmod(millis, 1000)
    return f'{h:02}:{m:02}:{s:02},{millis:03}'


def english_srt(episode):
    validate(episode)
    start, result = 0, []
    for i, scene in enumerate(episode['scenes'], 1):
        end = start + scene['duration']
        require('-->' not in scene['english'] and '\n\n' not in scene['english'], 'invalid subtitle text')
        result.append(f"{i}\n{timestamp(start)} --> {timestamp(end)}\n{scene['english']}\n")
        start = end
    return '\n'.join(result)


def scheduled_at(day):
    # Metadata only. This function does not activate a timer or publish anything.
    return datetime.fromisoformat(day + 'T19:00:00').replace(tzinfo=ZoneInfo('Asia/Seoul')).isoformat()


def fit_rect(size, box):
    x, y, w, h = box
    scale = min(w/size[0], h/size[1])
    nw, nh = max(1, round(size[0]*scale)), max(1, round(size[1]*scale))
    return (x+(w-nw)//2, y+(h-nh)//2, nw, nh)


def draw_fit(draw, text, box, font_path, initial_size, fill):
    from PIL import ImageFont
    x, y, w, h = box
    for size in range(initial_size, 19, -1):
        font = ImageFont.truetype(font_path, size)
        bounds = draw.multiline_textbbox((0, 0), text, font=font, spacing=8, align='center')
        if bounds[2]-bounds[0] <= w and bounds[3]-bounds[1] <= h:
            draw.multiline_text((x+(w-(bounds[2]-bounds[0]))/2-bounds[0], y-bounds[1]), text,
                                font=font, spacing=8, align='center', fill=fill)
            return
    raise ValueError('text does not fit safely; shorten or reflow it')


def render_card(scene, logo, destination):
    from PIL import Image, ImageDraw
    t = TEMPLATE
    canvas = Image.new('RGB', (t['width'], t['height']), t['background'])
    for source, box in ((scene['image'], t['image_box']), (logo, t['logo_box'])):
        with Image.open(source) as image:
            image = image.convert('RGBA')
            x, y, w, h = fit_rect(image.size, box)
            image = image.resize((w, h), Image.Resampling.LANCZOS)
            canvas.paste(image, (x, y), image)
    draw = ImageDraw.Draw(canvas)
    for field, color in (('title', t['title_color']), ('body', t['body_color']), ('footer', '#4F514B')):
        label = scene[field]
        if field == 'body' and scene.get('kind') == 'signature' and label == SIGNATURE:
            label = label.replace('. ', '.\n', 1)  # two short mobile-safe lines; punctuation unchanged
        draw_fit(draw, label, t[field+'_box'], t['font'], t[field+'_size'], color)
    canvas.save(destination)


def render(episode, output):
    total = validate(episode)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)  # never overwrite another/repeated run
    audio = Path(episode['narration_audio']).resolve()
    logo = Path(episode['logo']).resolve()
    require(audio.is_file() and logo.is_file(), 'missing narration or wordmark')
    for scene in episode['scenes']:
        require(Path(scene['image']).is_file(), 'missing scene image')
    probe = json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-of','json',str(audio)]))
    require(abs(float(probe['format']['duration'])-total) < 0.3, 'audio/timeline duration mismatch')
    clips = []
    for i, scene in enumerate(episode['scenes']):
        frame = output/f'card_{i}.png'
        clip = output/f'clip_{i}.mp4'
        render_card(scene, logo, frame)
        subprocess.run(['ffmpeg','-y','-v','error','-loop','1','-i',str(frame),'-t',str(scene['duration']),
                        '-r','30','-c:v','libx264','-preset','fast','-crf','20','-pix_fmt','yuv420p',str(clip)], check=True)
        clips.append(clip.name)
    concat = output/'concat.txt'
    concat.write_text(''.join(f"file '{clip}'\n" for clip in clips))
    silent = output/'silent.mp4'
    subprocess.run(['ffmpeg','-y','-v','error','-f','concat','-safe','0','-i',str(concat),'-c','copy',str(silent)],check=True)
    args = ['ffmpeg','-y','-v','error','-i',str(silent),'-i',str(audio)]
    if episode.get('music'):
        require(episode.get('music_rights_source'), 'music license evidence required')
        args += ['-stream_loop','-1','-i',str(Path(episode['music']).resolve()),'-filter_complex',
                 f'[1:a]apad,loudnorm=I=-16:TP=-2:LRA=9[v];[2:a]volume=0.04,afade=t=out:st={max(0,total-2)}:d=2[b];[v][b]amix=inputs=2:normalize=0:duration=longest,alimiter=limit=0.84[a]']
    else:
        args += ['-filter_complex','[1:a]apad,loudnorm=I=-16:TP=-2:LRA=9,alimiter=limit=0.84[a]']
    video = output/'video.mp4'
    args += ['-map','0:v','-map','[a]','-t',str(total),'-c:v','copy','-c:a','aac','-b:a','192k','-movflags','+faststart',str(video)]
    subprocess.run(args,check=True)
    subprocess.run(['ffmpeg','-v','error','-i',str(video),'-f','null','-'],check=True)
    (output/'english.srt').write_text(english_srt(episode),encoding='utf-8')
    (output/'description.txt').write_text(episode['description'],encoding='utf-8')
    (output/'result.json').write_text(json.dumps({'template_version':TEMPLATE['version'], 'rendered':True,
        'video_sha256':hashlib.sha256(video.read_bytes()).hexdigest(), 'english_track_created':True,
        'english_track_uploaded':False, 'uploaded':False, 'scheduled':False,
        'visual_reviewed':False, 'listening_reviewed':False},ensure_ascii=False,indent=2))
    return video


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    episode=json.loads(args.manifest.read_text())
    validate(episode)
    if args.output:
        print(render(episode,args.output))
    else:
        print('Valid manifest. No generation, render, upload or scheduling performed.')

if __name__=='__main__':
    main()
