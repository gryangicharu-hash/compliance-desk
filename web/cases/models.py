from django.conf import settings
from django.db import models
from django.db.models import F, Q

from members.models import Member


class Case(models.Model):
    """A suspicious-activity case. Hidden from front office by row-level security
    (migration 0002) as well as by the views, to prevent tipping-off."""

    class Type(models.TextChoices):
        UNUSUAL_ACTIVITY = "unusual_activity", "Unusual activity"
        SANCTIONS_MATCH = "sanctions_match", "Sanctions match"
        INCOMPLETE_KYC = "incomplete_kyc", "Incomplete KYC (reg. 21)"
        OTHER = "other", "Other suspicion"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        STR_FILED = "str_filed", "STR filed"
        CLOSED = "closed_not_suspicious", "Closed: not suspicious"

    ref = models.CharField(max_length=20, unique=True)
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="cases")
    case_type = models.CharField(max_length=30, choices=Type.choices)
    reason = models.TextField()
    suspicion_formed_at = models.DateTimeField()
    str_due_at = models.DateTimeField()
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.OPEN)
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cases_opened")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="cases_decided")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)
    frc_reference = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["status", "str_due_at"]
        constraints = [
            models.CheckConstraint(condition=Q(str_due_at__gt=F("suspicion_formed_at")), name="due_after_suspicion"),
            models.CheckConstraint(
                condition=Q(status="open") | (Q(decided_by__isnull=False) & Q(decided_at__isnull=False) & ~Q(decision_note="")),
                name="case_decision_recorded"),
            models.CheckConstraint(condition=~Q(status="str_filed") | ~Q(frc_reference=""), name="filed_has_frc_reference"),
        ]

    def __str__(self) -> str:
        return self.ref
