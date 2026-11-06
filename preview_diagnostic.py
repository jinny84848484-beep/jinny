"""No network, credentials, source text, TTS or upload. Synthetic renderer probe."""
import json, os, platform, subprocess, tempfile
from pathlib import Path
import PIL
from PIL import Image
os.environ.setdefault('SHORTS_STATE_DIR', tempfile.mkdtemp(prefix='preview-state-'))
import media_renderer as renderer
stage = 'start'
try:
    print(json.dumps({'python':platform.python_version(),'pillow':PIL.__version__}),flush=True)
    stage='config'
    channel=renderer.p.cfg()['channels']['ojirap']
    root=Path(tempfile.mkdtemp(prefix='preview-probe-'))
    stage='font'
    renderer.font(32)
    stage='overlay'
    renderer.overlay({},channel,0,5,{'notice':'자료를 바탕으로 구성한 그래픽'},root/'overlay.png')
    stage='captions'
    narration='이것은 영상 표시를 확인하는 시험 문장입니다.'
    alignment={'characters':list(narration),'character_start_times_seconds':[n*.1 for n in range(len(narration))],'character_end_times_seconds':[(n+1)*.1 for n in range(len(narration))]}
    cue=renderer.captions(narration,alignment,True)[0][2]
    (root/'review.srt').write_text('1\n00:00:00,000 --> 00:00:02,000\n'+cue+'\n',encoding='utf-8')
    Image.new('RGB',(1080,1380),'#17212c').save(root/'graphic.png')
    stage='ffmpeg'
    style='FontName=Noto Sans CJK KR,FontSize=17,Bold=-1,PrimaryColour=&HFFFFFF,OutlineColour=&H111111,BorderStyle=3,Outline=1,Shadow=0,Alignment=2,MarginV=42,MarginL=18,MarginR=32'
    filt='[1:v]split=2[bg][fg];[bg]scale=1080:1380:force_original_aspect_ratio=increase,crop=1080:1380,gblur=sigma=35,eq=brightness=-0.25[back];[fg]scale=1080:1380:force_original_aspect_ratio=decrease[front];[back][front]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30[v];[v]pad=1080:1920:0:185:black[b];[b][0:v]overlay=0:0:format=auto,format=yuv420p[composed];[composed]subtitles=review.srt:force_style=\''+style+'\'[out]'
    subprocess.run(['ffmpeg','-v','error','-y','-loop','1','-i','overlay.png','-loop','1','-i','graphic.png','-filter_complex',filt,'-map','[out]','-frames:v','1','frame.png'],cwd=root,check=True)
    stage='frame'
    with Image.open(root/'frame.png') as im: assert im.size==(1080,1920)
    print('SYNTHETIC_PREVIEW_PASS',flush=True)
except Exception as exc:
    allowed={'ValueError','RuntimeError','KeyError','TypeError','OSError','FileNotFoundError','CalledProcessError','IndexError'}
    print(json.dumps({'stage':stage,'exception':type(exc).__name__ if type(exc).__name__ in allowed else 'other'}),flush=True)
    raise SystemExit(1)
