import copy
import json
from pathlib import Path
import tempfile
import unittest
from world_kitchen.pipeline import TEMPLATE,SIGNATURE,validate,english_srt,fit_rect,render_card,scheduled_at
from world_kitchen.runner import run


def episode():
    scenes=[]
    for i,kind in enumerate(['hero','ingredients','heating','signature']):
        scenes.append(dict(kind=kind,duration=3,image=f'{i}.png',title='짧은 제목',body=f'내용 {i}',
                           footer='안내',narration='요리 설명',english='Recipe instructions.'))
    scenes[0]['title']='재료 딱 2개!'
    scenes[2]['body']='700W 45초 · 1000W 30초'
    scenes[-1]['narration']=SIGNATURE
    return dict(channel_id=TEMPLATE['channel_id'],title='요리 제목',description='AI 이미지 예시',dish='퀘사디아',country='멕시코',servings=1,
                ingredients=[dict(name='또띠아',amount=1,unit='장'),dict(name='치즈',amount=28,unit='g')],
                steps=[dict(text='치즈 올리고 접기',appliance='none',seconds=0),dict(text='700W 45초부터 확인',appliance='microwave',seconds=45)],
                korean_mart_note='국내 마트 재료',allergens=['밀','우유'],
                voice_provider='ElevenLabs',sources=['https://example.com/recipe'],rights_reviewed=True,
                recipe_reviewed=True,synthetic_disclosure=True,extra_purchase_allowed=False,
                appliance='microwave',heating=dict(seconds={'700':45,'1000':30},
                    source='https://example.com/recipe',basis='치즈 28g',caveat='기기 차이'),scenes=scenes)


