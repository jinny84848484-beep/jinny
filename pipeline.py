"""Local Shorts pipeline. Research/editorial decisions are made by the scheduled Codex chat.
No browser cookies, password scraping, OpenAI key or public publishing are used here.
"""
from __future__ import annotations
import argparse, base64, ctypes, datetime as dt, hashlib, json, os, re, secrets
import subprocess, sys, threading, urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent.parent
STATE = Path(os.environ.get("SHORTS_STATE_DIR", str(WORKSPACE / "work" / "shorts-state")))
STATE.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(WORKSPACE / "work" / "shorts-deps"))
KST = dt.timezone(dt.timedelta(hours=9))
CONFIG = ROOT / "config.json"
KEY_FILE = STATE / "elevenlabs.dpapi"
API = "https://api.elevenlabs.io"
LOCK = threading.RLock()

def now(): return dt.datetime.now(KST)
def read(path, default=None):
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else default
def write(path, obj):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)
def cfg(): return read(CONFIG)
def emit(obj): print(json.dumps(obj, ensure_ascii=True, indent=2))

class Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]

def protect(data: bytes, decrypt=False):
    if os.name != "nt": raise RuntimeError("This secret store requires Windows DPAPI")
    buf = ctypes.create_string_buffer(data)
    inp = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))); out = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]; kernel.LocalFree.restype = ctypes.c_void_p
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    if not fn(ctypes.byref(inp), None, None, None, None, 1, ctypes.byref(out)):
        raise ctypes.WinError(ctypes.get_last_error())
    try: return ctypes.string_at(out.data, out.size)
    finally: kernel.LocalFree(out.data)

def key():
    if KEY_FILE.exists(): return protect(KEY_FILE.read_bytes(), decrypt=True).decode()
    value = os.environ.get("ELEVENLABS_API_KEY")
    if value: return value
    raise RuntimeError("ElevenLabs connection is missing. Open the local setup page.")

def api(path, payload=None, api_key=None):
    # Host is fixed; credentials are never forwarded to a caller-supplied URL.
    req = urllib.request.Request(API + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"xi-api-key": api_key or key(), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=150) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        # Do not echo request headers, submitted keys, or provider response bodies.
        raise RuntimeError(f"ElevenLabs HTTP {exc.code}: check API permissions, voice access and credits.") from None

def list_voices(api_key=None):
    result=[]; token=None
    for _ in range(20):
        path="/v2/voices?page_size=100" + ("&next_page_token="+urllib.parse.quote(token) if token else "")
        data=api(path,api_key=api_key)
        result.extend({k:v.get(k) for k in ("voice_id","name","category","labels","preview_url")} for v in data.get("voices",[]))
        token=data.get("next_page_token")
        if not data.get("has_more") or not token: break
    return result

def subscription(api_key=None):
    data=api("/v1/user/subscription",api_key=api_key)
    return {k:data.get(k) for k in ("tier","character_count","character_limit","next_character_count_reset_unix")}

def status():
    c=cfg()
    return {"elevenlabs_connected": KEY_FILE.exists() or bool(os.environ.get("ELEVENLABS_API_KEY")),
        "voices_selected":all(ch["voice_id"] for ch in c["channels"].values()),
        "config":c,"voices":read(STATE/"voices.json",[]),
        "subscription":read(STATE/"subscription.json",{}),
        "last_collection":read(STATE/"collection-status.json",{}),
        "scheduler":read(STATE/"scheduler.json",{"status":"not_registered"}),
        "publishing":"render_only"}

