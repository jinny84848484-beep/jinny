"""Pure offline candidate; importing this module has no external effects."""
from datetime import datetime, date, timezone, timedelta
import re

LEGACY_STATES = frozenset({'draft_validation_failed', 'research_completed_unusable', 'editorial_review_failed'})
RESEARCH_STATE = 'research_validation_failed'


def assert_staged_start(record, day, today):
    """Validate without mutating. Does not authorize execution or release a hold."""
    def reject():
        raise RuntimeError('Staged start requires reconciled failed batch; no replay')

    try:
        if date.fromisoformat(day).isoformat() != day or day != today:
            reject()
    except (ValueError, TypeError):
        reject()
    if not isinstance(record, dict):
        reject()
    status = record.get('status')
    if status not in LEGACY_STATES | {RESEARCH_STATE} or record.get('retry_hold') is not True:
        reject()
    if record.get('jobs') or record.get('staging'):
        reject()
    # Any recorded publication-side state needs explicit reconciliation, even
    # when a later writer has changed the top-level status back to a failure.
    publication_keys = {'active_job', 'upload', 'uploads', 'upload_result', 'upload_results',
                        'video_id', 'video_ids', 'published_at', 'rendered', 'tts_submitted',
                        'upload_submitted', 'staging_envelope_blob'}
    if publication_keys.intersection(record):
        reject()
    checkpoints = record.get('checkpoints', {})
    if not isinstance(checkpoints, dict):
        reject()
    if any(not isinstance(cp, dict) or cp.get('status') != 'completed'
           for cp in checkpoints.values()):
        reject()
    if status != RESEARCH_STATE:
        return  # Retain legacy terminal cases, with stricter uncertainty checks.

    # This narrow recovery is for a completed research-only attempt. Never
    # carry draft/review checkpoints into a changed externally prepared batch.
    if set(checkpoints) not in ({'research'}, {'research', 'research_repair'}):
        reject()
    forbidden = {'media', 'review', 'review_input_hash', 'editorial_diagnostic', 'staging_history'}
    if forbidden.intersection(record):
        reject()
    active = 'research_repair' if 'research_repair' in checkpoints else 'research'
    if record.get('active_stage') != active:
        reject()

    def same_day(value):
        try:
            stamp = datetime.fromisoformat(value)
            return (stamp.utcoffset() is not None
                    and stamp.astimezone(timezone(timedelta(hours=9))).date().isoformat() == day)
        except (ValueError, TypeError):
            return False

    for cp in checkpoints.values():
        if (cp.get('usable') is not True or type(cp.get('attempts')) is not int
                or cp['attempts'] != 1
                or any(not isinstance(cp.get(k), str) or not re.fullmatch('[0-9a-f]{64}', cp[k])
                       for k in ('request_hash', 'sha256'))
                or not same_day(cp.get('at')) or not same_day(cp.get('completed_at'))):
            reject()
    failures = record.get('research_failures')
    if not isinstance(failures, list) or not failures or not isinstance(failures[-1], dict):
        reject()
    failure = failures[-1]
    if (failure.get('stage') != 'research_manifest' or not same_day(failure.get('at'))
            or failure.get('repair_attempted') is not ('research_repair' in checkpoints)
            or not isinstance(failure.get('reason'), str)
            or not re.fullmatch('research_manifest_[a-z_]+', failure['reason'])):
        reject()