class WorldKitchenTests(unittest.TestCase):
    def test_integrated_safety_guard(self):
        e=episode();e['steps'][0]['text']='호일을 전자레인지에 넣어요'
        with self.assertRaises(ValueError):validate(e)
    def test_explicit_grams_supported(self):
        e=episode();e['ingredients'][1]['unit']='그램';validate(e)
    def test_valid(self): self.assertEqual(validate(episode()),12)
    def test_wrong_channel(self):
        e=episode();e['channel_id']='other'
        with self.assertRaises(ValueError):validate(e)
    def test_required_reviews(self):
        for field in ['rights_reviewed','recipe_reviewed','synthetic_disclosure']:
            e=episode();e[field]=False
            with self.assertRaises(ValueError):validate(e)
    def test_no_extra_purchase(self):
        e=episode();e['extra_purchase_allowed']=True
        with self.assertRaises(ValueError):validate(e)
    def test_no_generic_high_power(self):
        e=episode();e['heating']['seconds'].pop('700')
        with self.assertRaises(ValueError):validate(e)
    def test_missing_time_basis(self):
        e=episode();e['heating']['basis']=''
        with self.assertRaises(ValueError):validate(e)
    def test_swapped_wattage_time_pairs(self):
        e=episode();e['scenes'][2]['body']='700W 30초 · 1000W 45초'
        with self.assertRaises(ValueError):validate(e)
    def test_incorrect_visible_time(self):
        e=episode();e['heating']['seconds']['700']=50
        with self.assertRaises(ValueError):validate(e)
    def test_immediate_hero_and_signature(self):
        for index,kind in [(0,'logo'),(-1,'logo')]:
            e=episode();e['scenes'][index]['kind']=kind
            with self.assertRaises(ValueError):validate(e)
    def test_missing_first_frame_ingredient_hook(self):
        e=episode();e['scenes'][0]['title']='전자레인지로 간편하게'
        with self.assertRaises(ValueError):validate(e)
    def test_false_ingredient_count_blocked(self):
        e=episode();e['ingredients'].append(dict(name='버터',unit='찻숟가락',amount=1))
        with self.assertRaises(ValueError):validate(e)
    def test_optional_ingredient_count_not_exaggerated(self):
        e=episode();e['ingredients'].append(dict(name='모차렐라',unit='g',amount=10,optional=True));validate(e)
    def test_bad_signature(self):
        e=episode();e['scenes'][-1]['narration']='다음에 만나요'
        with self.assertRaises(ValueError):validate(e)
    def test_nan_duration(self):
        e=episode();e['scenes'][0]['duration']=float('nan')
        with self.assertRaises(ValueError):validate(e)
    def test_unit_pause(self):
        e=episode();e['scenes'][1]['narration']='치즈 28, 그램'
        with self.assertRaises(ValueError):validate(e)
    def test_step_image_change(self):
        e=episode();e['scenes'][1]['image']='0.png'
        with self.assertRaises(ValueError):validate(e)
    def test_optional_cheese_label(self):
        e=episode();e['scenes'][0]['optional_ingredient_visual']=True
        with self.assertRaises(ValueError):validate(e)
        e['scenes'][0]['footer']='모차렐라 추가 예시';validate(e)
    def test_english_srt_contiguous(self):
        s=english_srt(episode())
        self.assertIn('00:00:00,000 --> 00:00:03,000',s)
        self.assertIn('00:00:09,000 --> 00:00:12,000',s)
        self.assertEqual(s.count(' --> '),4)
    def test_srt_injection(self):
        e=episode();e['scenes'][1]['english']='bad --> cue'
        with self.assertRaises(ValueError):english_srt(e)
    def test_contain_no_crop(self):
        self.assertEqual(fit_rect((1600,900),(0,0,630,815)),(0,230,630,354))
    def test_schedule_metadata(self):
        self.assertEqual(scheduled_at('2026-11-17'),'2026-11-17T19:00:00+09:00')
    def test_empty_queue(self):
        with tempfile.TemporaryDirectory() as p:
            self.assertEqual(run([],Path(p)/'state.json',p,'2026-11-17')['status'],'no_ready_episode')
    def test_duplicate_date(self):
        with self.assertRaises(ValueError):run([{'date':'x'},{'date':'x'}],'unused','unused','x')
    def test_not_reviewed(self):
        self.assertEqual(run([{'date':'x'}],'unused','unused','x')['status'],'awaiting_recipe_and_rights_review')
    def test_retry_protection(self):
        with tempfile.TemporaryDirectory() as p:
            p=Path(p);m=p/'manifest.json';m.write_text(json.dumps(episode()))
            q=[dict(date='2026-11-17',reviewed=True,manifest=str(m))]
            calls=[]
            def renderer(*args):calls.append(1);return 'video.mp4'
            self.assertFalse(run(q,p/'state.json',p,'2026-11-17',renderer)['uploaded'])
            self.assertEqual(run(q,p/'state.json',p,'2026-11-17',renderer)['status'],'already_attempted')
            self.assertEqual(len(calls),1)
    def test_failed_render_not_retried(self):
        with tempfile.TemporaryDirectory() as p:
            p=Path(p);m=p/'manifest.json';m.write_text(json.dumps(episode()))
            q=[dict(date='2026-11-17',reviewed=True,manifest=str(m))]
            def fail(*args):raise RuntimeError('interrupted')
            with self.assertRaises(RuntimeError):run(q,p/'state',p,'2026-11-17',fail)
            self.assertEqual(run(q,p/'state',p,'2026-11-17',fail)['status'],'already_attempted')
    def test_card_render(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as p:
            p=Path(p);Image.new('RGB',(1600,900),'green').save(p/'food.png')
            Image.new('RGBA',(600,120),'red').save(p/'logo.png')
            s=episode()['scenes'][0];s['image']=str(p/'food.png')
            render_card(s,p/'logo.png',p/'card.png')
            with Image.open(p/'card.png') as image:
                self.assertEqual(image.size,(720,1280))
                self.assertEqual(image.getpixel((40,210)),(255,253,247))
                self.assertEqual(image.getpixel((350,600)),(0,128,0))
    def test_unfittable_text_rejected(self):
        from PIL import Image,ImageDraw
        from world_kitchen.pipeline import draw_fit
        with self.assertRaises(ValueError):draw_fit(ImageDraw.Draw(Image.new('RGB',(720,1280))),
            '아'*100,(0,0,100,30),TEMPLATE['font'],44,'black')

if __name__=='__main__': unittest.main()
