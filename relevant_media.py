"""Discover directly relevant Commons photos with explicit reusable licenses.

Metadata establishes license provenance, not visual relevance. The editorial
vision review must approve the pictured subject before the renderer can use it.
"""
import hashlib,html,json,re,urllib.parse,urllib.request
import pipeline as p

def clean(value):return html.unescape(re.sub('<[^>]+>','',value or '')).strip()
def search(query,topic,channel):
    params={'action':'query','format':'json','generator':'search','gsrsearch':query,'gsrnamespace':6,'gsrlimit':12,'prop':'imageinfo','iiprop':'url|extmetadata|mime|size','iiurlwidth':1280}
    return fetch_candidates(params,topic,channel)

def validate_titles(titles):
    if (not isinstance(titles,list) or len(titles)!=2
            or any(not isinstance(t,str) or not t.startswith('File:') or not 6<=len(t)<=512
                   or any(ord(ch)<32 or ch in '|#' for ch in t) for t in titles)
            or len(set(t.replace('_',' ') for t in titles))!=2):
        raise ValueError('Exactly two distinct Commons file titles required')
    return [t.replace('_',' ') for t in titles]

def resolve_titles(titles,topic,channel):
    titles=validate_titles(titles)
    params={'action':'query','format':'json','titles':'|'.join(titles),
            'prop':'imageinfo','iiprop':'url|extmetadata|mime|size','iiurlwidth':1280}
    return fetch_candidates(params,topic,channel,set(titles))

def fetch_candidates(params,topic,channel,expected_titles=None):
    req=urllib.request.Request('https://commons.wikimedia.org/w/api.php?'+urllib.parse.urlencode(params),headers={'User-Agent':'JinnyShorts/1.0 (https://github.com/jinny84848484-beep/jinny)'})
    with urllib.request.urlopen(req,timeout=30) as r:data=json.load(r)
    found={}
    for page in data.get('query',{}).get('pages',{}).values():
        if expected_titles is not None and page.get('title') not in expected_titles:continue
        info=page.get('imageinfo',[{}])[0];meta=info.get('extmetadata',{})
        get=lambda name:clean(meta.get(name,{}).get('value',''))
        license=get('LicenseShortName');license_url=get('LicenseUrl')
        if license not in {'CC0','CC0 1.0','Public domain','CC BY 2.0','CC BY 2.5','CC BY 3.0','CC BY 4.0'}:continue
        if license.startswith('CC BY ') and not license_url:
            license_url='https://creativecommons.org/licenses/by/'+license.removeprefix('CC BY ')+'/'
        if info.get('mime') not in {'image/jpeg','image/png'}:continue
        if min(info.get('width',0),info.get('height',0))<500:continue
        # Thumbnails may use a different CDN; never broaden the approved host.
        # Fall back to the original URL only when it independently passes.
        url=next((u for u in (info.get('thumburl'),info.get('url'))
                  if isinstance(u,str) and urllib.parse.urlparse(u).scheme=='https'
                  and urllib.parse.urlparse(u).hostname=='upload.wikimedia.org'),'')
        if not url:continue
        source=urllib.parse.urlparse(info.get('descriptionurl',''))
        if source.scheme!='https' or source.hostname!='commons.wikimedia.org':continue
        key='commons-'+str(page['pageid'])+'-'+hashlib.sha256((topic+'|'+channel).encode()).hexdigest()[:8]
        capture_date=get('DateTimeOriginal')[:100]
        year=re.search(r'(?<!\d)(?:18|19|20)\d{2}(?!\d)',capture_date)
        notice=('자료사진 · ' if channel=='ojirap' else 'ARCHIVE PHOTO · ')+(year.group() if year else ('촬영일 미상' if channel=='ojirap' else 'DATE UNKNOWN'))
        found[key]={'url':url,'source':info['descriptionurl'],'path':'assets/'+key+('.png' if info['mime']=='image/png' else '.jpg'),'media_type':'image','credit':get('Artist')[:350]+' / Wikimedia Commons','license':license,'license_url':license_url,'notice':notice,'description':get('ImageDescription')[:1200],'capture_date':capture_date,'title':page['title'],'topic_key':topic,'channel':channel,'relevance_verified':False,'checked_at':p.now().isoformat()}
    return found

def candidates(jobs):
    result={}
    for job in jobs:
        if 'visual_source_titles' in job:
            current=resolve_titles(job['visual_source_titles'],job['topic_key'],job['channel'])
        else:
            queries=job.get('visual_queries',[])[:2]
            if not queries or any(not isinstance(q,str) or not 3<=len(q)<=100 for q in queries):raise ValueError('Specific visual search queries required')
            current={}
            for query in queries:
                current.update(search(query,job['topic_key'],job['channel']))
                if len(current)>=4:break
        if len(current)<2:raise RuntimeError('Not enough relevant licensed photo candidates; batch held')
        result.update(dict(list(current.items())[:4]))
    return result

def apply_review(jobs,media,selection):
    for job in jobs:
        choices=selection.get(job['id'],[])
        if len(choices)!=len(job['scenes']) or len(set(choices))<2:raise ValueError('Each story needs two reviewed relevant images')
        for scene,key in zip(job['scenes'],choices):
            if key not in media or media[key]['topic_key']!=job['topic_key'] or media[key].get('channel',None if job.get('format_version') else job['channel'])!=job['channel']:raise ValueError('Media belongs to another story')
            scene['media']=key;media[key]['relevance_verified']=True
    library=p.read(p.ROOT/'assets/media-library.json',{});library.update(media);p.write(p.ROOT/'assets/media-library.json',library)


