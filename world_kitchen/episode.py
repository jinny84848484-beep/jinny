"""World Kitchen episode content: the planning and review prompts, plan -> renderer
manifest mapping (checked by pipeline.validate + kitchen_guard), narration timing,
the wordmark and the 19:00 KST publish time. Content problems raise Hold (not an alarm).
"""
from __future__ import annotations

import json
import re
import subprocess
from datetime import timedelta

from .ledger import code_for
from .pipeline import TEMPLATE, SIGNATURE, validate
from .providers import tts

SIGNATURE_EN = TEMPLATE['signature_en']


class Hold(Exception):
    """Content problem: today's episode is not published; not a CEO alarm."""


def parse_json(text):
    value = text.strip()
    if value.startswith('```'):
        value = re.sub(r'^```(?:json)?\s*', '', value).removesuffix('```').strip()
    start, end = value.find('{'), value.rfind('}')
    if start < 0 or end < start:
        raise Hold('model returned no JSON object')
    try:
        return json.loads(value[start:end + 1])
    except ValueError:
        raise Hold('model returned invalid JSON') from None


PLAN_PROMPT = """You plan ONE YouTube Short for the Korean channel 방구석 세계요리 (World Kitchen at Home): "집에 있는 도구와 쉬운 재료로, 나도 따라 만드는 세계 요리".
Pick one world dish or a currently trending food in Korea that a beginner can make at home with ONLY a microwave or ONLY a rice cooker, with ingredients from Korean supermarkets, measured with 밥숟가락, 찻숟가락, 종이컵 or simple counts (g only for cheese/meat packs). Avoid every dish in the recent list. Browse real recipe pages; use at least two different https recipe/source pages you actually opened. Microwave heating times must come from a cited source for the same quantity; give both 700W and 1000W seconds (convert proportionally when only one is stated and say so in caveat).
Safety: no egg in shell, no metal or foil, no sealed containers, cover loosely with wrap or a lid, proteins cooked through ('속까지 완전히 익혀요'), no health or diet claims. Real brand names are forbidden anywhere.
Return ONLY JSON with exactly these keys:
dish, country, title (Korean, max 40 chars, hook style), description (Korean, 2-4 lines, must contain the sentence 'AI로 만든 이미지와 음성을 사용했습니다.'), servings (1-2), appliance ('microwave' or 'rice_cooker'),
ingredients: [{name, amount (number), unit (one of 밥숟가락 찻숟가락 종이컵 개 장 줌 꼬집 쪽 팩 봉지 약간 g), substitute (optional Korean-mart substitute)}] 2-8 items,
korean_mart_note (one sentence), allergens (array),
steps: [{text, appliance ('microwave' | 'rice_cooker' | 'none'), seconds (integer), mode (rice cooker mode name when appliance is rice_cooker)}] 2-6 steps; a microwave step text must contain its wattage like '700W 90초',
heating: {seconds: {"700": int, "1000": int}, source (one of sources), basis (quantity basis), caveat} (microwave only, else null),
sources: [https URLs] 2-4,
scenes: 4-6 scenes in order: first kind 'hero' (finished dish + one-line hook), then 'ingredients', then one or more 'step'/'heating' scenes, then 'finish'. Each scene: kind, title (max 14 chars), body (max 30 chars), footer (max 24 chars), narration (Korean, one or two short sentences, numbers written so they read naturally, never '28, 그램' style), english (subtitle), image_prompt (English, photorealistic food photo of exactly that moment, no text, no people faces, no brand packaging).
For a microwave dish one heating scene body must show both times in plain seconds exactly matching heating.seconds, like '700W 90초 · 1000W 60초'. Total narration about 150-260 Korean characters."""

REVIEW_PROMPT = """You are the independent safety and accuracy reviewer for a Korean home-cooking Short. Open the cited sources. Check: the dish is real and correctly attributed to its country; quantities and heating times match the sources for the same quantity (700W/1000W conversion stated); every step is safe for a beginner (no egg in shell, metal, sealed container, undercooked protein, rice cooker overfill); no health claims; no real brand names; Korean text is natural; measurements are Korean-kitchen friendly. Return ONLY JSON {"status":"PASS"|"FAIL","issues":[{"code":"...","explanation":"..."}]}. FAIL only for a concrete problem; PASS requires an empty issues list."""


