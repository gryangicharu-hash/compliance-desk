"""Transaction monitoring alerts and the weekly large cash report.

Alerts are suspicion information: like cases they are hidden from front office
by row-level security (migration 0002), not only by the views."""
from django.conf import settings
from django.db import models
from django.db.models import Q

from members.models import Member


class Rule(models.TextChoices):
    LARGE_CASH = "LARGE_CASH", "Large cash (weekly report)"
    STRUCTURING = "STRUCTURING", "Possible structuring"
    INCOME_MISMATCH = "INCOME_MISMATCH", "Credits far above declared income"
    RAPID_IN_OUT = "RAPID_IN_OUT", "Money in and straight out"
    DORMANT_REACTIVATION = "DORMANT_REACTIVATION", "Dormant account suddenly active"
    MANY_PAYERS = "MANY_PAYERS", "Payments from many different payers"


class Alert(models.Model):
    class Status(models.TextChoices):
        TO_REVIEW = "to_review", "To review"
        NO_ACTION = "no_action", "Reviewed: no further action"
        CASE_OPENED = "case_opened", "Case opened"
        FOR_CASH_REPORT = "for_cash_report", "For the weekly cash report"
        REPORTED = "reported", "Reported to the FRC"

    rule = models.CharField(max_length=30, choices=Rule.choices)
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="alerts")
    txn_ids = models.JSONField(default=list)
    key = models.CharField(max_length=64, unique=True)           # sha256 of rule, member and transactions
    description = models.TextField()
    reportable_only = models.BooleanField(default=False)
    raised_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=Status.choices)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="alerts_decided")
    decided_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)
    case = models.ForeignKey("cases.Case", null=True, blank=True, on_delete=models.PROTECT, related_name="alerts")
    cash_report = models.ForeignKey("CashReport", null=True, blank=True, on_delete=models.PROTECT, related_name="alerts")

    class Meta:
        ordering = ["raised_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(status__in=["no_action", "case_opened"])
                | (Q(decided_by__isnull=False) & Q(decided_at__isnull=False) & ~Q(note="")),
                name="alert_decision_recorded"),
            models.CheckConstraint(condition=~Q(status="case_opened") | Q(case__isnull=False), name="alert_case_linked"),
            models.CheckConstraint(condition=~Q(status="reported") | Q(cash_report__isnull=False),
                                   name="alert_report_linked"),
        ]


class CashReport(models.Model):
    """One week's large cash report (reg. 35): transactions from Saturday to Friday,
    due that Friday. Recorded when the compliance officer has submitted it."""
    period_start = models.DateField()
    period_end = models.DateField(unique=True)
    rows = models.PositiveIntegerField()
    frc_reference = models.CharField(max_length=80)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    submitted_at = models.DateTimeField(auto_now_add=True)
    file_sha256 = models.CharField(max_length=64)

    class Meta:
        ordering = ["-period_end"]
        constraints = [models.CheckConstraint(condition=~Q(frc_reference=""), name="cash_report_has_reference")]
