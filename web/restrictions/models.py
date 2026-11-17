"""What the core banking system must do with each member's accounts.

Compliance Desk decides holds and freezes, but money moves in the core banking system.
Every change of restriction is queued here and delivered (signed web call, and/or a
file in the outbox), and the core banking system's own list of restricted accounts is
imported back to show any member it has not yet restricted.

The levels name what to do, never why: the core banking system and its users must not
learn of suspicion or a case (tipping-off)."""
from django.db import models


class Level(models.TextChoices):
    NONE = "none", "No restriction"
    NO_NEW_BUSINESS = "no_new_business", "Awaiting approval: no new products or loans"
    HOLD = "hold", "Compliance hold: no transactions"
    FREEZE = "freeze", "Compliance freeze: no funds may move"
    CLOSED = "closed", "Membership closed"


ENFORCED = (Level.HOLD, Level.FREEZE)      # must show as restricted in core banking


class RestrictionState(models.Model):
    member = models.OneToOneField("members.Member", on_delete=models.PROTECT, related_name="restriction")
    level = models.CharField(max_length=20, choices=Level.choices)
    since = models.DateTimeField()


class RestrictionChange(models.Model):
    member = models.ForeignKey("members.Member", on_delete=models.PROTECT, related_name="restriction_changes")
    previous = models.CharField(max_length=20, choices=Level.choices)
    level = models.CharField(max_length=20, choices=Level.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["created_at", "id"]


class RestrictionExport(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.CharField(max_length=150)
    destination = models.CharField(max_length=20)          # download / outbox
    filename = models.CharField(max_length=200)
    sha256 = models.CharField(max_length=64)
    rows = models.PositiveIntegerField()

    class Meta:
        ordering = ["-created_at", "-id"]


class CoreReport(models.Model):
    """One file from core banking listing the accounts it currently restricts."""
    batch = models.OneToOneField("corebanking.ImportBatch", on_delete=models.PROTECT, related_name="core_report")
    received_at = models.DateTimeField(auto_now_add=True)
    as_at = models.DateTimeField()

    class Meta:
        ordering = ["-received_at", "-id"]


class CoreRestricted(models.Model):
    report = models.ForeignKey(CoreReport, on_delete=models.CASCADE, related_name="entries")
    member = models.ForeignKey("members.Member", on_delete=models.PROTECT, related_name="+")
    detail = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["report", "member"], name="one_entry_per_member_per_report")]