def build_episode(plan, day, folder):
    """Map a reviewed plan onto the existing renderer contract (images/audio filled later)."""
    if not isinstance(plan, dict):
        raise Hold('plan is not an object')
    try:
        scenes_in = plan['scenes']
        if not isinstance(scenes_in, list) or not 4 <= len(scenes_in) <= 6:
            raise Hold('plan needs 4-6 scenes')
        scenes = []
        for i, s in enumerate(scenes_in):
            kind = s.get('kind')
            if kind not in {'hero', 'ingredients', 'step', 'heating', 'finish'}:
                raise Hold('unknown scene kind')
            scenes.append({'kind': kind, 'duration': 3, 'image': str(folder / f'scene_{i}.png'),
                           'title': s['title'].strip(), 'body': s['body'].strip(), 'footer': s['footer'].strip(),
                           'narration': s['narration'].strip(), 'english': s['english'].strip(),
                           'image_prompt': s['image_prompt'].strip()})
        # Fixed ending reuses the hero photo (reviewed reason recorded for the guard).
        scenes.append({'kind': 'signature', 'duration': 3, 'image': scenes[0]['image'], 'title': '방구석 세계요리',
                       'body': plan['dish'] + ' 완성!', 'footer': 'AI 이미지·음성 사용', 'narration': SIGNATURE,
                       'english': SIGNATURE_EN, 'same_image_justification': 'fixed signature card reuses the finished dish'})
        episode = {
            'channel_id': TEMPLATE['channel_id'], 'title': plan['title'].strip()[:60],
            'description': plan['description'].strip() + '\n\n' + code_for(day),
            'dish': plan['dish'], 'country': plan['country'], 'servings': int(plan['servings']),
            'ingredients': plan['ingredients'], 'steps': plan['steps'], 'korean_mart_note': plan.get('korean_mart_note', ''),
            'allergens': plan.get('allergens') or [], 'voice_provider': 'ElevenLabs', 'sources': plan['sources'],
            'rights_reviewed': True, 'recipe_reviewed': True, 'synthetic_disclosure': True,
            'extra_purchase_allowed': False, 'appliance': plan['appliance'], 'scenes': scenes,
        }
        if plan['appliance'] == 'microwave':
            episode['heating'] = plan['heating']
            episode['heating']['seconds'] = {k: int(v) for k, v in plan['heating']['seconds'].items()}
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise Hold('plan schema: ' + type(exc).__name__ + ' ' + str(exc)[:80]) from None
    if not all(isinstance(u, str) and u.startswith('https://') for u in episode['sources']) or len(set(episode['sources'])) < 2:
        raise Hold('two https recipe sources required')
    if 'AI' not in episode['description']:
        raise Hold('AI disclosure missing from description')
    for scene in scenes:
        if 'image_prompt' in scene and any(word in scene['image_prompt'].lower() for word in ('logo', 'brand', 'text')) and 'no text' not in scene['image_prompt'].lower():
            raise Hold('image prompt asks for text or branding')
    try:
        validate(episode)  # renderer contract + kitchen_guard safety rules
    except ValueError as exc:
        raise Hold('guard: ' + str(exc)[:120]) from None
    return episode


def media_duration(path):
    out = subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(path)])
    return float(out.decode().strip())


def fit_durations(raw, pad=0.35):
    """Scene durations from narration length; renderer needs 0.25-15 s each and 10-60 s total."""
    durations = [round(min(15.0, max(1.5, d + pad)), 2) for d in raw]
    total = sum(durations)
    if not 10 <= total <= 60:
        raise Hold('narration length gives ' + str(round(total, 1)) + ' s; must be 10-60 s')
    return durations


def narrate(episode, folder):
    parts = []
    for i, scene in enumerate(episode['scenes']):
        path = folder / f'voice_{i}.mp3'
        tts(scene['narration'], path)
        parts.append(path)
    durations = fit_durations([media_duration(p) for p in parts])
    for scene, d in zip(episode['scenes'], durations):
        scene['duration'] = d
    inputs, filters = [], []
    for i, (path, d) in enumerate(zip(parts, durations)):
        inputs += ['-i', str(path)]
        filters.append(f'[{i}:a]aresample=44100,apad=whole_dur={d},atrim=0:{d}[a{i}]')
    filters.append(''.join(f'[a{i}]' for i in range(len(parts))) + f'concat=n={len(parts)}:v=0:a=1[out]')
    narration = folder / 'narration.m4a'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', *inputs, '-filter_complex', ';'.join(filters), '-map', '[out]',
                    '-c:a', 'aac', '-b:a', '192k', str(narration)], check=True)
    episode['narration_audio'] = str(narration)
    return narration


def make_logo(path):
    from PIL import Image, ImageDraw, ImageFont
    image = Image.new('RGBA', (520, 146), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(TEMPLATE['font'], 64)
    text = '방구석 세계요리'
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(((520 - (box[2] - box[0])) / 2 - box[0], (146 - (box[3] - box[1])) / 2 - box[1]), text,
              font=font, fill=TEMPLATE['title_color'])
    image.save(path)
    return path


def publish_time(now):
    target = now.replace(hour=19, minute=0, second=0, microsecond=0)
    if now <= target - timedelta(minutes=20):
        return target
    return (now + timedelta(minutes=20)).replace(second=0, microsecond=0)
