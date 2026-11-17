from django.conf import settings
from django.db import models
from django.db.models import Q

from members.models import Member


class ListKind(models.TextChoices):
    SANCTIONS = "sanctions", "Sanctions"
    PEP = "pep", "PEP"


class ListVersion(models.Model):
    class Status(models.TextChoices):
        CURRENT = "current", "In use"
        SUPERSEDED = "superseded", "Replaced"
        REJECTED = "rejected", "Rejected"

    list_name = models.CharField(max_length=120)
    kind = models.CharField(max_length=20, choices=ListKind.choices)
    source = models.CharField(max_length=255)          # uploaded file name or download URL
    sha256 = models.CharField(max_length=64)
    entry_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=Status.choices)
    error = models.TextField(blank=True)
    loaded_at = models.DateTimeField(auto_now_add=True)
    loaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)

    class Meta:
        ordering = ["-loaded_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["list_name"], condition=Q(status="current"),
                                               name="one_current_version_per_list")]


class ListEntry(models.Model):
    version = models.ForeignKey(ListVersion, on_delete=models.CASCADE, related_name="entries")
    ref = models.CharField(max_length=80)
    entry_type = models.CharField(max_length=20)       # individual / entity
    primary_name = models.CharField(max_length=300)
    aliases = models.JSONField(default=list)            # [[name, quality], ...]
    dates_of_birth = models.JSONField(default=list)
    nationalities = models.JSONField(default=list)
    listed_on = models.CharField(max_length=20, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["version", "ref"], name="unique_ref_per_version")]


class Hit(models.Model):
    class Status(models.TextChoices):
        TO_REVIEW = "to_review", "To review"
        FALSE_POSITIVE = "false_positive", "False positive"
        CONFIRMED = "confirmed", "Confirmed"
        LIFTED = "lifted", "Confirmed, later lifted (two-person release)"

    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="hits")
    kind = models.CharField(max_length=20, choices=ListKind.choices)
    subject_name = models.CharField(max_length=300)
    subject_role = models.CharField(max_length=20)       # member / official / beneficial_owner
    list_name = models.CharField(max_length=120)
    entry_ref = models.CharField(max_length=80)
    entry_name = models.CharField(max_length=300)
    matched_name = models.CharField(max_length=300)
    score = models.DecimalField(max_digits=5, decimal_places=4)
    method = models.CharField(max_length=20)
    dob_check = models.CharField(max_length=20)
    nationality_check = models.CharField(max_length=20)
    confidence = models.CharField(max_length=10)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.TO_REVIEW)
    raised_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-raised_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["member", "subject_name", "list_name", "entry_ref"], name="one_hit_per_pair"),
            models.CheckConstraint(
                condition=Q(status="to_review") | (Q(decided_by__isnull=False) & Q(decided_at__isnull=False) & ~Q(decision_note="")),
                name="hit_decision_recorded"),
        ]
