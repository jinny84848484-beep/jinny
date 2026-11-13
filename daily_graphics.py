"""Original text graphics for daily Shorts when no reviewed photo can be used.

Opt-in with config "daily_graphics_fallback": true. The cards contain ONLY the
reviewed script text of each scene (kind label, headline, body) plus the cited
source domains, so no new factual claim is introduced. They are offline
typography: no photographs, footage, faces, logos or reenactments, and every
card carries a visible "original graphic, not a photo" notice.

Files are deterministic for a pinned font; publish regenerates a missing file
and fails closed when its bytes differ from the reviewed sha256.
"""
from __future__ import annotations
import hashlib
import io
import json
import unicodedata
import urllib.parse
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import pipeline as p

SIZE = (1080, 1380)
SAFE = (80, 110, 1000, 1070)  # Bottom band stays clear for burned-in captions.
FONT = Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
BOLD = Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc')
POLICY = 'daily_original_graphics'
NOTICE = {'ko': '자료를 바탕으로 구성한 그래픽 · 실제 사진 아님',
          'en': 'Original graphic based on cited reports · not a photo'}
LABEL = {'ko': {'hook': '오늘의 이슈', 'fact': '보도 사실', 'opinion': '채널 논평', 'closing': '채널 논평'},
         'en': {'hook': "TODAY'S ISSUE", 'fact': 'REPORTED FACT', 'opinion': 'OUR TAKE', 'closing': 'OUR TAKE'}}
COMMENTARY = {'opinion', 'closing'}


def enabled():
    return p.cfg().get('daily_graphics_fallback') is True


def _language(job):
    return p.cfg()['channels'][job['channel']]['language']


def _clean(value):
    if not isinstance(value, str):
        return ''
    value = ' '.join(value.split())
    if any(unicodedata.category(c).startswith('C') for c in value):
        raise ValueError('Graphic text contains control characters')
    return value


def _domains(job, scene):
    sources = job.get('sources', [])
    picks = scene.get('source_indices') or list(range(len(sources)))
    out = []
    for i in picks:
        if type(i) is int and 0 <= i < len(sources):
            host = (urllib.parse.urlparse(sources[i].get('url', '')).hostname or '').removeprefix('www.')
            if host and host not in out:
                out.append(host)
    return out[:2]


def card_text(job, index):
    """The exact strings drawn on a card, all taken from the reviewed job."""
    lang = _language(job)
    scene = job['scenes'][index]
    domains = _domains(job, scene)
    footer = (('출처: ' if lang == 'ko' else 'Sources: ') + ' · '.join(domains)) if domains else ''
    return {'label': LABEL[lang][scene['kind']], 'headline': _clean(scene.get('headline')),
            'body': _clean(scene.get('body')), 'footer': footer, 'notice': NOTICE[lang],
            'counter': f'{index + 1} / {len(job["scenes"])}', 'commentary': scene['kind'] in COMMENTARY}


def _face(size, bold=False):
    return ImageFont.truetype(str(BOLD if bold else FONT), size, index=1)


def _wrap(draw, value, face, width):
    words = value.split(' ')
    lines, line = [], ''
    for word in words:
        trial = (line + ' ' + word).strip()
        if draw.textlength(trial, font=face) <= width:
            line = trial
            continue
        if line:
            lines.append(line)
        line = ''
        for ch in word:  # Break a single over-long word by character.
            if draw.textlength(line + ch, font=face) <= width:
                line += ch
            else:
                lines.append(line)
                line = ch
    if line:
        lines.append(line)
    return lines


def _fit(draw, value, sizes, width, max_lines, bold=False):
    for size in sizes:
        face = _face(size, bold)
        lines = _wrap(draw, value, face, width)
        if len(lines) <= max_lines:
            return face, size, lines
    raise ValueError('Graphic text does not fit the card')


