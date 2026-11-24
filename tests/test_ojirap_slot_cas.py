from copy import deepcopy
import unittest
from dataclasses import replace
import ojirap_slot_cas as s

DAY = "2026-11-20"
A, B = [s.Item(f"{DAY}-job-{i}", f"topic-{i}", str(i)*64) for i in [1,2]]
OPEN = lambda d: d == DAY

class Memory:
    def __init__(self):
        self.ledger = {"days":{}, "publications":[], "untouched":5}
        self.rev = object()
        self.writes = 0

    def read(self): return deepcopy(self.ledger),self.rev

    def write(self, new, expected):
        if expected != self.rev: raise s.KnownConflict()
        self.ledger = new
        self.rev = object()
        self.writes += 1
        return self.rev

class Slots(unittest.TestCase):
    def setUp(self): self.m=Memory()
    def claim(self, *items, kind="auto", owner="run1", write=None, day=DAY, still_open=OPEN):
        return s.claim(self.m.read,write or self.m.write,day=day,owner=owner,
                       kind=kind,items=items,still_open=still_open)
    def move(self, lease, slot, expected, phase, **kw):
        return s.advance(self.m.read,self.m.write,lease,slot,expected=expected,phase=phase,still_open=OPEN,**kw)

    def test_two_auto_atomically_and_manual_blocked(self):
        lease=self.claim(A,B);self.assertEqual(lease.slots,("1","2"))
        self.assertEqual(self.m.writes,1)
        self.assertEqual(self.m.ledger["untouched"],5)
        with self.assertRaises(s.SlotHold): self.claim(A,kind="manual",owner="user")

    def test_manual_one_blocks_auto_but_can_claim_second_manual(self):
        self.assertEqual(self.claim(A,kind="manual",owner="a").slots,("1",))
        with self.assertRaises(s.SlotHold): self.claim(A,B)
        self.assertEqual(self.claim(B,kind="manual",owner="b").slots,("2",))

    def test_actual_interleaving_stale_cas_with_manual_winner(self):
        hits=[]
        def interleave(new,rev):
            hits.append(rev)
            self.claim(B,kind="manual",owner="manual")
            return self.m.write(new,rev)
        with self.assertRaises(s.SlotHold): self.claim(A,B,write=interleave)
        self.assertEqual(len(hits),1)
        self.assertEqual(self.m.writes,1)
        self.assertEqual(self.m.ledger[s.ROOT][DAY]["1"]["kind"],"manual")

    def test_typed_conflict_only_is_safe_to_retry(self):
        hits=[]
        def changed_snapshot(new,rev):
            hits.append(rev)
            if len(hits)==1:
                fresh,fresh_rev=self.m.read(); fresh["untouched"]=6
                self.m.write(fresh,fresh_rev)
            return self.m.write(new,rev)
        self.claim(A,B,write=changed_snapshot)
        self.assertEqual(len(hits),2)
        self.assertEqual(self.m.ledger["untouched"],6)

    def test_ambiguous_commit_never_retried_and_known_lease_load(self):
        count=[]
        def ambiguous(new,rev):
            count.append(rev);self.m.write(new,rev);raise TimeoutError
        with self.assertRaises(TimeoutError): self.claim(A,B,write=ambiguous)
        self.assertEqual(len(count),1)
        with self.assertRaises(s.SlotHold): self.claim(A,B)
        lease,rows=s.load(self.m.read,day=DAY,owner="run1")
        self.assertEqual(lease.slots,("1","2"))
        self.assertTrue(all(r["phase"]=="claimed" for r in rows.values()))

    def test_intent_replay_conflict_and_wrong_slot(self):
        lease=self.claim(A,B)
        newer=self.move(lease,"1","claimed","upload_requested")
        for l in (lease,newer,replace(newer,slots=("1",))):
            with self.assertRaises(s.SlotHold):
                self.move(l,"1","claimed","upload_requested")
        with self.assertRaises(s.SlotHold): self.move(newer,"2","claimed","publish_requested")

    def test_auto_schedule_needs_two_and_partial_never_restarts(self):
        lease=self.claim(A,B)
        lease=self.move(lease,"1","claimed","upload_requested")
        lease=self.move(lease,"1","upload_requested","private_id",video_id="a"*11)
        with self.assertRaises(s.SlotHold): self.move(lease,"1","private_id","schedule_requested")
        lease=self.move(lease,"2","claimed","upload_requested")
        with self.assertRaises(s.SlotHold):
            self.move(lease,"2","upload_requested","private_id",video_id="a"*11)
        lease=self.move(lease,"2","upload_requested","private_id",video_id="b"*11)
        with self.assertRaises(s.SlotHold):
            self.move(replace(lease,slots=("1",)),"1","private_id","schedule_requested")
        lease=self.move(lease,"1","private_id","schedule_requested")
        with self.assertRaises(s.SlotHold): self.move(lease,"2","private_id","schedule_requested")
        lease=self.move(lease,"1","schedule_requested","schedule_verified")
        self.move(lease,"2","private_id","schedule_requested")

    def test_existing_day_and_unmanaged_publication_block_pre_provider(self):
        self.m.ledger["days"][DAY]={"status":"operator_final_qa_pending"}
        with self.assertRaises(s.SlotHold): self.claim(A,B)
        self.m.ledger["days"].clear()
        self.m.ledger["publications"].append({"channel":"ojirap","at":"2026-11-19T17:00:00Z"})
        with self.assertRaises(s.SlotHold): self.claim(A,B)
        self.assertEqual(self.m.writes,0)

    def test_bad_dates_bad_hashes_and_preserve_extras(self):
        for day,item in (("2026-11-21",A),("2026-13-20",A),(DAY,replace(A,media="bad"))):
            with self.assertRaises(s.SlotHold): self.claim(item,kind="manual",day=day)
        self.m.ledger["additional_batches"]={"extra":{"keep":True}}
        self.m.ledger["publications"]=[{"channel":"ojirap","day":DAY,"slot_kind":"extra"}]
        self.claim(A,B)
        self.assertEqual(self.m.ledger["additional_batches"],{"extra":{"keep":True}})

    def test_scope_corruption_or_cutoff_never_writes_intent(self):
        lease=self.claim(A,B)
        with self.assertRaises(s.SlotHold):
            s.advance(self.m.read,self.m.write,lease,"1",expected="claimed",phase="upload_requested",still_open=lambda d:False)
        self.m.ledger[s.ROOT][DAY]["1"]["channel_id"]="other"
        with self.assertRaises(s.SlotHold): self.move(lease,"1","claimed","upload_requested")
        self.assertEqual(self.m.writes,1)

    def test_new_unmatched_publication_blocks_fresh_intent(self):
        lease=self.claim(A,B)
        self.m.ledger["publications"]=[{"channel":"ojirap","day":DAY,"video_id":"z"*11}]
        with self.assertRaises(s.SlotHold): self.move(lease,"1","claimed","upload_requested")
