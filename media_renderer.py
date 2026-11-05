"""Render verified Shorts with credited footage, original narration and timed captions."""
from __future__ import annotations
import argparse, hashlib, json, os, re, subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import pipeline as p

W,H=1080,1920

def font(size,bold=True):
    candidates=[os.environ.get('SHORTS_FONT_BOLD' if bold else 'SHORTS_FONT',''),
      'C:/Windows/Fonts/'+('malgunbd.ttf' if bold else 'malgun.ttf'),
      '/usr/share/fonts/opentype/noto/'+('NotoSansCJK-Bold.ttc' if bold else 'NotoSansCJK-Regular.ttc')]
    for name in candidates:
        if name and Path(name).exists(): return ImageFont.truetype(name,size)
    raise RuntimeError('Install a Korean-capable font or set SHORTS_FONT')

def lines(draw,text,f,width):
    result=[]
    for paragraph in text.split('\n'):
        line=''
        for word in paragraph.split():
            candidate=(line+' '+word).strip()
            if line and draw.textlength(candidate,font=f)>width:
                result.append(line);line=word
            else:line=candidate
        if line:result.append(line)
    return result

def text(draw,value,box,color,size=90,bold=True,center=False):
    x,y,w,h=box
    for s in range(size,17,-2):
        f=font(s,bold); rows=lines(draw,value,f,w)
        if len(rows)*s*1.3<=h and all(draw.textlength(r,font=f)<=w for r in rows):break
    else:raise ValueError('Text overflows: '+value[:40])
    for row in rows:
        left=x+(w-draw.textlength(row,font=f))/2 if center else x
        draw.text((left,y),row,font=f,fill=color);y+=s*1.3

def overlay(scene,ch,i,total,media,path):
    ko=ch['language']=='ko';bg=ch['background'];accent=ch['accent']
    fg='#FAFAF7' if ko else '#191919';muted='#BEBEBE' if ko else '#655E54'
    im=Image.new('RGBA',(W,H),bg);draw=ImageDraw.Draw(im)
    draw.rectangle((0,0,W,18),fill=accent)
    brand=ch['name'] if ko else 'A NOT-SO-POLITE KOREAN'
    text(draw,brand,(64,100,900,60),fg,32)
    if not media:raise ValueError('Directly relevant visual material required')
    # The picture is dominant. Only synchronized captions carry editorial text.
    # No duplicated headline, summary paragraph or fact/opinion badges.
    draw.rectangle((0,185,W,1564),fill=(0,0,0,0))
    draw.rectangle((0,185,W,257),fill=(0,0,0,170))
    text(draw,media['notice'],(42,205,965,45),'#FFFFFF',23,False)
    text(draw,'@'+ch['handle'],(64,1750,860,65),muted,27,False)
    im.save(path)

def captions(text_value,alignment,ko):
    chars=alignment['characters'];starts=alignment['character_start_times_seconds'];ends=alignment['character_end_times_seconds']
    if ''.join(chars)!=text_value or len(chars)!=len(starts) or len(chars)!=len(ends):raise ValueError('Alignment mismatch')
    # Split only at whole-word boundaries, punctuation or paragraph breaks.
    tokens=list(re.finditer(r'\S+',text_value)); cues=[];group=[];limit=12 if ko else 34
    def flush():
        if not group:return
        a,b=group[0].start(),group[-1].end()-1
        cues.append((starts[a],ends[b],text_value[a:b+1].replace('\n',' ')))
        group.clear()
    for token in tokens:
        if group and (token.end()-group[0].start()>limit or '\n' in text_value[group[-1].end():token.start()]):flush()
        group.append(token)
        if re.search(r'[.!?。]$',token.group()):flush()
    flush()
    first=re.search(r'[.!?。](?:\s|$)',text_value)
    if first and first.start()+1<=(34 if ko else 64):
        boundary=ends[first.start()]
        consumed=next((i+1 for i,cue in enumerate(cues) if cue[1]>=boundary),0)
        if consumed:cues=[(0,boundary,text_value[:first.start()+1])]+cues[consumed:]
    if cues:cues[0]=(0,cues[0][1],cues[0][2])
    return cues