def collect():
    results=[]; errors=[]
    for geo in ("KR","US"):
        url="https://trends.google.com/trending/rss?geo="+geo
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"ShortsResearch/1.0"})
            with urllib.request.urlopen(req,timeout=25) as response: raw=response.read(4_000_000)
            xml=ET.fromstring(raw); ns={"ht":"https://trends.google.com/trending/rss"}
            for item in xml.findall("./channel/item"):
                # Local-name extraction tolerates documented RSS namespace version changes.
                def get_local(node,name):
                    return next((ch.text or "" for ch in node if ch.tag.split("}")[-1]==name),"")
                news=[]
                for child in item:
                    if child.tag.split("}")[-1]=="news_item":
                        news.append({"title":get_local(child,"news_item_title"),"url":get_local(child,"news_item_url"),"publisher":get_local(child,"news_item_source")})
                results.append({"region":geo,"query":item.findtext("title"),"trend_started_at":item.findtext("pubDate"),
                    "approx_traffic":get_local(item,"approx_traffic"),"news_candidates":news,"feed":url})
        except Exception as exc: errors.append({"region":geo,"error":type(exc).__name__})
    data={"collected_at":now().isoformat(),"topics":results,"errors":errors,"notice":"Discovery signals only; each news claim must be verified against original sources before scripting."}
    out=ROOT/"runs"/now().date().isoformat()/"research"/"google-trends.json"
    write(out,data); write(STATE/"collection-status.json",{"at":data["collected_at"],"count":len(results),"errors":errors,"path":str(out)})
    if not results: raise RuntimeError("No trends fetched; do not fabricate replacement news.")
    return {"path":str(out),"topic_count":len(results),"errors":errors}

def validate(d):
    c=cfg(); ch=d.get("channel")
    if ch not in c["channels"]: raise ValueError("Unknown target channel")
    if d.get("channel_id")!=c["channels"][ch]["channel_id"]: raise ValueError("Channel identity mismatch")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,90}",d.get("id","")): raise ValueError("Invalid job id")
    if not d.get("verified_at") or d.get("editorial_status")!="verified": raise ValueError("Source verification required")
    checked=dt.datetime.fromisoformat(d["verified_at"])
    if checked.tzinfo is None or not -300 <= (now()-checked).total_seconds() <= 36*3600: raise ValueError("Refresh source verification before rendering")
    sources=d.get("sources",[])
    hosts={urllib.parse.urlparse(s.get("url","")).hostname for s in sources if s.get("url","").startswith("https://")}
    if len(hosts)<2: raise ValueError("Two independent source domains required")
    for s in sources:
        if not all(s.get(k) for k in ("title","url","checked_at")): raise ValueError("Incomplete source citation")
    scenes=d.get("scenes",[])
    if not 4<=len(scenes)<=10: raise ValueError("Use 4-10 scenes")
    for scene in scenes:
        if not all(scene.get(k) for k in ("headline","narration","kind")): raise ValueError("Incomplete scene")
        if scene["kind"] not in ("fact","opinion","hook","closing"): raise ValueError("Unknown scene kind")
        if scene["kind"]=="fact" and not scene.get("source_indices"): raise ValueError("Fact scene needs citations")
        for i in scene.get("source_indices",[]):
            if not isinstance(i,int) or not 0<=i<len(sources): raise ValueError("Bad citation index")
    text=" ".join(s["narration"].strip() for s in scenes)
    if len(text)>1800 or len(text)<80: raise ValueError("Narration must be 80-1800 characters")
    if not d.get("title") or len(d["title"])>100: raise ValueError("Title must be 1-100 characters")
    return text

@contextmanager
def process_lock(name):
    path=STATE/(name+".lock")
    try: handle=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError: raise RuntimeError("Another job or an interrupted job holds the lock; inspect before retrying.")
    try:
        os.write(handle,str(os.getpid()).encode()); os.close(handle); yield
    finally: path.unlink(missing_ok=True)

