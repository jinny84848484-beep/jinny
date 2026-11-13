"""Read-only OAuth/channel identity check. No uploader or production imports."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

CHANNEL_ID='UCWFQVq3zxSnMi1sa0dUTVMQ'

class CheckFailed(Exception):
    pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise CheckFailed('REDIRECT_BLOCKED')

def request_json(url,data=None,headers=None):
    request=urllib.request.Request(url,data=data,headers=headers or {},method='POST' if data is not None else 'GET')
    try:
        with urllib.request.build_opener(NoRedirect).open(request,timeout=30) as response:
            raw=response.read(1024*1024+1)
            if len(raw)>1024*1024:raise CheckFailed('RESPONSE_TOO_LARGE')
            value=json.loads(raw)
            if not isinstance(value,dict):raise CheckFailed('INVALID_RESPONSE')
            return value
    except CheckFailed:raise
    except Exception:raise CheckFailed('AUTH_SERVICE_ERROR') from None

def check():
    raw=os.environ.get('YOUTUBE_KOREAN_OAUTH')
    if not raw:raise CheckFailed('MISSING_OAUTH')
    try:credentials=json.loads(raw)
    except Exception:raise CheckFailed('INVALID_OAUTH_FORMAT') from None
    if not isinstance(credentials,dict):raise CheckFailed('INVALID_OAUTH_FORMAT')
    if credentials.get('channel_id')!=CHANNEL_ID:raise CheckFailed('STORED_CHANNEL_MISMATCH')
    fields=('client_id','client_secret','refresh_token')
    if any(not isinstance(credentials.get(k),str) or not credentials[k].strip() for k in fields):raise CheckFailed('MISSING_OAUTH_FIELDS')
    body=urllib.parse.urlencode({**{k:credentials[k] for k in fields},'grant_type':'refresh_token'}).encode()
    token=request_json('https://oauth2.googleapis.com/token',body,{'Content-Type':'application/x-www-form-urlencoded'}).get('access_token')
    if not isinstance(token,str) or not token:raise CheckFailed('TOKEN_REFRESH_FAILED')
    result=request_json('https://www.googleapis.com/youtube/v3/channels?part=id&mine=true',headers={'Authorization':'Bearer '+token})
    items=result.get('items')
    if not isinstance(items,list) or len(items)!=1 or not isinstance(items[0],dict) or items[0].get('id')!=CHANNEL_ID:raise CheckFailed('AUTHENTICATED_CHANNEL_MISMATCH')
    return CHANNEL_ID

def main():
    try:
        channel=check()
    except CheckFailed as exc:
        # Only local fixed codes are surfaced, never provider payloads/credentials.
        print('Corgi auth check: '+str(exc));return 1
    except Exception:
        print('Corgi auth check: INTERNAL_CHECK_ERROR');return 1
    print('Corgi auth check: VERIFIED '+channel+' (identity only; upload capability and quota not tested)');return 0

if __name__=='__main__':
    raise SystemExit(main())