def run_ff(args,cwd):
    result=subprocess.run([p.ffmpeg(),'-hide_banner','-loglevel','error','-y',*args],cwd=cwd,capture_output=True,text=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise RuntimeError(result.stderr[-2000:])

def render(job_path):
    d=p.read(job_path);spoken=p.validate(d);ch=p.cfg()['channels'][d['channel']]
    day=d['id'][:10];folder=p.ROOT/'runs'/day/d['channel']/d['id'];folder.mkdir(parents=True,exist_ok=True)
    library=p.read(p.ROOT/'assets/media-library.json',{})
    alignment=p.synthesize(d,folder)['alignment'];duration=alignment['character_end_times_seconds'][-1]+0.35
    if not 15<=duration<=75:raise ValueError('Unexpected narration length')
    cues=captions(spoken,alignment,ch['language']=='ko')
    (folder/'captions.srt').write_text('\n\n'.join(f'{i+1}\n{p.srt_time(a)} --> {p.srt_time(b)}\n{t}' for i,(a,b,t) in enumerate(cues))+'\n',encoding='utf-8')
    offsets=[];offset=0
    for scene in d['scenes']:offsets.append(offset);offset+=len(scene['narration'].strip())+1
    boundaries=[alignment['character_start_times_seconds'][x] for x in offsets]+[duration]
    boundaries[0]=0
    credits={};manifest_parts=[]
    for i,scene in enumerate(d['scenes']):
        media=library.get(scene.get('media'));part=folder/f'part-{i:02}.mp4'
        art=folder/f'overlay-{i:02}.png';overlay(scene,ch,i,len(d['scenes']),media,art)
        seconds=boundaries[i+1]-boundaries[i]
        args=['-loop','1','-framerate','30','-i',art.name]
        if media:
            path=(p.ROOT/media['path']).resolve()
            if not path.is_relative_to(p.ROOT.resolve()) or not path.is_file():raise ValueError('Missing or unsafe media path')
            credits[scene['media']]=media
            if media.get('media_type')=='image':args+=['-loop','1','-framerate','30','-i',str(path)]
            else:args+=['-stream_loop','-1','-i',str(path)]
            args+=['-filter_complex',
              '[1:v]split=2[bg][fg];[bg]scale=1080:1380:force_original_aspect_ratio=increase,crop=1080:1380,gblur=sigma=35,eq=brightness=-0.25[back];[fg]scale=1080:1380:force_original_aspect_ratio=decrease[front];[back][front]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30[v];[v]pad=1080:1920:0:185:black[b];[b][0:v]overlay=0:0:format=auto,format=yuv420p[out]', '-map','[out]']
        else:args+=['-vf','format=yuv420p']
        args+=['-t',str(seconds),'-an','-r','30','-c:v','libx264','-preset','veryfast','-crf','21','-threads','4',part.name]
        run_ff(args,folder);manifest_parts.append("file '"+part.name+"'")
        print(d['id'],f'scene {i+1}/{len(d["scenes"])}',flush=True)
    (folder/'parts.txt').write_text('\n'.join(manifest_parts),encoding='utf-8')
    family='Malgun Gothic' if os.name=='nt' else 'Noto Sans CJK KR'
    style=f'FontName={family},FontSize=17,Bold=-1,PrimaryColour=&HFFFFFF,OutlineColour=&H111111,BorderStyle=3,Outline=1,Shadow=0,Alignment=2,MarginV=42,MarginL=18,MarginR=32'
    final=folder/'short.mp4'
    run_ff(['-f','concat','-safe','0','-i','parts.txt','-i','narration.mp3','-vf',f"subtitles=captions.srt:force_style='{style}'",'-af','loudnorm=I=-16:TP=-1.5:LRA=11','-t',str(duration),'-c:v','libx264','-preset','veryfast','-crf','21','-threads','4','-pix_fmt','yuv420p','-c:a','aac','-b:a','160k','-movflags','+faststart',final.name],folder)
    desc=d.get('description','')+'\n\nSources:\n'+'\n'.join(s['url'] for s in d['sources'])
    desc+='\n\nVisual credits (resized and captioned):\n'+'\n'.join(m['credit']+' — '+m['source']+'\n'+m.get('license','')+' '+m.get('license_url','')+'\n'+m.get('disclaimer','') for m in credits.values())
    desc+='\n\n#Shorts'
    p.write(folder/'script.json',d)
    p.write(folder/'upload-metadata.json',{'channel_id':d['channel_id'],'title':d['title'],'description':desc,'privacyStatus':'public','language':ch['language'],'category':d['category'],'uploaded':False})
    result={'status':'rendered','path':str(final),'duration':duration,'width':W,'height':H,'created_at':p.now().isoformat(),'published':False,'sha256':hashlib.sha256(final.read_bytes()).hexdigest(),'footage':list(credits)}
    p.write(folder/'manifest.json',result)
    for t,label in [(1,'opening'),(duration/2,'middle'),(duration-3,'closing')]:
        run_ff(['-ss',str(t),'-i',final.name,'-frames:v','1','-vf','scale=405:720',f'preview-{label}.jpg'],folder)
    print(json.dumps(result),flush=True);return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('jobs',nargs='+');args=parser.parse_args()
    for job in args.jobs:render(Path(job))

