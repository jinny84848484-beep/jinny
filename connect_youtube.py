"""Loopback-only Google OAuth onboarding with PKCE and encrypted local storage.

Run with --client path-to-downloaded-desktop-client.json. Never prints credentials.
The user selects and authorizes each channel on Google's own consent screen.
"""
import argparse, base64, hashlib, html, json, secrets, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import pipeline as p
from cloud_runner import http, verify_channel

SCOPES=['https://www.googleapis.com/auth/youtube.upload','https://www.googleapis.com/auth/youtube.readonly']
PENDING={}
CSRF=secrets.token_urlsafe(32)
CLIENT={}
PORT=8766

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass  # Callback codes must not enter access logs.
    def page(self,message='',code=200):
        forms=''
        for key,ch in p.cfg()['channels'].items():
            connected=(p.STATE/('youtube-'+key+'.dpapi')).exists()
            forms+=f'<p>{html.escape(ch["name"])}: {"연결 완료" if connected else "연결 필요"}</p><form method="post" action="/connect"><input type="hidden" name="csrf" value="{CSRF}"><input type="hidden" name="channel" value="{key}"><button>이 채널 연결하기</button></form>'
        body=('<!doctype html><html lang="ko"><meta charset="utf-8"><title>YouTube 자동 업로드 연결</title><h1>YouTube 자동 업로드 연결</h1><p>'+html.escape(message)+'</p><p>Google 화면에서 선택한 채널의 업로드 및 조회 권한을 연결합니다. 인증 정보는 이 PC의 암호화 저장소에 저장됩니다.</p>'+forms+'</html>').encode()
        self.send_response(code);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Security-Policy',"default-src 'none'; form-action 'self'; frame-ancestors 'none'");self.end_headers();self.wfile.write(body)
    def valid_host(self):return self.headers.get('Host')==f'127.0.0.1:{PORT}'
    def do_POST(self):
        if not self.valid_host() or self.path!='/connect':return self.page('잘못된 요청',400)
        if self.headers.get('Origin') not in {None,f'http://127.0.0.1:{PORT}'}:return self.page('잘못된 출처',403)
        length=int(self.headers.get('Content-Length','0'))
        if not 0<length<2048:return self.page('잘못된 요청',400)
        form=urllib.parse.parse_qs(self.rfile.read(length).decode())
        if not secrets.compare_digest(form.get('csrf',[''])[0],CSRF):return self.page('요청을 다시 시작하세요',403)
        channel=form.get('channel',[''])[0]
        if channel not in p.cfg()['channels']:return self.page('알 수 없는 채널',400)
        state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(64)
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        PENDING[state]={'channel':channel,'verifier':verifier,'expires':time.monotonic()+900}
        query={'client_id':CLIENT['client_id'],'redirect_uri':f'http://127.0.0.1:{PORT}/callback','response_type':'code','scope':' '.join(SCOPES),'state':state,'code_challenge':challenge,'code_challenge_method':'S256','access_type':'offline','prompt':'consent select_account'}
        self.send_response(303);self.send_header('Location','https://accounts.google.com/o/oauth2/v2/auth?'+urllib.parse.urlencode(query));self.send_header('Cache-Control','no-store');self.end_headers()
    def do_GET(self):
        if not self.valid_host():return self.page('잘못된 요청',400)
        parsed=urllib.parse.urlparse(self.path)
        if parsed.path=='/':return self.page()
        if parsed.path!='/callback':return self.page('페이지 없음',404)
        query=urllib.parse.parse_qs(parsed.query);item=PENDING.pop(query.get('state',[''])[0],None)
        if not item or item['expires']<time.monotonic():return self.page('인증 요청이 만료되었습니다. 다시 연결하세요.',400)
        if query.get('error'):return self.page('Google 연결이 승인되지 않았습니다.',400)
        try:
            data=urllib.parse.urlencode({'client_id':CLIENT['client_id'],'client_secret':CLIENT['client_secret'],'code':query['code'][0],'code_verifier':item['verifier'],'redirect_uri':f'http://127.0.0.1:{PORT}/callback','grant_type':'authorization_code'}).encode()
            result=http('https://oauth2.googleapis.com/token',data,{'Content-Type':'application/x-www-form-urlencoded'})
            if not set(SCOPES).issubset(set(result.get('scope','').split())):raise ValueError('Missing scope')
            channel=item['channel'];channel_id=verify_channel(channel,result['access_token'])
            if not result.get('refresh_token'):raise ValueError('Missing refresh token')
            secret={'channel_id':channel_id,'client_id':CLIENT['client_id'],'client_secret':CLIENT['client_secret'],'refresh_token':result['refresh_token']}
            target=p.STATE/('youtube-'+channel+'.dpapi');target.write_bytes(p.protect(json.dumps(secret).encode()))
            p.write(p.STATE/('youtube-'+channel+'-status.json'),{'channel_id':channel_id,'connected':True,'at':p.now().isoformat()})
            self.send_response(303);self.send_header('Location','/');self.end_headers()
        except Exception:
            self.page('연결을 저장하지 않았습니다. 채널 선택과 권한을 확인한 뒤 다시 시도하세요.',400)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--client',required=True);args=parser.parse_args()
    CLIENT=json.loads(Path(args.client).read_text(encoding='utf-8'))['installed']
    if CLIENT.get('auth_uri')!='https://accounts.google.com/o/oauth2/auth' or CLIENT.get('token_uri')!='https://oauth2.googleapis.com/token':raise SystemExit('Unexpected OAuth provider')
    print(f'YouTube connection page: http://127.0.0.1:{PORT}/',flush=True)
    ThreadingHTTPServer(('127.0.0.1',PORT),Handler).serve_forever()
