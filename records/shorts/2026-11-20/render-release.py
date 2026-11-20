from PIL import Image,ImageDraw,ImageFont
from pathlib import Path
import subprocess,json,math,sys
P=Path(__file__).parent; W,H,FPS=540,960,24
font='/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc'
fonts={s:ImageFont.truetype(font,s) for s in [16,19,23,28,34,42,65]}
from bisect import bisect_right
kind=sys.argv[1]
audio=P/'approved-narration-joined.mp3'
starts={'economy':[0,3.317551,9.430204,13.296327,23.562449,29.596735], 'person':[34.66449,39.026939,46.733061,49.084082,53.133061,56.450612]}[kind]
offset=starts[0]; end=34.66449 if kind=='economy' else 62.98075
starts=[x-offset for x in starts]; speech=end-offset
duration=math.ceil((speech+.5)*FPS)/FPS
scenes={
'economy':[
 ('물가 하락?','비교 날짜부터 보세요','전월과 전년, 다른 기준'),
 ('10월 생산자물가','전월 대비 −0.1%','한국은행 · 2026.11.20 발표'),
 ('같은 통계, 다른 비교','전년 동월 대비 +7.2%','전월 −0.1%와 동시에 성립'),
 ('품목별 방향도 다릅니다','농림수산 −8.3%','공산품 +0.3% · 모두 전월 대비'),
 ('생산자물가 ≠ 내 영수증','소비자가격 하락으로','곧바로 해석하지 않습니다'),
 ('오늘의 한 줄 논평','화살표 하나보다','기준 날짜부터 봅시다')],
'person':[
 ('이강인','이름을 다시 올리다','AFC 남자 국제선수상'),
 ('2026.11.19 · 방콕','국제선수상 수상','AFC 시상식 · 보도 대조'),
 ('2025 → 2026','2년 연속','같은 상을 다시 지켰습니다'),
 ('상 이름을 정확히','아시아 밖에서 뛰는','아시아 선수 대상'),
 ('다른 부문과 구분','올해의 국제선수상','올해의 선수상과 다른 부문'),
 ('오늘의 한 줄 논평','누구를 이겼나보다','다시 올린 이름에 박수')]}[kind]
out=P/(kind+'-release-visual.mp4')
cmd=['ffmpeg','-y','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s','540x960','-r',str(FPS),'-i','-','-an','-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p','-movflags','+faststart',str(out)]
p=subprocess.Popen(cmd,stdin=subprocess.PIPE)
def tx(d,s,y,size=28,color='#F7F8FA'):
 f=fonts[size]; box=d.textbbox((0,0),s,font=f); x=(W-(box[2]-box[0]))/2; assert x>=18,(s,x);d.text((x,y),s,font=f,fill=color)
for n in range(math.ceil(duration*FPS)):
 t=n/FPS;i=bisect_right(starts,t)-1;q=min(1,(t-starts[i])/1.2)
 im=Image.new('RGB',(W,H),'#111C2D');d=ImageDraw.Draw(im)
 for j in range(8):
  x=int((j*92+t*13)%700-80);d.line((x,180,x-110,680),fill='#1B2C42',width=2)
 d.rounded_rectangle((26,29,514,73),radius=16,fill='#F2C75C');tx(d,'오늘의 오지랖  /  2026.11.20',37,19,'#111C2D')
 tx(d,scenes[i][0],112,34)
 if kind=='economy':
  if i in (0,4,5):
   for k,(c,angle) in enumerate([('#53C9D3',t*1.5),('#F2C75C',t*1.5+math.pi)]):
    cx=270+int(116*math.cos(angle));cy=435+int(65*math.sin(angle));d.ellipse((cx-25,cy-25,cx+25,cy+25),fill=c)
   d.rounded_rectangle((179,372,361,500),radius=26,outline='#A0ACBC',width=3)
   tx(d,['비교 기준','','','','소비자 가격','기준 날짜'][i],418,23)
  else:
   pairs={1:[('전월 대비',-.1)],2:[('전월 대비',-.1),('전년 동월 대비',7.2)],3:[('농림수산',-8.3),('공산품',.3)]}[i]
   for k,(label,v) in enumerate(pairs):
    cx=270 if len(pairs)==1 else 148+k*244
    y=280+10*(1-q);color='#F2C75C' if v>0 else '#53C9D3'
    d.rounded_rectangle((cx-111,y,cx+111,y+235),radius=23,fill='#1C3047',outline=color,width=2)
    f=fonts[19];d.text((cx-d.textlength(label,font=f)/2,y+26),label,font=f,fill='white')
    vs=f'{v:+g}%';f=fonts[42];d.text((cx-d.textlength(vs,font=f)/2,y+92),vs,font=f,fill=color)
    dy=int((1-q)*22); yy=y+185+dy; direction=-1 if v>0 else 1
    d.line((cx,yy-16,cx,yy+16),fill=color,width=4)
    d.polygon([(cx,yy+16*direction),(cx-11,yy+3*direction),(cx+11,yy+3*direction)],fill=color)
   tx(d,'수치 카드 · 도형 크기는 비율 아님',570,16,'#A5B6C8')
 else:
  for k,x in enumerate([150,390]):
   yy=int(395+10*math.sin(t*2+k));c='#F2C75C';d.polygon([(x-55,yy-65),(x+55,yy-65),(x+39,yy+25),(x-39,yy+25)],fill=c)
   d.arc((x-80,yy-57,x+80,yy+20),0,180,fill=c,width=9);d.rectangle((x-8,yy+25,x+8,yy+77),fill=c);d.rounded_rectangle((x-48,yy+75,x+48,yy+92),radius=5,fill=c)
   d.text((x-47,yy+121),str(2025+k),font=fonts[34],fill='white')
  d.line((235,410,300,410),fill='#53C9D3',width=6);d.polygon([(315,410),(295,396),(295,424)],fill='#53C9D3')
 tx(d,scenes[i][1],725,28);tx(d,scenes[i][2],774,23,'#C1D3E6')
 tx(d,'설명용 자체 그래픽 · 실제 현장 영상 아님',835,16,'#A5B6C8')
 d.rectangle((25,929,25+490*t/duration,936),fill='#53C9D3')
 if n in [0,int(duration*FPS/2),int(duration*FPS)-10]:im.save(P/f'{kind}-qa-{n}.jpg')
 p.stdin.write(im.tobytes())
p.stdin.close();assert p.wait()==0
if audio:
 subprocess.run(['ffmpeg','-y','-v','error','-i',str(out),'-i',str(audio),'-map','0:v','-map','1:a','-c:v','copy','-af',f'atrim=start={offset}:end={end},asetpts=PTS-STARTPTS,apad','-c:a','aac','-t',str(duration),'-movflags','+faststart',str(P/(kind+'-release.mp4'))],check=True)
(P/(kind+'-release-render.json')).write_text(json.dumps({'duration':duration,'fps':FPS,'width':W,'height':H,'audio':str(audio) if audio else None,'release_banner_removed':True,'voice_source_start':offset,'voice_source_end':end,'caption_starts_relative':starts},indent=2))
