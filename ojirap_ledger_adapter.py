"""GitHub journal_get/journal_put CAS bridge; inert until explicitly wired."""
import re

from ojirap_slot_cas import KnownConflict, SlotHold

SHA = re.compile(r"[0-9a-f]{40}\Z")


class JournalWriteUnconfirmed(SlotHold):
    """A PUT was attempted; read the same journal to reconcile, never resend."""


def _valid_sha(value):
    return isinstance(value, str) and SHA.fullmatch(value) is not None


class GitHubLedgerCAS:
    """Give the existing cloud_runner journal functions, not keys.

    The same repository/default branch and fixed state/ledger.json must be
    targeted by both callbacks. Never map errors from the PUT to KnownConflict.
    Byte-identical blob rollback must not be used as a live journal operation.
    """

    def __init__(self, journal_get, journal_put):
        self._get = journal_get
        self._put = journal_put

    def read(self):
        ledger, sha = self._get()
        if not isinstance(ledger, dict) or not _valid_sha(sha):
            raise SlotHold("Existing GitHub journal and blob SHA required")
        return ledger, sha

    def write(self, ledger, expected):
        if not isinstance(ledger, dict) or not _valid_sha(expected):
            raise SlotHold("No valid existing GitHub revision for update")
        _, current = self.read()
        if current != expected:
            # This adapter has not issued a write, so bounded acquisition
            # can safely start over. The PUT still carries expected SHA to
            # close the race between this check and the mutation.
            raise KnownConflict("GitHub journal changed before PUT")
        try:
            new = self._put(ledger, expected)
        except Exception:
            # GitHub statuses are not SHA-specific through the legacy client.
            # Do not propagate a potentially sensitive raw HTTP body.
            raise JournalWriteUnconfirmed("GitHub PUT result unconfirmed; reconcile") from None
        if not _valid_sha(new) or new == expected:
            raise JournalWriteUnconfirmed("No distinct durable GitHub write receipt; reconcile")
        return new
