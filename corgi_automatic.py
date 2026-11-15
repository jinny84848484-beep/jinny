"""Single scheduled authority. Select immutable queue dates, never regenerate/retry."""
import json,os
import corgi_runner as legacy
import corgi_schedule_plan as p
import corgi_schedule_adapter as a

def tick(clock=a.now):
 a.assert_context()
 mode=os.environ.get('CORGI_MODE')
 if mode not in {'auto_schedule','auto_reconcile'}:raise ValueError('Automatic mode required')
 ledger,_=a.c.journal_get()
 if p.mode(ledger)!=p.MODE:return {'state':'inactive','scheduled':0}
 records=ledger.get('corgi_publications',{})
 # No known ID means uncertain insert; never synthesize a receipt or resubmit.
 if any(r.get('status') not in {'complete','scheduled_verified','schedule_verification_pending'} for r in records.values()):return {'state':'uncertain_submission_hold','scheduled':0}
 for item_id,record in sorted(records.items()):
  if record.get('status')=='schedule_verification_pending' or (record.get('status')=='scheduled_verified' and p.instant(record['publish_at'])<=clock()):
   # Query the exact persisted ID. Failure preserves receipt and halts new inserts.
   a.reconcile(item_id,clock)
 if mode=='auto_reconcile':return {'state':'reconciled','scheduled':0}
 queue=json.loads((legacy.ROOT/'corgi/queue.json').read_text())
 if queue.get('schema_version')!=1 or not isinstance(queue.get('items'),list):raise ValueError('Invalid queue')
 days=sorted({item.get('publish_date_kst','') for item in queue['items']})
 total=0;blocked=0;late=0
 for day in days:
  # Re-read after each successful reservation; reuse neither stale SHA nor day.
  ledger,_=a.c.journal_get()
  if any(r.get('date_kst')==day for r in ledger.get('corgi_publications',{}).values()):continue
  delta=p.target(day)-clock()
  if delta<p.LEAD:late+=1;continue # Never roll overdue items to tomorrow.
  if delta>p.HORIZON:continue
  try:p.plan(day,ledger,clock())
  except (ValueError,FileNotFoundError,KeyError):blocked+=1;continue
  a.schedule(day,clock) # repeats preflight and CAS; uncertain errors terminate tick
  total+=1
  if total>=14:break
 return {'state':'checked','scheduled':total,'unapproved_or_invalid':blocked,'past_or_too_late':late}
