"""Discover directly relevant Commons photos with explicit reusable licenses.

Metadata establishes license provenance, not visual relevance. The editorial
vision review must approve the pictured subject before the renderer can use it.
"""
import hashlib,html,json,re,urllib.parse,urllib.request
import pipeline as p

def clean(value):return html.unescape(re.sub('<[^>]+>','',value or '')).strip()
def search(query,topic,channel):
    params={'action':'query','format':'json','generator':'search','gsrsearch':query,'gsrnamespace':6,'gsrlimit':4,'prop':'imageinfo','iiprop':'url|extmetadata|mime|size','iiurlwidth':1280}
    req=urllib.request.Request('https://commons.wikimedia.org/w/api.php?'+urllib.parse.urlencode(params),headers={'User-Agent':'JinnyShorts/1.0 (https://github.com/jinny84848484-beep/jinny)'})
    with urllib.request.urlopen(req,timeout=30) as r:data=json.load(r)
    found={}
    for page in data.get('query',{}).get('pages',{}).values():
        info=page.get('imageinfo',[{}])[0];meta=info.get('extmetadata',{})
        get=lambda name:clean(meta.get(name,{}).get('value',''))
        license=get('LicenseShortName');license_url=get('LicenseUrl')
        if license not in {'CC0','CC0 1.0','Public domain','CC BY 2.0','CC BY 2.5','CC BY 3.0','CC BY 4.0'}:continue
        if info.get('mime') not in {'image/jpeg','image/png'}:continue
        if min(info.get('width',0),info.get('height',0))<500:continue
        url=info.get('thumburl') or info.get('url','')
        if urllib.parse.urlparse(url).hostname!='upload.wikimedia.org':continue
        key='commons-'+str(page['pageid'])+'-'+hashlib.sha256(topic.encode()).hexdigest()[:8]
        found[key]={'url':url,'source':info['descriptionurl'],'path':'assets/'+key+('.png' if info['mime']=='image/png' else '.jpg'),'media_type':'image','credit':get('Artist')[:350]+' / Wikimedia Commons','license':license,'license_url':license_url,'notice':'자료사진' if channel=='ojirap' else 'ARCHIVE PHOTO','description':get('ImageDescription')[:1200],'capture_date':get('DateTimeOriginal')[:100],'title':page['title'],'topic_key':topic,'relevance_verified':False,'checked_at':p.now().isoformat()}
    return found

def candidates(jobs):
    result={}
    for job in jobs:
        queries=job.get('visual_queries',[])[:2]
        if not queries or any(not isinstance(q,str) or not 3<=len(q)<=100 for q in queries):raise ValueError('Specific visual search queries required')
        current={}
        for query in queries:
            current.update(search(query,job['topic_key'],job['channel']))
            if len(current)>=2:break
        if len(current)<2:raise RuntimeError('Not enough relevant licensed photo candidates; batch held')
        result.update(dict(list(current.items())[:2]))
    return result

def apply_review(jobs,media,selection):
    for job in jobs:
        choices=selection.get(job['id'],[])
        if len(choices)!=len(job['scenes']) or len(set(choices))<2:raise ValueError('Each story needs two reviewed relevant images')
        for scene,key in zip(job['scenes'],choices):
            if key not in media or media[key]['topic_key']!=job['topic_key']:raise ValueError('Media belongs to another story')
            scene['media']=key;media[key]['relevance_verified']=True
    library=p.read(p.ROOT/'assets/media-library.json',{});library.update(media);p.write(p.ROOT/'assets/media-library.json',library)
