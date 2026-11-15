"""Offline scheduling primitives. No OAuth, network, upload, journal writes or CLI.
Future transport must CAS plan.reserved_ledger before insert, then persist receipt.
"""
import copy,datetime as dt,re,json
import corgi_runner as legacy
KST=legacy.KST
MODE='youtube_scheduled_v1'
LEGACY='legacy_public_v1'
LEAD=dt.timedelta(hours=2)
HORIZON=dt.timedelta(days=14)
def instant(value):
 if not isinstance(value,str):raise ValueError('Timestamp missing')
 t=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
 if t.tzinfo is None:raise ValueError('Timezone required')
 return t.astimezone(dt.timezone.utc)
def target(day):
 d=dt.date.fromisoformat(day)
 return dt.datetime.combine(d,dt.time(19),KST).astimezone(dt.timezone.utc)
def mode(ledger):
 value=ledger.get('corgi_delivery',{}).get('mode',LEGACY)
 if value not in (LEGACY,MODE):raise ValueError('Unknown delivery mode')
 return value
def require_legacy(ledger):
 if mode(ledger)!=LEGACY:raise ValueError('Direct-public publisher disabled by single delivery mode')
def scheduling_quality(item):
 qa=json.loads(legacy.local_file(item['qa']['path']).read_text())
 rights=json.loads(legacy.local_file(item['rights']['path']).read_text())
 for key in ('actual_motion','original_corgi_identity','short_tail','not_slideshow','resolution_1080p'):
  if qa.get(key) is not True:raise ValueError('Specific corgi quality evidence missing: '+key)
 if rights.get('music_license_verified') is not True and rights.get('music_absent') is not True:raise ValueError('Music rights unverified')
def plan(day,ledger,now):
 if now.tzinfo is None:raise ValueError('Aware current time required')
 if mode(ledger)!=MODE:raise ValueError('Scheduled mode not explicitly activated')
 when=target(day);delta=when-now.astimezone(dt.timezone.utc)
 if not LEAD<=delta<=HORIZON:raise ValueError('Outside safe future scheduling window; never fall back to public')
 item,path,fp=legacy.approved_item(day) # exact asset, QA/rights, metadata + owner approval
 scheduling_quality(item)
 records=ledger.get('corgi_publications',{})
 if any(x.get('status') not in {'complete','scheduled_verified'} for x in records.values()):raise ValueError('Uncertain prior submission needs reconciliation')
 if item['id'] in records or any(x.get('date_kst')==day or x.get('asset_sha256')==item['sha256'] for x in records.values()):raise ValueError('Duplicate item/date/asset')
 meta=item['metadata'];at=when.isoformat().replace('+00:00','Z')
 body={'snippet':{'title':meta['title'],'description':meta['description'],'categoryId':'15'},'status':{'privacyStatus':'private','publishAt':at,'selfDeclaredMadeForKids':meta['made_for_kids'],'containsSyntheticMedia':meta['contains_synthetic_media']}}
 out=copy.deepcopy(ledger);record={'status':'schedule_upload_submitted','date_kst':day,'asset_sha256':item['sha256'],'manifest_sha256':fp,'channel_id':legacy.CHANNEL,'publish_at':at,'expected_metadata':copy.deepcopy(meta),'mode':MODE,'submitted_at':now.isoformat()}
 out.setdefault('corgi_publications',{})[item['id']]=record
 return {'expected_ledger_hash':legacy.digest(ledger),'reserved_ledger':out,'item_id':item['id'],'asset_path':str(path),'upload_body':body,'may_execute':False}
def receipt(record,video_id):
 if record.get('status')!='schedule_upload_submitted' or not re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id):raise ValueError('Invalid upload receipt')
 out=copy.deepcopy(record);out.update(status='schedule_verification_pending',video_id=video_id);return out
 # Receipt persistence must never be blocked by a time cutoff.
def verify(record,response,now):
 if now.tzinfo is None:raise ValueError('Aware current time required')
 if record.get('status') not in {'schedule_verification_pending','scheduled_verified'}:raise ValueError('No verifiable scheduled receipt')
 if record.get('mode')!=MODE or record.get('channel_id')!=legacy.CHANNEL or instant(record.get('publish_at'))!=target(record.get('date_kst')):raise ValueError('Reservation identity/time mismatch')
 items=response.get('items',[])
 if len(items)!=1:raise ValueError('Video response missing/ambiguous')
 v=items[0];s=v.get('status',{})
 if v.get('id')!=record.get('video_id') or v.get('snippet',{}).get('channelId')!=legacy.CHANNEL:raise ValueError('Wrong video/channel')
 expected=record.get('expected_metadata',{})
 if not expected or any(v.get('snippet',{}).get(k)!=expected[k] for k in ('title','description')) or s.get('selfDeclaredMadeForKids') is not expected['made_for_kids'] or s.get('containsSyntheticMedia') is not expected['contains_synthetic_media']:
  raise ValueError('Approved metadata/disclosures mismatch')
 if s.get('uploadStatus')!='processed' or v.get('processingDetails',{}).get('processingStatus')!='succeeded':raise ValueError('Processing not verified complete')
 when=instant(record['publish_at']);out=copy.deepcopy(record)
 if now.astimezone(dt.timezone.utc)<when:
  if s.get('privacyStatus')!='private' or instant(s.get('publishAt'))!=when:raise ValueError('Scheduled privacy/time mismatch')
  out.update(status='scheduled_verified',scheduled_verified_at=now.isoformat())
 else:
  if s.get('privacyStatus')!='public':raise ValueError('Publication pending or failed; never reupload')
  out.update(status='complete',verified_at=now.isoformat(),url='https://www.youtube.com/shorts/'+v['id'])
 return out
