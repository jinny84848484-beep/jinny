"""Deterministic recipe guard for the 방구석 세계요리 (World Kitchen at Home) Shorts.

Written by the chief of staff (Claude) as the review half of the pipeline; the
production half (research, script, images, voice, render, upload) belongs to the
chief strategy officer (GPT). Every daily job must pass validate_recipe() before
any paid voice or image generation. A failing job is dropped, never published.

Channel rules (CEO 2026-11-16):
- tools: microwave and rice cooker only
- ingredients from Korean supermarkets, measured with 밥숟가락 / 종이컵
- finished dish + hook first, then ingredients, measures and steps
- followable recipe, cooking safety, AI content disclosure
"""
from __future__ import annotations
import re

CHANNEL = 'kitchen'
APPLIANCES = {'microwave', 'rice_cooker'}
UNITS = {'밥숟가락', '찻숟가락', '종이컵', '개', '장', '줌', '꼬집', '쪽', '팩', '봉지', '약간', 'g', '그램'}
SCENE_ORDER = ('hook', 'ingredients', 'steps', 'finish')
MAX_MICROWAVE_SECONDS = 600          # one heating step
MAX_TOTAL_MINUTES = 60               # a 'quick home' recipe
WATTAGE = re.compile(r'(700|800|1000)\s*W')
PROTEIN = ('닭', '돼지', '소고기', '쇠고기', '고기', '달걀', '계란', '새우', '오징어', '생선', '조개', '연어')
DONENESS = ('속까지', '완전히 익', '75', '핏기', '투명해지지 않', '불투명')
FORBIDDEN = {
    'egg_in_shell': re.compile(r'(껍질째|껍데기째|껍질 그대로).{0,12}(달걀|계란)|(달걀|계란).{0,12}(껍질째|껍데기째|통째로 전자레인지)'),
    'metal_in_microwave': re.compile(r'(알루미늄|호일|은박|금속|쿠킹포일|스테인리스).{0,20}(전자레인지|돌리)'),
    'sealed_container': re.compile(r'(밀폐|뚜껑을 꽉|완전히 닫).{0,20}(전자레인지|돌리|가열)'),
    'raw_protein': re.compile(r'(날것|생으로|덜 익혀|레어로).{0,10}(닭|돼지|달걀|계란)|(닭|돼지|달걀|계란).{0,10}(날것|생으로|덜 익혀|레어로)'),
    'rice_cooker_overfill': re.compile(r'(최대 ?눈금|MAX).{0,10}(넘|위로|초과)'),
    'medical_claim': re.compile(r'(치료|완치|다이어트 효과|살이 빠|면역력|혈당을 낮|암을)'),
}
REQUIRED_DISCLOSURE = 'AI'


class RecipeRejected(ValueError):
    """Fixed, non-content reason code; safe to log publicly."""


def _text(job):
    parts = [job.get('title', ''), job.get('description', '')]
    for scene in job.get('scenes', []):
        parts += [scene.get('headline', ''), scene.get('body', ''), scene.get('narration', '')]
    for step in job.get('steps', []):
        parts.append(step.get('text', ''))
    return '\n'.join(p for p in parts if isinstance(p, str))


def validate_recipe(job):
    """Return the list of warnings; raise RecipeRejected(code) on any hard failure."""
    def need(cond, code):
        if not cond:
            raise RecipeRejected(code)

    need(isinstance(job, dict) and job.get('channel') == CHANNEL, 'kitchen_channel')
    for key in ('dish', 'country', 'title', 'description', 'appliance', 'servings'):
        need(isinstance(job.get(key), (str, int)) and str(job.get(key)).strip(), 'kitchen_field_' + key)
    need(job['appliance'] in APPLIANCES, 'kitchen_appliance')
    need(isinstance(job['servings'], int) and 1 <= job['servings'] <= 4, 'kitchen_servings')
    need(len(job['title']) <= 60, 'kitchen_title_length')

    ingredients = job.get('ingredients')
    need(isinstance(ingredients, list) and 2 <= len(ingredients) <= 12, 'kitchen_ingredient_count')
    for item in ingredients:
        need(isinstance(item, dict) and isinstance(item.get('name'), str) and item['name'].strip(), 'kitchen_ingredient_name')
        need(item.get('unit') in UNITS, 'kitchen_unit')
        if item['unit'] != '약간':
            amount = item.get('amount')
            limit = 1000 if item['unit'] in {'g', '그램'} else 20
            need(isinstance(amount, (int, float)) and not isinstance(amount, bool) and 0 < amount <= limit, 'kitchen_amount')
        if item.get('substitute') is not None:
            need(isinstance(item['substitute'], str) and item['substitute'].strip(), 'kitchen_substitute')
    need(any(item.get('substitute') for item in ingredients) or job.get('korean_mart_note'), 'kitchen_mart_angle')

    steps = job.get('steps')
    need(isinstance(steps, list) and 2 <= len(steps) <= 8, 'kitchen_step_count')
    total = 0
    for step in steps:
        need(isinstance(step, dict) and isinstance(step.get('text'), str) and step['text'].strip(), 'kitchen_step_text')
        seconds = step.get('seconds', 0)
        need(isinstance(seconds, int) and 0 <= seconds <= 3600, 'kitchen_step_seconds')
        total += seconds
        if step.get('appliance') == 'microwave':
            need(0 < seconds <= MAX_MICROWAVE_SECONDS, 'kitchen_microwave_time')
            need(WATTAGE.search(step['text']) or WATTAGE.search(job.get('wattage_note', '')), 'kitchen_wattage')
        if step.get('appliance') == 'rice_cooker':
            need(isinstance(step.get('mode'), str) and step['mode'].strip(), 'kitchen_rice_cooker_mode')
        need(step.get('appliance') in APPLIANCES | {None, 'none'}, 'kitchen_step_appliance')
    need(any(step.get('appliance') == job['appliance'] for step in steps), 'kitchen_main_appliance_unused')
    need(total <= MAX_TOTAL_MINUTES * 60, 'kitchen_total_time')

    scenes = job.get('scenes')
    need(isinstance(scenes, list) and len(scenes) >= 4, 'kitchen_scene_count')
    kinds = [scene.get('kind') for scene in scenes]
    need(kinds[0] == 'hook', 'kitchen_hook_first')
    positions = [kinds.index(kind) if kind in kinds else -1 for kind in SCENE_ORDER]
    need(all(p >= 0 for p in positions) and positions == sorted(positions), 'kitchen_scene_order')

    text = _text(job)
    for code, pattern in FORBIDDEN.items():
        need(not pattern.search(text), 'kitchen_unsafe_' + code)
    names = ' '.join(item['name'] for item in ingredients)
    if any(word in names for word in PROTEIN):
        need(any(word in text for word in DONENESS), 'kitchen_doneness_note')

    need(job.get('ai_disclosure') is True and REQUIRED_DISCLOSURE in job['description'], 'kitchen_ai_disclosure')

    warnings = []
    if job['appliance'] == 'microwave' and not any('랩' in s['text'] or '뚜껑' in s['text'] for s in steps):
        warnings.append('microwave_cover_hint_missing')
    if not job.get('allergens'):
        warnings.append('allergen_list_empty')
    return warnings
