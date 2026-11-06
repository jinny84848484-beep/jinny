"""Production adapter; constructed only by an explicitly authorized manual driver.

This module is not wired into a workflow. No network calls on import. It uses the
pinned production renderer and upload code without bypassing source/media review.
"""
import copy
import json
import os
import re
import base64
import io
from urllib.parse import urlparse
import cloud_runner as c
import additional_batch as a

REPOSITORY = 'jinny84848484-beep/jinny'


class Runtime:
    def __init__(self, approved_head, verified_tts_terms=None):
        self.approved_head = approved_head
        self.verified_tts_terms = verified_tts_terms

    def today(self): return c.p.now().date().isoformat()
    def now(self): return c.p.now().isoformat()
    def run_id(self): return os.environ['GITHUB_RUN_ID']
    def config(self): return c.p.cfg()
    def journal_get(self): return c.journal_get()
    def journal_put(self, ledger, sha): return c.journal_put(ledger, sha)

    def assert_context(self):
        a.require(os.environ.get('GITHUB_REPOSITORY') == REPOSITORY and
                  os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch' and
                  os.environ.get('GITHUB_REF') == 'refs/heads/main' and
                  os.environ.get('GITHUB_RUN_ATTEMPT') == '1' and
                  os.environ.get('PUBLIC_UPLOADS_ENABLED') == 'true' and
                  re.fullmatch('[0-9a-f]{40}', self.approved_head or '') is not None and
                  os.environ.get('GITHUB_SHA') == self.approved_head,
                  'Explicit manual production context required')
        # Deploying driver must share shorts-production concurrency with daily job.
        a.require(os.environ.get('ADDITIONAL_CONCURRENCY_GROUP') == 'shorts-production',
                  'Shared production concurrency is required')

    def bind_authorization(self, approval, packet):
        self.billing_config_hash = approval['config_hash']
        self.production_date = packet['production_date']
        self.approved_packet = copy.deepcopy(packet)

    def assert_tts_budget(self, costs, characters):
        a.require(getattr(self, 'production_date', None) == self.today(), 'Billing production date expired')
        a.require(a.digest(self.config()) == getattr(self, 'billing_config_hash', None), 'Billing config changed')
        self.billing_costs = copy.deepcopy(costs)
        self.billing_characters = characters
        payment = costs['tts_payment']
        if payment['mode'] == 'included':
            a.require(self.remaining_characters() >= characters, 'Insufficient included TTS credits')
            a.require(self.production_date == self.today(), 'Billing production date expired during balance check')
            return
        terms = self.verified_tts_terms
        a.require(isinstance(terms, dict) and a.digest(terms) == payment['pricing_evidence_hash']
                  and terms.get('provider') == 'ElevenLabs' and terms.get('currency') == payment['currency']
                  and terms.get('model_id') == self.config()['channels']['ojirap'].get('model_id')
                  and terms.get('valid_on') == self.today()
                  and terms.get('existing_metered_account') is True
                  and terms.get('includes_all_taxes_fees_and_model_multipliers') is True,
                  'Verified current metered pricing/account terms unavailable')
        try:
            actual = a.Decimal(terms['max_cost_per_character'])
            approved = a.Decimal(payment['max_cost_per_character'])
            ceiling = a.Decimal(payment['max_total'])
        except (KeyError, a.InvalidOperation, TypeError):
            raise ValueError('Invalid metered pricing terms') from None
        a.require(actual.is_finite() and actual > 0 and actual <= approved
                  and actual * characters <= ceiling, 'Metered TTS price exceeds approved allowance')

    def remaining_characters(self):
        sub = c.p.subscription()
        a.require(type(sub.get('character_limit')) is int and
                  type(sub.get('character_count')) is int, 'Included credit balance unavailable')
        return sub['character_limit'] - sub['character_count']

    def validate_job(self, job):
        candidate = copy.deepcopy(job)
        candidate.update(editorial_status='verified', verified_at=self.now())
        for source in candidate['sources']:
            source['checked_at'] = self.now()
        c.p.validate(candidate)  # Structural check only; no claim that review passed.
        a.require(candidate['category'] in c.CATEGORIES, 'Unknown category')

    def validate_media(self, packet, *, require_local=True):
        if packet.get('media_policy') == 'original_graphics':
            return self.validate_graphics(packet, require_local=require_local)
        media = packet.get('media', {})
        a.require(2 <= len(media) <= 4, 'Two to four licensed exact-subject images required')
        for key, value in media.items():
            a.require(re.fullmatch(r'commons-[0-9]+-[0-9a-f]{8}', key) is not None and
                      value.get('topic_key') == packet['job']['topic_key'] and
                      value.get('media_type') == 'image' and
                      value.get('license') in {'CC0', 'CC0 1.0', 'Public domain',
                                              'CC BY 2.0', 'CC BY 2.5', 'CC BY 3.0', 'CC BY 4.0'} and
                      value.get('credit') and value.get('notice') and
                      value.get('title', '').startswith('File:') and
                      value.get('path') in {'assets/' + key + '.jpg', 'assets/' + key + '.png'},
                      'Invalid licensed media metadata')
            for field, host in (('url', 'upload.wikimedia.org'), ('source', 'commons.wikimedia.org')):
                parsed = urlparse(value.get(field, ''))
                a.require(parsed.scheme == 'https' and parsed.hostname == host and not parsed.username,
                          'Unapproved media URL')
            a.require(not value['license'].startswith('CC BY ') or value.get('license_url'),
                      'Attribution license URL required')
            # Content hash pins actual bytes approved for provider inspection and rendering.
            a.require(re.fullmatch('[0-9a-f]{64}', value.get('sha256', '')) is not None,
                      'Pinned media byte hash required')
            path = (c.p.ROOT / value['path']).resolve()
            a.require(path.is_relative_to(c.p.ROOT.resolve()), 'Unsafe media path')
            if require_local:
                a.require(path.is_file() and a.hashlib.sha256(path.read_bytes()).hexdigest() == value['sha256'],
                          'Media bytes missing or changed')

    def materialize_media(self, packet):
        if packet.get('media_policy') == 'original_graphics':
            import original_graphics as graphics
            root = (c.p.ROOT / 'assets' / packet['batch_id']).resolve()
            a.require(root.is_relative_to(c.p.ROOT.resolve()), 'Unsafe original graphic path')
            manifest = packet.get('graphics_manifest', {})
            a.require(packet == self.approved_packet, 'Unapproved graphic materialization')
            try:
                graphics.validate_approved_graphics(packet['job'], manifest, root,
                    approved_story_hash=a.digest(packet['job']), production_date=packet['production_date'],
                    graphics_spec=packet['graphics_spec'], files=packet.get('graphics_files'))
                a.require(isinstance(packet.get('graphics_files'), dict), 'Approved PNG transport required')
            except Exception:
                raise ValueError('graphics_bytes_invalid') from None
            try:
                self.graphics_review = graphics.render_graphics_review_frames(packet['job'], manifest, root, graphics_spec=packet['graphics_spec'])
            except Exception:
                raise ValueError('graphics_preview_failed') from None
            return
        self.validate_media(packet, require_local=False)
        import urllib.request
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl): return None
        opener = urllib.request.build_opener(NoRedirect)
        for media in packet['media'].values():
            path = (c.p.ROOT / media['path']).resolve()
            if path.is_file():
                a.require(a.hashlib.sha256(path.read_bytes()).hexdigest() == media['sha256'],
                          'Existing media bytes differ from approval')
                continue
            req = urllib.request.Request(media['url'], headers={'User-Agent': 'JinnyShorts/1.0'})
            with opener.open(req, timeout=60) as response:
                raw = response.read(80_000_001)
            a.require(len(raw) <= 80_000_000 and a.hashlib.sha256(raw).hexdigest() == media['sha256'],
                      'Downloaded media differs from approval')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)

    @staticmethod
    def graphics_media(job, batch_id, manifest):
        return {'graphic-' + batch_id + '-' + str(card['scene_index']): {
            'path': 'assets/' + batch_id + '/' + card['path'], 'sha256': card['sha256'],
            'media_type': 'image', 'topic_key': job['topic_key'], 'notice': card['notice'],
            'credit': 'Original typography and diagram by this channel',
            'source': 'Original explanatory graphic based on the cited reporting',
            'license': 'Original channel artwork', 'license_url': '',
            'disclaimer': card['notice'] + ' · 실제 사건 영상이나 자료사진이 아닙니다.',
            'relevance_verified': False, 'scene_index': card['scene_index']}
            for card in manifest['cards']}

    def validate_graphics(self, packet, *, require_local=True):
        import original_graphics as graphics
        original = self.approved_packet['job']
        manifest = packet.get('graphics_manifest', {})
        a.require(packet.get('media_policy') == 'original_graphics' and
                  packet.get('batch_id') == self.approved_packet['batch_id'] and
                  manifest == self.approved_packet.get('graphics_manifest') and
                  manifest.get('job_content_hash') == a.digest(original), 'Unapproved original graphics')
        expected = self.graphics_media(original, packet['batch_id'], manifest)
        actual = copy.deepcopy(packet.get('media', {}))
        for value in actual.values(): value['relevance_verified'] = False
        a.require(actual == expected, 'Original graphic metadata changed')
        root = (c.p.ROOT / 'assets' / packet['batch_id']).resolve()
        a.require(root.is_relative_to(c.p.ROOT.resolve()), 'Unsafe original graphic path')
        if require_local:
            graphics.validate_approved_graphics(original, manifest, root,
                approved_story_hash=a.digest(original), production_date=packet['production_date'],
                graphics_spec=self.approved_packet['graphics_spec'])

    def image_data(self, path, expected_hash):
        from PIL import Image, ImageOps
        path = path.resolve()
        a.require(path.is_relative_to(c.p.ROOT.resolve()) and path.is_file(), 'Review frame path invalid')
        raw = path.read_bytes()
        a.require(a.hashlib.sha256(raw).hexdigest() == expected_hash, 'Review frame changed')
        with Image.open(io.BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source).convert('RGB'); image.thumbnail((1080, 1920))
            output = io.BytesIO(); image.save(output, format='JPEG', quality=90)
        return 'data:image/jpeg;base64,' + base64.b64encode(output.getvalue()).decode()

    def review_request(self, packet, model, output_tokens, tool_calls):
        allowed = c.REVIEW_ISSUES - {'review_contract', 'english_hook'}
        instructions = '''Independently audit ONLY this ONE Korean personal-commentary Short, not a batch.
Browse the actual cited source articles and inspect all attached image bytes. Check current material developments as of the supplied production_date, including warrant status. A later production date does not turn older reporting into fresh evidence. Fail rather than rewrite or publish if later developments make the approved script or graphic labels misleading. Check material factual claims,
source independence, natural Korean, distinctions between allegation/arrest/warrant/conviction, privacy,
and exact pictured-subject relevance. Do not infer guilt or invent confirmation. Verify reusable image
licenses and archive notices. Generic stock, unrelated buildings, symbolic images, or invented event footage
cannot substitute for directly relevant photographs. Fail if two usable directly relevant photos are absent.
Return ONLY JSON with status, issues, selection, source_checks. status PASS or FAIL. issues is an array of objects
with code, job_index:0, scene_index (-1 or scene index), explanation (1-1600 characters).
On PASS issues is empty and selection maps the supplied job ID to one approved image ID per scene,
using at least two distinct images. On FAIL provide issues and empty selection. Never rewrite the script.
source_checks is an array with exactly url, accessed, verified_scene_indices, independence_group.
accessed must be true only when you opened and read the actual article in this request. List only supplied
source URLs. verified_scene_indices lists factual scene indices adequately supported by that article.
independence_group names the independently operated reporting organization; syndicated copies share one group.
PASS requires at least two independent reporting groups whose actual articles you opened and read, and
coverage of every scene containing factual claims. Do not label a search snippet or blocked page accessed.
Allowed codes: ''' + ', '.join(sorted(allowed)) + '\n' + c.SEARCH_SAFETY + c.REVIEW_CALIBRATION
        graphics_mode = packet.get('media_policy') == 'original_graphics'
        if graphics_mode:
            instructions = instructions.replace('and exact pictured-subject relevance.', 'and relevance of the printed factual content and explanatory-graphic provenance.')
            instructions = instructions.replace(
                'Verify reusable image\nlicenses and archive notices. Generic stock, unrelated buildings, symbolic images, or invented event footage\ncannot substitute for directly relevant photographs. Fail if two usable directly relevant photos are absent.',
                'This is the separately approved ORIGINAL GRAPHICS policy: sober original typography and geometric timelines, not photographs. Inspect actual composed 1080x1920 review frames. Require the notice 자료를 바탕으로 구성한 그래픽, clear reported-fact versus channel-opinion labels, legible Korean, no invented event reconstruction, faces, news images or implication of actual footage. Every printed factual label must be supported by the cited reporting. Five scene-specific graphics are required; licensed photographs are NOT required under this policy.')
            instructions = instructions.replace('using at least two distinct images.',
                'using exactly the matching scene_index graphic for each scene, in order.')
        # Transport bytes are verified during materialization and sent as image
        # parts below. They must never be duplicated into model text tokens.
        review_packet = copy.deepcopy(packet)
        review_packet.pop('graphics_files', None)
        if graphics_mode:
            review_packet['review_image_context'] = {
                'kind': 'composed_full_frame', 'width': 1080, 'height': 1920,
                'note': 'Attached images are full composed review frames. The graphics_manifest describes only embedded artwork cards.',
                'frames': self.graphics_review['frames'],
            }
        review_text = json.dumps(review_packet, ensure_ascii=False)
        a.require(len(review_text) <= 50000, 'Review text exceeds safe input bound')
        content = [{'type': 'input_text', 'text': review_text}]
        for key, media in packet['media'].items():
            if graphics_mode:
                frame = self.graphics_review['frames'][media['scene_index']]
                url = self.image_data(c.p.ROOT / 'assets' / packet['batch_id'] / frame['path'], frame['sha256'])
            else:
                url = c.review_image(media)
            content.extend([{'type': 'input_text', 'text': 'Image ID: ' + key},
                            {'type': 'input_image', 'image_url': url, 'detail': 'high'}])
        return {'model': model, 'instructions': instructions, 'input': [{'role': 'user', 'content': content}],
                'max_output_tokens': output_tokens, 'max_tool_calls': tool_calls, 'store': False,
                'tools': [{'type': 'web_search'}], 'tool_choice': 'required',
                'include': ['web_search_call.action.sources']}

    def review(self, request):
        # One request; no hidden retry, fallback model, repair or regeneration.
        a.require(self.production_date == self.today(), 'Review production date expired')
        data = c.http('https://api.openai.com/v1/responses', request,
                      {'Authorization': 'Bearer ' + os.environ['OPENAI_API_KEY']})
        a.require(data.get('status') == 'completed', 'Review response incomplete; reconcile without retry')
        a.require(c.research_sources(data), 'No recognized browsed-source evidence')
        return data

    def save_checkpoint(self, batch, stage, response):
        c.p.write(c.p.ROOT / 'runs' / batch[:10] / 'additional' / batch / 'checkpoints' / (stage + '.json'), response)

    def apply_review(self, packet, response):
        text = '\n'.join(part.get('text', '') for item in response.get('output', [])
                         for part in item.get('content', []) if part.get('type') == 'output_text')
        result = copy.deepcopy(packet)
        parsed = json.loads(text)
        a.require(isinstance(parsed, dict) and set(parsed) ==
                  {'status', 'issues', 'selection', 'source_checks'}, 'Source-audited review contract required')
        checks = parsed.pop('source_checks')
        a.require(isinstance(checks, list), 'Source checks required')
        supplied = {source['url'] for source in result['job']['sources']}
        opened = {item.get('action', {}).get('url') for item in response.get('output', [])
                  if item.get('type') == 'web_search_call' and item.get('status') == 'completed'
                  and item.get('action', {}).get('type') == 'open_page'}
        covered = set(); groups = set(); domains = set(); checked_urls = set()
        for check in checks:
            a.require(isinstance(check, dict) and set(check) ==
                      {'url', 'accessed', 'verified_scene_indices', 'independence_group'} and
                      check['url'] in supplied and type(check['accessed']) is bool and
                      isinstance(check['verified_scene_indices'], list) and
                      all(type(i) is int and 0 <= i < len(result['job']['scenes'])
                          for i in check['verified_scene_indices']) and
                      isinstance(check['independence_group'], str) and check['independence_group'].strip(),
                      'Invalid source review audit')
            if check['accessed'] and check['verified_scene_indices']:
                a.require(check['url'] in opened, 'Reviewed article lacks completed open-page evidence')
                covered.update(check['verified_scene_indices'])
                groups.add(check['independence_group'].strip().lower())
                domains.add(urlparse(check['url']).hostname)
                checked_urls.add(check['url'])
        factual = {i for i, scene in enumerate(result['job']['scenes'])
                   if scene.get('kind') == 'fact' or scene.get('source_indices')}
        a.require(len(groups) >= 2 and len(domains) >= 2 and factual <= covered,
                  'Independent opened-source coverage incomplete')
        if packet.get('media_policy') == 'original_graphics':
            expected_keys = ['graphic-' + packet['batch_id'] + '-' + str(i) for i in range(len(result['job']['scenes']))]
            a.require(parsed == {'status': 'PASS', 'issues': [], 'selection': {result['job']['id']: expected_keys}},
                      'Original graphics factual/visual review did not pass')
            for scene, key in zip(result['job']['scenes'], expected_keys):
                scene['media'] = key; result['media'][key]['relevance_verified'] = True
        else:
            decision = c.parse_editorial_review(json.dumps(parsed), [result['job']], result['media'])
            a.require(decision['status'] == 'PASS', 'Editorial review failed; batch held')
            c.relevant_media.apply_review([result['job']], result['media'], decision['selection'])
        result['job'].update(editorial_status='verified', verified_at=self.now())
        for source in result['job']['sources']:
            # checked_at denotes this review's citation assessment, not a fabricated fetch.
            source['checked_at'] = self.now()
            source['article_read_this_review'] = source['url'] in checked_urls
        return result

    def prepare_media(self, reviewed):
        self.validate_media(reviewed)
        # All media is already pinned locally. No post-review download substitution.
        library = c.p.read(c.p.ROOT / 'assets/media-library.json', {})
        library.update(reviewed['media']); c.p.write(c.p.ROOT / 'assets/media-library.json', library)

    def render(self, reviewed):
        job = reviewed['job']
        path = c.p.ROOT / 'runs' / job['id'][:10] / 'additional' / reviewed['batch_id'] / 'jobs' / (job['id'] + '.json')
        c.p.write(path, job)
        folder = c.p.ROOT / 'runs' / job['id'][:10] / job['channel'] / job['id']
        # This path is new; never silently adopt old audio/render artifacts.
        a.require(not folder.exists(), 'Existing job artifacts require reconciliation')
        expected_job = a.digest(job)
        def billing_guard(actual_job, speech_text, actual_config):
            a.require(a.digest(actual_config) == self.billing_config_hash, 'Synthesis config differs from approval')
            a.require(a.digest(actual_job) == expected_job and
                      len(speech_text) == self.billing_characters,
                      'Synthesis text differs from approved reserved input')
            self.assert_tts_budget(self.billing_costs, len(speech_text))
            a.require(self.production_date == self.today(), 'Synthesis production date expired')
        c.media_renderer.render(path, billing_guard=billing_guard)
        return folder

    def render_qa_request(self, folder, reviewed):
        import subprocess
        alignment = c.p.read(folder / 'narration.json')['alignment']
        scenes = reviewed['job']['scenes']; cursor = 0; frames = []
        qa_root = c.p.ROOT / 'runs' / self.production_date / 'additional' / reviewed['batch_id'] / 'render-qa'
        qa_root.mkdir(parents=True, exist_ok=True)
        for index, scene in enumerate(scenes):
            text = scene['narration'].strip()
            begin = alignment['character_start_times_seconds'][cursor]
            end = alignment['character_end_times_seconds'][cursor + len(text) - 1]
            # Inspect two real frames per scene, not the synthetic preview frames.
            for label, stamp in [('early', begin + min(.20, (end - begin) / 4)),
                                 ('late', max(begin, end - .15))]:
                path = qa_root / (str(index) + '-' + label + '.png')
                subprocess.run([c.p.ffmpeg(), '-v', 'error', '-y', '-ss', str(stamp),
                                '-i', str(folder / 'short.mp4'), '-frames:v', '1', str(path)],
                               check=True, capture_output=True, timeout=60)
                frames.append({'scene_index': index, 'position': label, 'path': path.name,
                               'sha256': a.hashlib.sha256(path.read_bytes()).hexdigest()})
            cursor += len(text) + 1
        content = [{'type': 'input_text', 'text': json.dumps({'job': self.approved_packet['job'],
                    'notice': '자료를 바탕으로 구성한 그래픽', 'frames': frames}, ensure_ascii=False)}]
        for frame in frames:
            content.extend([{'type': 'input_text', 'text': 'Scene ' + str(frame['scene_index']) + ' ' + frame['position']},
                            {'type': 'input_image', 'image_url': self.image_data(qa_root / frame['path'], frame['sha256']),
                             'detail': 'high'}])
        c.p.write(qa_root / 'frames.json', frames)
        return {'model': a.MODEL, 'max_output_tokens': 1200, 'store': False,
                'text': {'format': {'type': 'json_object'}},
                'instructions': 'Audit these ACTUAL MP4 extracted frames before public upload. Check every scene: readable Korean, no cropped/overlapping important text or captions, sober original explanatory graphics, visible 자료를 바탕으로 구성한 그래픽 notice, clear fact/opinion labeling and no faces/news images/fabricated crime reconstruction. Compare visible text against the supplied already fact-reviewed script; reject added unsupported claims. These are sampled frames, not proof of full audio/video quality. Return ONLY JSON {"status":"PASS" or "FAIL","issues":[strings]}. PASS requires empty issues; any unresolved visual/content concern means FAIL. Do not invent missing evidence.',
                'input': [{'role': 'user', 'content': content}]}

    def render_qa(self, request):
        a.require(self.production_date == self.today(), 'Render QA production date expired')
        return c.http('https://api.openai.com/v1/responses', request,
                      {'Authorization': 'Bearer ' + os.environ['OPENAI_API_KEY']})

    def assert_render_qa(self, response):
        a.require(response.get('status') == 'completed', 'Render QA incomplete')
        text = '\n'.join(part.get('text', '') for item in response.get('output', [])
                         for part in item.get('content', []) if part.get('type') == 'output_text')
        a.require(json.loads(text) == {'status': 'PASS', 'issues': []}, 'Actual rendered-frame QA did not pass')

    def verify_target(self, channel, channel_id):
        a.require(c.p.cfg()['channels'][channel]['channel_id'] == channel_id, 'Target changed')
        self.token = c.youtube_token(channel); c.verify_channel(channel, self.token)

    def upload(self, folder, job):
        a.require(self.production_date == self.today(), 'Upload production date expired')
        video_id = c.upload(folder, job, self.token)
        return {'video_id': video_id, 'channel_id': job['channel_id'], 'privacy': 'public'}
