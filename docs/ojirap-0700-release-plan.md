# Ojirap 07:00 KST release guard (draft, not enabled)

Treat 07:00 Asia/Seoul as the release time, not as the GitHub start time.
The proposed preparation window is 04:45–06:40 KST. Once integrated,
05:07 and 05:37 KST are candidate primary and backup starts. Do not change
the live cron in this patch. Neither cron nor this guard guarantees that
YouTube will finish processing or publish at the exact second.

Proposed activation path:
1. Integrate two verified, rights-cleared rendered Korean jobs, and one
   persistent atomic date+channel+slot reservation used by the managed
   manual and automatic paths. A publication unknown state holds the slot.
2. Verify that the owner-approved operating credentials can update exact
   private video IDs and read back schedule/processing status. The existing
   CLI requests upload and read-only scopes; do not assume video update is
   authorized. Do not change the OAuth scope or connection from this patch.
3. Private-stage both files. Pin the exact two media hashes and IDs in the
   journal. Require processed, never-public videos in the correct channel.
4. Arm 07:00 only if still safely before cutoff, with an intent marker
   before each external write. Do not set a past publishAt; YouTube can
   publish immediately. Do not retry an uncertain upload or arm call.
5. If one booking succeeds and the other fails or is uncertain, reconcile
   immediately using the exact existing IDs. Do not label the day complete
   or submit another upload. Two API writes are not atomic. Independently
   verify public status after the scheduled time.
6. Only switch production cron after verifying the live authorized adapter
   and managed manual-slot isolation. If operators bypass the gate by
   posting directly in Studio, this module cannot lock them out.

This patch is a network-free, adapter-driven guard and tests, not a
deployment, active scheduler, live OAuth check, or publish command.

Official reference:
- https://developers.google.com/youtube/v3/docs/videos#status.publishAt
- https://developers.google.com/youtube/v3/docs/videos/update
