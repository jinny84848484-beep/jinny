"""Offline, dated editorial graphics. No network, TTS, payment or publication.

Only the reviewed November 6 story is supported. Provenance is original artwork,
not a claim of footage licensing or an approval receipt. Full runtime job hash
binds regenerated cards, while a pinned editorial hash rejects foreign stories.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import io
import json
import unicodedata
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

NOTICE = '자료를 바탕으로 구성한 그래픽'
DATE = '2026-11-06'
EDITORIAL_HASH = '2a2c4e6362fba9247a2670868be47a921b5c8498584fe3ce8833b33a8a04e38a'
SIZE = (1080, 1380)
SAFE = (80, 110, 970, 1070)
FONT = Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
BOLD = Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _check_job(job, approved_story_hash, production_date):
    if (dt.date.fromisoformat(production_date).isoformat() != production_date
            or not DATE <= production_date <= (dt.date.fromisoformat(DATE) + dt.timedelta(days=1)).isoformat()
            or not job.get('id', '').startswith(production_date + '-')):
        raise ValueError('Dated approved story required; obtain a new review for another date')
    if digest(job) != approved_story_hash:
        raise ValueError('Approved job content hash mismatch')
    editorial = {k: v for k, v in job.items() if k not in ('id', 'channel_id')}
    if digest(editorial) != EDITORIAL_HASH:
        raise ValueError('Missing, changed or foreign approved editorial content')


FONT_HASHES = {'/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc': 'b76b0433203017ca80401b2ee0dd69350349871c4b19d504c34dbdd80541690a', '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc': 'faa5f3656a78b2e2d450d27fe8382c778bc2b6bb5ea29c986664a6a435056ceb'}

def _check_spec(graphics_spec):
    if not isinstance(graphics_spec, dict) or set(graphics_spec) != {'cards'}:
        raise ValueError('Unknown graphics specification fields')
    cards = graphics_spec['cards']
    if not isinstance(cards, list) or len(cards) != 5:
        raise ValueError('Exactly five explicit cards required')
    for i, card in enumerate(cards):
        if not isinstance(card, list) or len(card) != 4:
            raise ValueError('Each card needs four fields')
        category, headline, rows, footer = card
        if category != ('채널 논평' if i == 4 else '보도 사실'):
            raise ValueError('Fact/commentary classifications required')
        if not isinstance(rows, list) or not 1 <= len(rows) <= 3:
            raise ValueError('One to three body rows required')
        for value in [category, headline, *rows, footer]:
            if (not isinstance(value, str) or not 1 <= len(value) <= 70
                    or value != value.strip()
                    or any(unicodedata.category(c).startswith('C') for c in value)):
                raise ValueError('Invalid graphic text, length or control character')


def _image(index, graphics_spec, production_date):
    _check_spec(graphics_spec)
    cards = graphics_spec['cards']
    for font_path, expected in FONT_HASHES.items():
        if hashlib.sha256(Path(font_path).read_bytes()).hexdigest() != expected:
            raise ValueError("Pinned graphic font mismatch")
    im = Image.new('RGB', SIZE, '#101C29')
    draw = ImageDraw.Draw(im)
    records = []
    def label(value, x, y, size, color='#F3F6F9', bold=False):
        face = ImageFont.truetype(str(BOLD if bold else FONT), size, index=1)
        box = draw.textbbox((x, y), value, font=face)
        if not (SAFE[0] <= box[0] and SAFE[1] <= box[1] and box[2] <= SAFE[2] and box[3] <= SAFE[3]):
            raise ValueError('Graphic text exceeds caption-safe artwork bounds')
        draw.text((x, y), value, font=face, fill=color)
        records.append({'text': value, 'bbox': list(box), 'font_size': size})
    category, headline, rows, footer = cards[index]
    draw.rectangle((80, 125, 88, 225), fill='#87C8CA')
    label(category, 112, 123, 32, '#87C8CA', True)
    label('제작 ' + production_date.replace('-', '.') + ' · 출처 시점은 본문 참고', 112, 178, 26, '#C3D0DC')
    label(headline, 80, 290, 58, bold=True)
    draw.line((80, 400, 970, 400), fill='#466074', width=2)
    for n, row in enumerate(rows):
        y = 476 + n * 132
        draw.ellipse((85, y+27, 99, y+41), fill='#87C8CA')
        label(row, 125, y, 41)
    label(footer, 80, 915, 30, '#C3D0DC')
    label(NOTICE, 80, 990, 30, '#C3D0DC')
    label(f'{index + 1} / 5', 870, 990, 26, '#C3D0DC')
    buf = io.BytesIO()
    im.save(buf, format='PNG', optimize=False, compress_level=9)
    return buf.getvalue(), records


def build_original_graphics(job, output_dir, *, approved_story_hash, production_date, graphics_spec):
    _check_job(job, approved_story_hash, production_date)
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    cards = []
    for i in range(5):
        data, labels = _image(i, graphics_spec, production_date)
        name = f'original-graphic-{i+1:02d}.png'
        target = root / name
        if target.is_symlink():
            raise ValueError('Symlink graphic target rejected')
        target.write_bytes(data)
        cards.append({'scene_index': i, 'path': name, 'file': name,
                      'sha256': hashlib.sha256(data).hexdigest(),
                      'content_hash': approved_story_hash, 'notice': NOTICE,
                      'classification': graphics_spec['cards'][i][0], 'labels': labels})
    manifest = {'policy': 'original_graphics', 'version': 1,
                'job_content_hash': approved_story_hash, 'graphics_spec_hash': digest(graphics_spec), 'production_date': production_date,
                'editorial_hash': EDITORIAL_HASH, 'dimensions': list(SIZE),
                'safe_bounds': list(SAFE), 'cards': cards,
                'provenance': 'Original typography and geometric artwork; no photographs, footage, faces or reenactments',
                'approval_receipt': False}
    (root / 'graphics-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    return manifest


def validate_original_graphics(job, manifest, output_dir, *, approved_story_hash, production_date, graphics_spec):
    _check_job(job, approved_story_hash, production_date)
    if (manifest.get('policy') != 'original_graphics' or manifest.get('version') != 1
            or manifest.get('job_content_hash') != approved_story_hash
            or manifest.get('graphics_spec_hash') != digest(graphics_spec)
            or manifest.get('production_date') != production_date
            or manifest.get('editorial_hash') != EDITORIAL_HASH
            or manifest.get('dimensions') != list(SIZE)
            or manifest.get('safe_bounds') != list(SAFE)
            or len(manifest.get('cards', [])) != 5):
        raise ValueError('Invalid original graphics manifest')
    root = Path(output_dir).resolve()
    for i, card in enumerate(manifest['cards']):
        expected, labels = _image(i, graphics_spec, production_date)
        name = f'original-graphic-{i+1:02d}.png'
        path = root / name
        sha = hashlib.sha256(expected).hexdigest()
        if (card.get('scene_index') != i or card.get('file') != name
                or card.get('path') != name
                or path.is_symlink() or not path.is_file()
                or card.get('sha256') != sha or hashlib.sha256(path.read_bytes()).hexdigest() != sha
                or card.get('content_hash') != approved_story_hash or card.get('notice') != NOTICE
                or card.get('classification') != graphics_spec['cards'][i][0] or card.get('labels') != labels):
            raise ValueError('Missing, foreign, altered or incorrectly bound graphics artifact')
    return True


def generate_original_graphics(job, output_dir, *, graphics_spec):
    return build_original_graphics(job, output_dir, approved_story_hash=digest(job), production_date=job['id'][:10], graphics_spec=graphics_spec)


def render_graphics_review_frames(job, manifest, output_dir, *, graphics_spec):
    """Render actual legacy compositor/libass pixels with silent synthetic timing.

    Review only: no speech synthesis, upload, or approval is performed. Each
    frame shows the first subtitle cue of its scene; this is not full-video QA.
    """
    import subprocess
    import media_renderer as renderer
    root = Path(output_dir).resolve()
    validate_approved_graphics(job, manifest, root, approved_story_hash=digest(job), production_date=job['id'][:10], graphics_spec=graphics_spec)
    ch = renderer.p.cfg()['channels'][job['channel']]
    frames = []
    for i, card in enumerate(manifest['cards']):
        overlay = root / f'review-overlay-{i+1:02d}.png'
        renderer.overlay(job['scenes'][i], ch, i, 5, {'notice': NOTICE}, overlay)
        narration = job['scenes'][i]['narration']
        alignment = {'characters': list(narration), 'character_start_times_seconds': [n*.1 for n in range(len(narration))], 'character_end_times_seconds': [(n+1)*.1 for n in range(len(narration))]}
        cue = renderer.captions(narration, alignment, True)[0][2]
        subtitle = root / f'review-{i+1:02d}.srt'
        subtitle.write_text('1\n00:00:00,000 --> 00:00:02,000\n'+cue+'\n', encoding='utf-8')
        frame = root / f'review-frame-{i+1:02d}.png'
        style = 'FontName=Noto Sans CJK KR,FontSize=17,Bold=-1,PrimaryColour=&HFFFFFF,OutlineColour=&H111111,BorderStyle=3,Outline=1,Shadow=0,Alignment=2,MarginV=42,MarginL=18,MarginR=32'
        filt = '[1:v]split=2[bg][fg];[bg]scale=1080:1380:force_original_aspect_ratio=increase,crop=1080:1380,gblur=sigma=35,eq=brightness=-0.25[back];[fg]scale=1080:1380:force_original_aspect_ratio=decrease[front];[back][front]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30[v];[v]pad=1080:1920:0:185:black[b];[b][0:v]overlay=0:0:format=auto,format=yuv420p[composed];[composed]subtitles='+subtitle.name+":force_style='"+style+"'[out]"
        subprocess.run(['ffmpeg','-v','error','-y','-loop','1','-i',overlay.name,'-loop','1','-i',card['file'],'-filter_complex',filt,'-map','[out]','-frames:v','1',frame.name],cwd=root,check=True)
        frames.append({'scene_index': i, 'path': frame.name, 'sha256': hashlib.sha256(frame.read_bytes()).hexdigest(), 'subtitle': cue})
    sheet = Image.new('RGB', (1080, 768), '#080D13')
    for i, frame in enumerate(frames):
        with Image.open(root/frame['path']) as im:
            sheet.paste(im.resize((216,384)), (i*216,192))
    sheet.save(root/'rendered-contact-sheet.png')
    report = {'mode':'offline_synthetic_timing_no_voice', 'full_video_review':False, 'job_content_hash':digest(job),'frames':frames,'contact_sheet':'rendered-contact-sheet.png'}
    (root/'rendered-qa.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('approved_story_json')
    p.add_argument('output_dir')
    p.add_argument('graphics_spec_json')
    args = p.parse_args()
    raw = json.loads(Path(args.approved_story_json).read_text())
    job = raw['jobs'][0] if 'jobs' in raw else raw
    graphics_spec = json.loads(Path(args.graphics_spec_json).read_text())
    manifest = generate_original_graphics(job, args.output_dir, graphics_spec=graphics_spec)
    validate_original_graphics(job, manifest, args.output_dir, approved_story_hash=digest(job), production_date=job['id'][:10], graphics_spec=graphics_spec)
    print(json.dumps({'manifest': str(Path(args.output_dir)/'graphics-manifest.json'), 'cards': len(manifest['cards']), 'validated': True}))


def validate_approved_graphics(job, manifest, output_dir, *, approved_story_hash,
                               production_date, graphics_spec, files=None):
    """Check exact approval-bound PNGs without platform-dependent regeneration.

    Caller must independently bind this manifest/spec/job to dispatch approval.
    With files supplied, validate every byte before creating any local artwork.
    """
    import base64
    _check_job(job, approved_story_hash, production_date)
    _check_spec(graphics_spec)
    root = Path(output_dir).resolve()
    if (manifest.get('policy') != 'original_graphics' or manifest.get('version') != 1
            or manifest.get('job_content_hash') != approved_story_hash
            or manifest.get('graphics_spec_hash') != digest(graphics_spec)
            or manifest.get('production_date') != production_date
            or manifest.get('editorial_hash') != EDITORIAL_HASH
            or manifest.get('dimensions') != list(SIZE)
            or manifest.get('safe_bounds') != list(SAFE)
            or not isinstance(manifest.get('cards'), list) or len(manifest['cards']) != 5):
        raise ValueError('Invalid approved graphics manifest')
    names = [f'original-graphic-{i+1:02d}.png' for i in range(5)]
    if files is not None and (not isinstance(files, dict) or set(files) != set(names)):
        raise ValueError('Invalid approved graphics files')
    pending = []
    total = 0
    for i, (name, card) in enumerate(zip(names, manifest['cards'])):
        category, headline, rows, footer = graphics_spec['cards'][i]
        expected_text = [category, '제작 ' + production_date.replace('-', '.') + ' · 출처 시점은 본문 참고',
                         headline, *rows, footer, NOTICE, f'{i+1} / 5']
        if (set(card) != {'scene_index','path','file','sha256','content_hash','notice','classification','labels'}
                or card['scene_index'] != i or card['path'] != name or card['file'] != name
                or card['content_hash'] != approved_story_hash or card['notice'] != NOTICE
                or card['classification'] != category or not isinstance(card['labels'], list)
                or [label.get('text') for label in card['labels']] != expected_text):
            raise ValueError('Invalid approved graphic binding')
        for label in card['labels']:
            box = label.get('bbox')
            if (set(label) != {'text','bbox','font_size'} or not isinstance(box,list) or len(box)!=4
                    or any(type(n) is not int for n in box)
                    or not SAFE[0] <= box[0] < box[2] <= SAFE[2]
                    or not SAFE[1] <= box[1] < box[3] <= SAFE[3]
                    or type(label['font_size']) is not int or not 26 <= label['font_size'] <= 58):
                raise ValueError('Invalid approved graphic layout')
        path = root / name
        if path.is_symlink():
            raise ValueError('Symlink graphic target rejected')
        if files is not None:
            encoded = files[name]
            if not isinstance(encoded,str) or len(encoded) > 262144:
                raise ValueError('Graphic transport size exceeded')
            raw = base64.b64decode(encoded, validate=True)
        else:
            if not path.is_file() or path.stat().st_size > 196608:
                raise ValueError('Missing or oversized approved graphic')
            raw = path.read_bytes()
        total += len(raw)
        if not 0 < len(raw) <= 196608 or total > 600000 or hashlib.sha256(raw).hexdigest() != card['sha256']:
            raise ValueError('Approved graphic bytes differ')
        with Image.open(io.BytesIO(raw)) as im:
            if im.format != 'PNG' or im.size != SIZE or im.mode != 'RGB' or getattr(im,'n_frames',1) != 1:
                raise ValueError('Invalid approved PNG format')
            im.verify()
        pending.append((path, raw))
    if files is not None:
        root.mkdir(parents=True,exist_ok=True)
        import os
        for path, raw in pending:
            if path.exists():
                if path.read_bytes() != raw:
                    raise ValueError('Existing approved graphic differs')
            else:
                fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'wb') as output: output.write(raw)
    return True
