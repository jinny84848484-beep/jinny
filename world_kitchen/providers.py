"""World Kitchen — every outside connection in one place: OpenAI (text, images),
ElevenLabs (voice) and YouTube (channel check, duplicate check, scheduled upload).
Account, key or credit problems raise RuntimeError starting with ALARM (CEO alarm).
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

from .pipeline import TEMPLATE

ALARM = 'CEO ALARM (World Kitchen connection/credits): '


def http(url, data=None, headers=None, method=None, timeout=180):
    body = None
    if data is not None:
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
        headers = {'Content-Type': 'application/json', **(headers or {})}
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            ctype = resp.headers.get('Content-Type', '')
            return json.loads(raw.decode() or '{}') if 'json' in ctype or raw[:1] in (b'{', b'[') else raw
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors='replace')[:300]
        if exc.code in (401, 403) or 'insufficient_quota' in detail or 'quota_exceeded' in detail:
            raise RuntimeError(ALARM + 'HTTP ' + str(exc.code) + ' from ' + urllib.parse.urlparse(url).hostname
                               + ' (key, OAuth or credits): ' + detail[:160]) from None
        raise RuntimeError('HTTP ' + str(exc.code) + ' ' + url.split('?')[0] + ': ' + detail) from None


def openai_text(prompt, instructions, *, search):
    body = {'model': os.environ.get('OPENAI_MODEL', 'gpt-5.4'), 'instructions': instructions,
            'input': prompt, 'max_output_tokens': 6000, 'store': False}
    if search:
        body.update(tools=[{'type': 'web_search'}], tool_choice='required')
    else:
        body['text'] = {'format': {'type': 'json_object'}}
    data = http('https://api.openai.com/v1/responses', body, {'Authorization': 'Bearer ' + os.environ['OPENAI_API_KEY']})
    if data.get('status') != 'completed':
        raise RuntimeError('Incomplete OpenAI response')
    return '\n'.join(c.get('text', '') for item in data.get('output', []) for c in item.get('content', [])
                     if c.get('type') == 'output_text')


def openai_image(prompt, path):
    body = {'model': os.environ.get('KITCHEN_IMAGE_MODEL', 'gpt-image-1'), 'prompt': prompt,
            'size': '1024x1024', 'quality': 'medium', 'n': 1}
    data = http('https://api.openai.com/v1/images/generations', body,
                {'Authorization': 'Bearer ' + os.environ['OPENAI_API_KEY']}, timeout=240)
    item = (data.get('data') or [{}])[0]
    if not item.get('b64_json'):
        raise RuntimeError('Image generation returned no image')
    Path(path).write_bytes(base64.b64decode(item['b64_json']))


def tts(text, path):
    voice = urllib.parse.quote(os.environ['KITCHEN_VOICE_ID'], safe='')
    audio = http('https://api.elevenlabs.io/v1/text-to-speech/' + voice + '?output_format=mp3_44100_128',
                 {'text': text, 'model_id': os.environ.get('KITCHEN_TTS_MODEL', 'eleven_multilingual_v2'),
                  'language_code': 'ko', 'voice_settings': {'stability': 0.5, 'similarity_boost': 0.75}},
                 {'xi-api-key': os.environ['ELEVENLABS_API_KEY']}, 'POST')
    if not isinstance(audio, bytes) or len(audio) < 1000:
        raise RuntimeError('Narration audio missing')
    Path(path).write_bytes(audio)


def youtube_token():
    try:
        c = json.loads(os.environ['YOUTUBE_KITCHEN_OAUTH'])
    except ValueError:
        raise RuntimeError(ALARM + 'YOUTUBE_KITCHEN_OAUTH is not valid JSON') from None
    if not isinstance(c, dict):
        raise RuntimeError(ALARM + 'YOUTUBE_KITCHEN_OAUTH must be a JSON object')
    c = {k: v.strip() if isinstance(v, str) else v for k, v in c.items()}  # stray spaces from copy-paste
    missing = [k for k in ('channel_id', 'client_id', 'client_secret', 'refresh_token') if not c.get(k)]
    if missing:
        raise RuntimeError(ALARM + 'YOUTUBE_KITCHEN_OAUTH is missing ' + ', '.join(missing))
    if c['channel_id'] != TEMPLATE['channel_id']:
        raise RuntimeError(ALARM + 'stored OAuth is for a different channel')
    client_id = c['client_id']
    if not re.fullmatch(r'\d+-[0-9a-z]+\.apps\.googleusercontent\.com', client_id):
        # The client ID is not secret; show only its outline to locate a paste mistake.
        raise RuntimeError(ALARM + 'client_id looks wrong (length ' + str(len(client_id)) + ', starts '
                           + repr(client_id[:14]) + ', ends ' + repr(client_id[-27:]) + ')')
    if not c['client_secret'].startswith('GOCSPX-') or not c['refresh_token'].startswith('1//'):
        raise RuntimeError(ALARM + 'client_secret must start with GOCSPX- and refresh_token with 1//')
    body = urllib.parse.urlencode({'client_id': c['client_id'], 'client_secret': c['client_secret'],
                                   'refresh_token': c['refresh_token'], 'grant_type': 'refresh_token'}).encode()
    result = http('https://oauth2.googleapis.com/token', body, {'Content-Type': 'application/x-www-form-urlencoded'})
    token = result.get('access_token')
    if not token:
        raise RuntimeError(ALARM + 'YouTube OAuth refresh failed; reconnect the World Kitchen channel')
    mine = http('https://www.googleapis.com/youtube/v3/channels?part=id,contentDetails&mine=true',
                headers={'Authorization': 'Bearer ' + token})
    items = mine.get('items', [])
    if [x['id'] for x in items] != [TEMPLATE['channel_id']]:
        raise RuntimeError(ALARM + 'authenticated YouTube channel is not World Kitchen')
    return token, items[0]['contentDetails']['relatedPlaylists']['uploads']


def find_existing(token, uploads, code):
    data = http('https://www.googleapis.com/youtube/v3/playlistItems?part=snippet&maxResults=25&playlistId='
                + urllib.parse.quote(uploads), headers={'Authorization': 'Bearer ' + token})
    for item in data.get('items', []):
        snippet = item.get('snippet', {})
        if code in snippet.get('description', ''):
            return snippet.get('resourceId', {}).get('videoId')
    return None


def upload(video, episode, token, publish_at):
    expected_time = (publish_at.astimezone(ZoneInfo('UTC')).strftime('%Y-%m-%dT%H:%M:%SZ')
                     if publish_at is not None else None)
    size = video.stat().st_size
    body = {'snippet': {'title': episode['title'], 'description': episode['description'], 'categoryId': '26',
                        'defaultLanguage': 'ko', 'defaultAudioLanguage': 'ko'},
            'status': {'privacyStatus': 'private',
                       'selfDeclaredMadeForKids': False, 'containsSyntheticMedia': True}}
    if expected_time is not None:
        body['status']['publishAt'] = expected_time
    req = urllib.request.Request('https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status',
                                 data=json.dumps(body).encode(), method='POST',
                                 headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json; charset=UTF-8',
                                          'X-Upload-Content-Length': str(size), 'X-Upload-Content-Type': 'video/mp4'})
    with urllib.request.urlopen(req, timeout=60) as r:
        session = r.headers['Location']
    host = urllib.parse.urlparse(session).hostname
    if host not in {'www.googleapis.com', 'youtube.googleapis.com'}:
        raise RuntimeError('Unexpected upload host')
    result = http(session, video.read_bytes(), {'Authorization': 'Bearer ' + token, 'Content-Type': 'video/mp4',
                                                'Content-Length': str(size)}, 'PUT', timeout=600)
    if result.get('snippet', {}).get('channelId') != TEMPLATE['channel_id']:
        raise RuntimeError('Upload response channel mismatch')
    status = result.get('status', {})
    if status.get('privacyStatus') != 'private' or bool(status.get('publishAt')) != (expected_time is not None):
        raise RuntimeError('Upload privacy or schedule does not match the requested settings; check Studio')
    return result['id']
