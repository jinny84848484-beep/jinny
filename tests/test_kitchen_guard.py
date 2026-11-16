import copy
import unittest

import kitchen_guard as k


def job():
    return {
        'channel': 'kitchen', 'dish': '오야코동', 'country': '일본', 'servings': 1,
        'title': '전자레인지 5분 오야코동', 'appliance': 'microwave',
        'description': '편의점 닭가슴살로 만드는 덮밥. 이 영상의 이미지는 AI로 만들었습니다.',
        'ai_disclosure': True, 'wattage_note': '700W 기준',
        'ingredients': [
            {'name': '닭가슴살', 'amount': 1, 'unit': '팩', 'substitute': '훈제 닭가슴살'},
            {'name': '달걀', 'amount': 1, 'unit': '개'},
            {'name': '간장', 'amount': 1, 'unit': '밥숟가락'},
            {'name': '밥', 'amount': 1, 'unit': '종이컵'},
        ],
        'steps': [
            {'text': '그릇에 닭가슴살과 간장을 넣고 랩을 살짝 덮어 700W로 2분 돌려요.', 'appliance': 'microwave', 'seconds': 120},
            {'text': '달걀을 풀어 붓고 1분 더 돌려 속까지 익혀요.', 'appliance': 'microwave', 'seconds': 60},
            {'text': '밥 위에 올리면 끝.', 'appliance': 'none', 'seconds': 0},
        ],
        'scenes': [
            {'kind': 'hook', 'headline': '5분 덮밥', 'narration': '전자레인지로 5분이면 일본 덮밥 끝.'},
            {'kind': 'ingredients', 'headline': '재료', 'narration': '닭가슴살 한 팩, 달걀 하나.'},
            {'kind': 'steps', 'headline': '돌리기', 'narration': '랩 덮고 700W 2분.'},
            {'kind': 'finish', 'headline': '완성', 'narration': '밥 위에 올리면 완성.'},
        ],
        'allergens': ['달걀', '대두'],
    }


class KitchenGuard(unittest.TestCase):
    def test_valid_recipe_passes(self):
        self.assertEqual(k.validate_recipe(job()), [])

    def reject(self, mutate, code):
        value = job()
        mutate(value)
        with self.assertRaises(k.RecipeRejected) as ctx:
            k.validate_recipe(value)
        self.assertEqual(str(ctx.exception), code)

    def test_tools_and_measures(self):
        self.reject(lambda j: j.update(appliance='oven'), 'kitchen_appliance')
        self.reject(lambda j: j['ingredients'][2].update(unit='ml'), 'kitchen_unit')
        self.reject(lambda j: j['ingredients'][2].update(amount=0), 'kitchen_amount')
        self.reject(lambda j: j['steps'][0].update(seconds=900), 'kitchen_microwave_time')
        self.reject(lambda j: (j['steps'][0].update(text='그릇에 넣고 2분 돌려요.'), j.update(wattage_note='')), 'kitchen_wattage')
        self.reject(lambda j: [s.update(appliance='none') for s in j['steps']], 'kitchen_main_appliance_unused')
        self.reject(lambda j: j['steps'].append({'text': '밥솥에 넣어요', 'appliance': 'rice_cooker', 'seconds': 600}), 'kitchen_rice_cooker_mode')

    def test_korean_mart_angle_required(self):
        def strip(j):
            for item in j['ingredients']:
                item.pop('substitute', None)
        self.reject(strip, 'kitchen_mart_angle')

    def test_hook_first_and_scene_order(self):
        self.reject(lambda j: j['scenes'].reverse(), 'kitchen_hook_first')
        self.reject(lambda j: j['scenes'].__setitem__(slice(1, 3), [j['scenes'][2], j['scenes'][1]]), 'kitchen_scene_order')

    def test_cooking_safety(self):
        cases = {
            'kitchen_unsafe_egg_in_shell': '달걀을 껍질째 전자레인지에 넣어요.',
            'kitchen_unsafe_metal_in_microwave': '알루미늄 호일을 덮고 전자레인지에 돌려요.',
            'kitchen_unsafe_sealed_container': '밀폐 용기에 담아 전자레인지에 돌려요.',
            'kitchen_unsafe_raw_protein': '닭은 덜 익혀 먹어도 돼요.',
            'kitchen_unsafe_medical_claim': '이 요리는 다이어트 효과가 있어요.',
        }
        for code, sentence in cases.items():
            with self.subTest(code=code):
                self.reject(lambda j, s=sentence: j['steps'][2].update(text=s), code)

    def test_protein_needs_doneness_note(self):
        def remove(j):
            j['steps'][1]['text'] = '달걀을 풀어 붓고 1분 더 돌려요.'
        self.reject(remove, 'kitchen_doneness_note')

    def test_ai_disclosure_required(self):
        self.reject(lambda j: j.update(ai_disclosure=False), 'kitchen_ai_disclosure')
        self.reject(lambda j: j.update(description='편의점 닭가슴살 덮밥'), 'kitchen_ai_disclosure')

    def test_warnings_do_not_block(self):
        value = job()
        value['allergens'] = []
        value['steps'][0]['text'] = '그릇에 닭가슴살과 간장을 넣고 700W로 2분 돌려요.'
        self.assertEqual(k.validate_recipe(value), ['microwave_cover_hint_missing', 'allergen_list_empty'])

    def test_rice_cooker_recipe(self):
        value = job()
        value.update(appliance='rice_cooker', wattage_note='')
        value['steps'] = [
            {'text': '쌀 1종이컵과 물, 재료를 넣어요.', 'appliance': 'none', 'seconds': 0},
            {'text': '만능찜으로 40분, 닭이 속까지 익었는지 확인해요.', 'appliance': 'rice_cooker', 'mode': '만능찜', 'seconds': 2400},
        ]
        value['scenes'][2]['narration'] = '밥솥 만능찜 40분.'
        self.assertEqual(k.validate_recipe(value), ['allergen_list_empty'] if not value['allergens'] else [])


if __name__ == '__main__':
    unittest.main()
