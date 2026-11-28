"""Discover directly relevant Commons photos with explicit reusable licenses.

Metadata establishes license provenance, not visual relevance. The editorial
vision review must approve the pictured subject before the renderer can use it.
"""
import hashlib,html,json,re,urllib.error,urllib.parse,urllib.request
import pipeline as p

def clean(value):return html.unescape(re.sub('<[^>]+>','',value or '')).strip()
def search(query,topic,channel):
    params={'action':'query','format':'json','generator':'search','gsrsearch':query,'gsrnamespace':6,'gsrlimit':12,'prop':'imageinfo','iiprop':'url|extmetadata|mime|size','iiurlwidth':1280}
    return fetch_candidates(params,topic,channel)

def search_related_category(query, topic, channel):
    """Bounded fallback: one Commons category and one licensed file page."""
    params={'action':'query','format':'json','list':'search','srnamespace':14,
            'srlimit':1,'srsearch':query}
    req=urllib.request.Request('https://commons.wikimedia.org/w/api.php?'+urllib.parse.urlencode(params),
                               headers={'User-Agent':'JinnyShorts/1.0 (https://github.com/jinny84848484-beep/jinny)'})
    with urllib.request.urlopen(req,timeout=30) as r:
        hits=json.load(r).get('query',{}).get('search',[])
    if not hits:return {}
    title=hits[0].get('title','')
    if not isinstance(title,str) or not title.startswith('Category:') or len(title)>300:return {}
    return fetch_candidates({'action':'query','format':'json','generator':'categorymembers',
                             'gcmtitle':title,'gcmtype':'file','gcmlimit':12,'prop':'imageinfo',
                             'iiprop':'url|extmetadata|mime|size','iiurlwidth':1280},topic,channel)


def contextual(candidate):
    # A related result is never presented as a scene of the reported event.
    if not isinstance(candidate,dict) or candidate.get('media_type')!='image':return candidate
    return {**candidate,'illustrative_only':True,'notice':'자료화면 · 실제 현장 아님',
            'disclaimer':'관련 실사 자료화면입니다. 보도된 사건의 실제 현장이나 당사자를 보여주는 사진이 아닙니다.'}


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

# Commons is already the search provider. These suffixes are redundant there.
# Retry only concise variants of the same subject, never an unrelated query.
COMMONS_TAIL = re.compile(r'\s+(?:(?:CC BY(?: [234]\.0)?|CC0|public domain)\s+)?(?:Wikimedia Commons|Commons)\s*$', re.I)


def query_variants(queries):
    originals = list(dict.fromkeys(queries))
    yield from originals
    seen = set(originals)
    for query in originals:
        simpler = COMMONS_TAIL.sub('', query).strip()
        if len(simpler) >= 3 and simpler not in seen:
            seen.add(simpler)
            yield simpler


class InsufficientCandidates(RuntimeError):
    """Search completed but could not supply two licensed candidates."""


def candidates(jobs, *, diversify=False):
    result={}
    for job in jobs:
        if 'visual_source_titles' in job:
            current=resolve_titles(job['visual_source_titles'],job['topic_key'],job['channel'])
        else:
            queries=job.get('visual_queries',[])[:2]
            if not queries or any(not isinstance(q,str) or not 3<=len(q)<=100 for q in queries):raise ValueError('Specific visual search queries required')
            current={}
            if job['channel']=='ojirap' and 'research_visual_titles' in job:
                try:
                    direct=job.get('research_direct_titles', [])
                    current.update((key,value if value.get('title') in direct else contextual(value))
                                   for key,value in resolve_titles(job['research_visual_titles'],
                                   job['topic_key'],job['channel']).items())
                except (ValueError,urllib.error.URLError,TimeoutError):
                    pass  # Advisory evidence; an unavailable exact match does not prevent normal discovery.
                if len(current)>=2:
                    result.update(current)
                    continue
            if diversify:
                # Keep the same four-image review budget while including the
                # relevant alternative, not just the first query's near-duplicates.
                topic,channel=job['topic_key'],job['channel']
                ordered=list(dict.fromkeys(queries))
                groups=[list(search(q,topic,channel).items()) for q in ordered]
                if len(ordered)==2 and channel=='ojirap':
                    direct_keys={key for key,_ in groups[0]}
                    alternate=[(key,contextual(value)) for key,value in groups[1]
                               if key not in direct_keys]
                    # When direct and related file searches are sparse, look
                    # within a category for the alternate subject only.
                    if len(direct_keys)+len(alternate)<4 and len(alternate)<2:
                        seen=direct_keys|{key for key,_ in alternate}
                        alternate.extend((key,contextual(value)) for key,value in
                                         search_related_category(ordered[1],topic,channel).items()
                                         if key not in seen)
                    groups[1]=alternate
                for row in range(max(map(len,groups),default=0)):
                    for group in groups:
                        if row<len(group):current.setdefault(*group[row])
                    if len(current)>=4:break
                if len(current)<4:
                    for query in query_variants(queries):
                        if query in queries:continue
                        for key,value in search(query,job['topic_key'],job['channel']).items():
                            current.setdefault(key,contextual(value) if job['channel']=='ojirap' and
                                               len(queries)==2 and query==COMMONS_TAIL.sub('',queries[1]).strip() else value)
                        if len(current)>=4:break
            else:
                for query in query_variants(queries):
                    for key,value in search(query,job['topic_key'],job['channel']).items():
                        current.setdefault(key,value)
                    if len(current)>=4:break
        if len(current)<2:raise InsufficientCandidates('Not enough relevant licensed photo candidates; batch held')
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