def render_card(job, index):
    text = card_text(job, index)
    if not text['headline']:
        raise ValueError('Graphic headline required')
    im = Image.new('RGB', SIZE, '#101C29')
    draw = ImageDraw.Draw(im)
    width = SAFE[2] - SAFE[0]
    accent = '#F2B66D' if text['commentary'] else '#87C8CA'
    labels = []

    def put(value, x, y, face, size, color):
        box = draw.textbbox((x, y), value, font=face)
        if not (SAFE[0] <= box[0] and SAFE[1] <= box[1] and box[2] <= SAFE[2] and box[3] <= SAFE[3]):
            raise ValueError('Graphic text exceeds caption-safe artwork bounds')
        draw.text((x, y), value, font=face, fill=color)
        labels.append({'text': value, 'bbox': list(box), 'font_size': size})
        return box[3]

    draw.rectangle((80, 128, 88, 180), fill=accent)
    put(text['label'], 112, 124, _face(32, True), 32, accent)
    face, size, lines = _fit(draw, text['headline'], (56, 50, 44, 40), width, 3, True)
    y = 220
    for line in lines:
        y = put(line, 80, y, face, size, '#F3F6F9') + 12
    y += 18
    draw.line((80, y, 1000, y), fill='#466074', width=2)
    y += 36
    if text['body']:
        face, size, lines = _fit(draw, text['body'], (40, 36, 32, 28), width, 5)
        for line in lines:
            y = put(line, 80, y, face, size, '#DCE4EC') + 14
    if y > 880:
        raise ValueError('Graphic text does not fit the card')
    if text['footer']:
        face, size, lines = _fit(draw, text['footer'], (26, 22), width, 2)
        fy = 912 if len(lines) == 1 else 890
        for line in lines:
            fy = put(line, 80, fy, face, size, '#9FB1C2') + 8
    put(text['notice'], 80, 990, _face(24), 24, '#C3D0DC')
    put(text['counter'], 900, 990, _face(24), 24, '#C3D0DC')
    buf = io.BytesIO()
    im.save(buf, format='PNG', optimize=False, compress_level=9)
    return buf.getvalue(), labels


def media_key(job, index):
    return 'graphic-' + job['id'] + '-' + str(index)


def relative_path(job, index):
    return 'assets/graphics/' + job['id'][:10] + '/' + job['id'] + f'/scene-{index + 1:02d}.png'


def build(job):
    """Render one card per scene and return media-library entries keyed by scene."""
    root = p.ROOT.resolve()
    entries = {}
    for i in range(len(job['scenes'])):
        data, labels = render_card(job, i)
        rel = relative_path(job, i)
        target = (root / rel).resolve()
        if not target.is_relative_to(root):
            raise ValueError('Unsafe graphic path')
        if target.is_symlink():
            raise ValueError('Symlink graphic target rejected')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        notice = card_text(job, i)['notice']
        entries[media_key(job, i)] = {
            'path': rel, 'sha256': hashlib.sha256(data).hexdigest(), 'media_type': 'image',
            'policy': POLICY, 'topic_key': job['topic_key'], 'channel': job['channel'],
            'scene_index': i, 'notice': notice,
            'text_hash': hashlib.sha256(json.dumps(card_text(job, i), ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
            'credit': 'Original typography by this channel',
            'source': 'Original explanatory graphic based on the cited reporting',
            'license': 'Original channel artwork', 'license_url': '',
            'disclaimer': notice + ' · No photograph, footage or reenactment.',
            'relevance_verified': False}
    return entries


def ensure(jobs, library):
    """Before render: every graphic scene must match its reviewed bytes exactly."""
    by_id = {job['id']: job for job in jobs}
    for job in jobs:
        for i, scene in enumerate(job['scenes']):
            key = scene.get('media')
            media = library.get(key) if isinstance(key, str) else None
            if not media or media.get('policy') != POLICY:
                continue
            if key != media_key(job, i) or media.get('path') != relative_path(job, i) or media.get('scene_index') != i:
                raise ValueError('Graphic bound to the wrong scene')
            target = (p.ROOT.resolve() / media['path']).resolve()
            if not target.is_relative_to(p.ROOT.resolve()) or target.is_symlink():
                raise ValueError('Unsafe graphic path')
            if not target.is_file():
                data, _ = render_card(by_id[job['id']], i)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            if hashlib.sha256(target.read_bytes()).hexdigest() != media.get('sha256'):
                raise ValueError('Graphic bytes differ from the reviewed card')
    return True
