"""Pure pinned correction proposal. Never writes journal, reviews or publishes."""
import copy,hashlib,json
INPUT_HASH='b4bbab01f268f8e8f3f8ea214ce7d8f8ece08a506dcddb572bfa80886263061d'
JOB='2026-11-15-samsung-intelligent-eyewear'
def fingerprint(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def corrected_request(saved):
 if fingerprint(saved)!=INPUT_HASH:raise ValueError('Exact failed review input required')
 out=copy.deepcopy(saved);jobs=out['jobs']
 if len(jobs)!=2 or jobs[0]['id']!=JOB or jobs[0]['channel']!='ojirap':raise ValueError('Job scope mismatch')
 scene=jobs[0]['scenes'][2]
 if scene['headline']!='판매 시작은 십이월 십일' or scene['narration'].count('십이월 십일')!=1:raise ValueError('Unexpected date wording')
 scene['headline']='2026년 12월 10일 출시 예정'
 scene['body']='한국·미국 출시 예정일. 사전판매와 구분.'
 scene['narration']=scene['narration'].replace('십이월 십일','이천이십육년 십이월 열흘')
 return out