def synthesize(d,folder):
    text=validate(d); c=cfg(); voice=c["channels"][d["channel"]]["voice_id"]
    if not voice: raise RuntimeError("Choose the saved ElevenLabs voice on the local setup page")
    model=c['channels'][d['channel']].get('model_id',c['model_id'])
    settings=c['channels'][d['channel']].get('voice_settings',{'stability':0.5,'similarity_boost':0.75,'style':0,'use_speaker_boost':False})
    signature=hashlib.sha256(json.dumps([text,voice,model,settings],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    cache=read(folder/"narration.json")
    if cache:
        if cache.get("signature")!=signature: raise RuntimeError("Existing audio belongs to a different script/voice; use a new job id")
        if not (folder/"narration.mp3").exists(): raise RuntimeError("Cached audio missing; do not silently repeat paid synthesis")
        return cache
    with process_lock("tts"):
        ledger=read(STATE/"usage.json",{})
        day=now().date().isoformat(); entries=ledger.setdefault(day,[])
        if any(e["id"]==d["id"] for e in entries): raise RuntimeError("Prior TTS submission exists; reconcile it before retrying to avoid duplicate charges")
        if sum(e["characters"] for e in entries)+len(text)>c["daily_character_cap"]: raise RuntimeError("Local daily narration cap reached")
        sub=subscription()
        if sub.get("character_limit") is None or sub.get("character_count") is None: raise RuntimeError("Cannot verify included credit balance")
        if sub["character_limit"]-sub["character_count"]<len(text): raise RuntimeError("Insufficient included credits; automatic overage is not allowed")
        entry={"id":d["id"],"signature":signature,"characters":len(text),"state":"submitted","at":now().isoformat()}
        entries.append(entry); write(STATE/"usage.json",ledger)
        data=api("/v1/text-to-speech/"+urllib.parse.quote(voice,safe="")+"/with-timestamps?output_format=mp3_44100_128",
            {"text":text,"model_id":model,"voice_settings":settings,"language_code":c['channels'][d['channel']]['language']})
        audio=base64.b64decode(data["audio_base64"],validate=True)
        (folder/"narration.mp3").write_bytes(audio)
        cache={"signature":signature,"alignment":data.get("alignment"),"characters":len(text),"created_at":now().isoformat()}
        write(folder/"narration.json",cache)
        entry["state"]="completed"; write(STATE/"usage.json",ledger)
        return cache

def ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()

def srt_time(seconds):
    ms=round(seconds*1000); h,ms=divmod(ms,3600000); m,ms=divmod(ms,60000); s,ms=divmod(ms,1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def cues_for(text,alignment,limit):
    chars=alignment.get("characters",[]); starts=alignment.get("character_start_times_seconds",[]); ends=alignment.get("character_end_times_seconds",[])
    if "".join(chars)!=text or not(len(chars)==len(starts)==len(ends)): raise ValueError("Character alignment mismatch; audio is saved, but rendering needs timing review")
    cues=[]; start=0
    for i,c in enumerate(chars):
        if c in ".!?。\n" or (i-start>=limit and c.isspace()) or i-start>=limit+10 or i==len(chars)-1:
            line="".join(chars[start:i+1]).strip()
            if line: cues.append((starts[start],ends[i],line))
            start=i+1
    return cues

def font(size,bold=True):
    from PIL import ImageFont
    return ImageFont.truetype("C:/Windows/Fonts/"+("malgunbd.ttf" if bold else "malgun.ttf"),size)

def wrapped(draw,text,f,width):
    lines=[]; line=""
    for char in text:
        if char=="\n": lines.append(line); line=""; continue
        if line and draw.textlength(line+char,font=f)>width: lines.append(line.rstrip()); line=char.lstrip()
        else: line+=char
    if line: lines.append(line)
    return lines

def draw_text_fit(draw,text,box,color,max_size=88,min_size=28,bold=True):
    x,y,w,h=box
    for size in range(max_size,min_size-1,-2):
        f=font(size,bold); lines=wrapped(draw,text,f,w)
        if len(lines)*size*1.4<=h: break
    else: raise ValueError("Text overflows video layout; shorten scene text")
    for line in lines:
        draw.text((x,y),line,font=f,fill=color); y+=size*1.4

def scene_image(scene,channel,index,total,path,title):
    from PIL import Image,ImageDraw
    dark=channel["language"]=="ko"; fg="#FAFAF7" if dark else "#191919"; muted="#BABABA" if dark else "#655E54"
    im=Image.new("RGB",(1080,1920),channel["background"]); draw=ImageDraw.Draw(im)
    draw.rectangle((0,0,1080,18),fill=channel["accent"])
    draw_text_fit(draw,channel["name"],(74,95,870,95),fg,34,24)
    draw.text((74,225),f"{index+1:02} / {total:02}",font=font(32),fill=muted)
    label={"fact":"팩트" if dark else "THE FACTS","opinion":"내 생각" if dark else "MY TAKE","hook":"오늘의 이슈" if dark else "TODAY'S ISSUE","closing":"굳이 한마디" if dark else "ONE MORE THING"}[scene["kind"]]
    draw.rounded_rectangle((74,315,550,386),radius=16,fill=channel["accent"])
    draw.text((98,326),label,font=font(34),fill="#151515" if dark else "#FFFFFF")
    draw_text_fit(draw,scene["headline"],(74,445,850,500),fg,100,52)
    if scene.get("body"): draw_text_fit(draw,scene["body"],(78,1000,840,240),muted,44,30,False)
    draw.rectangle((74,1245,940,1250),fill=channel["accent"])
    draw_text_fit(draw,"@"+channel["handle"],(74,1715,800,72),muted,29,24)
    im.save(path)

def render(job_path,smoke=False):
    d=read(job_path); text=validate(d); c=cfg(); channel=c["channels"][d["channel"]]
    folder=ROOT/"runs"/now().date().isoformat()/d["channel"]/d["id"]
    folder.mkdir(parents=True,exist_ok=True)
    final=folder/("test-silent.mp4" if smoke else "short.mp4")
    if final.exists(): return {"status":"already_rendered","path":str(final)}
    write(folder/"script.json",d)
    if smoke:
        alignment={"characters":list(text),"character_start_times_seconds":[i*8/len(text) for i in range(len(text))],"character_end_times_seconds":[(i+1)*8/len(text) for i in range(len(text))]}
    else: alignment=synthesize(d,folder).get("alignment") or {}
    cues=cues_for(text,alignment,20 if channel["language"]=="ko" else 36)
    duration=alignment["character_end_times_seconds"][-1]+0.3
    if not smoke and not 15<=duration<=75: raise ValueError(f"Audio length {duration:.1f}s outside 15-75s; review before output")
    srt="\n\n".join(f"{i+1}\n{srt_time(a)} --> {srt_time(b)}\n{t}" for i,(a,b,t) in enumerate(cues))+"\n"
    (folder/"captions.srt").write_text(srt,encoding="utf-8")
    offsets=[]; offset=0
    for scene in d["scenes"]:
        offsets.append(offset); offset+=len(scene["narration"].strip())+1
    timings=[alignment["character_start_times_seconds"][i] for i in offsets]+[duration]
    lines=[]
    for i,scene in enumerate(d["scenes"]):
        path=folder/f"scene-{i:02}.png"; scene_image(scene,channel,i,len(d["scenes"]),path,d["title"])
        lines.extend([f"file '{path.name}'",f"duration {timings[i+1]-timings[i]:.5f}"])
    lines.append(f"file 'scene-{len(d['scenes'])-1:02}.png'")
    (folder/"scenes.txt").write_text("\n".join(lines),encoding="utf-8")
    cmd=[ffmpeg(),"-hide_banner","-loglevel","error","-y","-f","concat","-safe","0","-i","scenes.txt"]
    if smoke: cmd += ["-f","lavfi","-i","anullsrc=r=44100:cl=stereo"]
    else: cmd += ["-i","narration.mp3"]
    style="FontName=Malgun Gothic,FontSize=15,PrimaryColour=&HFFFFFF,OutlineColour=&H111111,Outline=1,Shadow=0,Alignment=2,MarginV=68,MarginL=24,MarginR=40"
    cmd += ["-vf",f"fps=30,subtitles=captions.srt:force_style='{style}'","-t",str(duration),"-c:v","libx264","-preset","fast","-crf","21","-pix_fmt","yuv420p","-c:a","aac","-b:a","128k","-movflags","+faststart",final.name]
    process=subprocess.run(cmd,cwd=folder,capture_output=True,text=True,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    if process.returncode: final.unlink(missing_ok=True); raise RuntimeError("Render failed: "+process.stderr[-1200:])
    description=d.get("description","")+"\n\nSources:\n"+"\n".join(s["url"] for s in d["sources"])
    write(folder/"upload-metadata.json",{"channel_id":d["channel_id"],"title":d["title"],"description":description,"privacyStatus":"private","language":channel["language"],"uploaded":False})
    manifest={"status":"rendered_test_silent" if smoke else "rendered","path":str(final),"duration":duration,"width":1080,"height":1920,"created_at":now().isoformat(),"published":False}
    write(folder/"manifest.json",manifest)
    return manifest

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def send(self,data,code=200,kind="application/json; charset=utf-8"):
        raw=data if isinstance(data,bytes) else json.dumps(data,ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type",kind); self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff"); self.send_header("X-Frame-Options","DENY")
        self.send_header("Referrer-Policy","no-referrer"); self.send_header("Content-Length",str(len(raw)))
        self.end_headers(); self.wfile.write(raw)
    def allowed(self): return self.headers.get("Host")==f"127.0.0.1:{self.server.server_port}"
    def do_GET(self):
        if not self.allowed(): return self.send({"error":"Invalid host"},403)
        if self.path=="/":
            html=(ROOT/"setup.html").read_text(encoding="utf-8").replace("__TOKEN__",self.server.csrf)
            return self.send(html.encode(),kind="text/html; charset=utf-8")
        if self.path=="/api/status": return self.send(status())
        return self.send({"error":"Not found"},404)
    def do_POST(self):
        origin=f"http://127.0.0.1:{self.server.server_port}"
        if not self.allowed() or self.headers.get("Origin")!=origin or self.headers.get("X-Setup-Token")!=self.server.csrf:
            return self.send({"error":"Local setup page required"},403)
        length=int(self.headers.get("Content-Length","0"))
        if length>16000: return self.send({"error":"Request too large"},413)
        try:
            body=json.loads(self.rfile.read(length))
            with LOCK:
                if self.path=="/api/connect":
                    value=body.get("api_key","").strip()
                    if not value: raise ValueError("API 키를 입력하세요.")
                    voices=list_voices(value); sub=subscription(value)
                    KEY_FILE.write_bytes(protect(value.encode()))
                    write(STATE/"voices.json",voices); write(STATE/"subscription.json",sub)
                elif self.path=="/api/settings":
                    c=cfg(); ids={v["voice_id"] for v in read(STATE/"voices.json",[])}
                    for channel in c["channels"]:
                        voice=body.get(channel,"")
                        if voice not in ids: raise ValueError("계정의 기존 음성을 선택하세요.")
                        c["channels"][channel]["voice_id"]=voice
                    write(CONFIG,c)
                else: return self.send({"error":"Not found"},404)
            return self.send(status())
        except (ValueError,RuntimeError) as exc: return self.send({"error":str(exc)},400)
        except Exception: return self.send({"error":"연결에 실패했습니다. 네트워크와 API 권한을 확인해 주세요."},500)

def serve(port):
    server=ThreadingHTTPServer(("127.0.0.1",port),Handler); server.csrf=secrets.token_urlsafe(32)
    emit({"setup_url":f"http://127.0.0.1:{port}","pid":os.getpid()})
    server.serve_forever()

def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="command",required=True)
    sub.add_parser("status"); sub.add_parser("collect")
    s=sub.add_parser("serve"); s.add_argument("--port",type=int,default=8765)
    for name in ("validate","render"):
        s=sub.add_parser(name); s.add_argument("job"); s.add_argument("--smoke",action="store_true")
    a=p.parse_args()
    try:
        if a.command=="status": emit(status())
        elif a.command=="collect": emit(collect())
        elif a.command=="serve": serve(a.port)
        elif a.command=="validate": emit({"valid":True,"narration_characters":len(validate(read(a.job)))})
        elif a.command=="render":
            with process_lock("render"): emit(render(a.job,a.smoke))
    except Exception as exc: emit({"error":str(exc)}); sys.exit(1)

if __name__=="__main__": main()
