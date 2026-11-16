"""Cooking-only credential transport: no redirects, retries or response-body errors."""
import json, urllib.request, urllib.parse, urllib.error
HOSTS={'www.googleapis.com','youtube.googleapis.com','oauth2.googleapis.com'}
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):raise RuntimeError('Google redirect rejected')
def validate(url):
 p=urllib.parse.urlsplit(url)
 if p.scheme!='https' or p.hostname not in HOSTS or p.port not in (None,443) or p.username or p.password or p.fragment:raise ValueError('Unexpected Google endpoint')
def open_request(request,timeout=180):
 validate(request.full_url)
 try:return urllib.request.build_opener(NoRedirect()).open(request,timeout=timeout)
 except urllib.error.HTTPError as e:raise RuntimeError('Google HTTP '+str(e.code)) from None
 except urllib.error.URLError:raise RuntimeError('Google response uncertain; reconcile without retry') from None
def http(url,body=None,headers=None,method=None):
 hdr=dict(headers or {})
 if body is not None and not isinstance(body,bytes):body=json.dumps(body).encode();hdr.setdefault('Content-Type','application/json')
 with open_request(urllib.request.Request(url,data=body,headers=hdr,method=method)) as r:
  raw=r.read();return json.loads(raw) if raw else {}
