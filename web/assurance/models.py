"""Auditors' working papers and the governance records the audit tests rely on.

Independence safeguards (enforced in the database by migration 0002, not only in views):
  * only the auditor role can create or change engagements, test runs, samples,
    programme steps and findings;
  * saved test runs, results and samples can never be changed or deleted;
  * management (compliance officer, senior management) can read engagements and
    findings and record a response, but cannot change what the auditor wrote.
"""
from django.conf import settings
from django.db import models
from django.db.models import F, Q

SEVERITIES = [("high", "High"), ("medium", "Medium"), ("low", "Low")]


class Engagement(models.Model):
    class Kind(models.TextChoices):
        INTERNAL = "internal_audit", "Internal audit"
        EXTERNAL = "external_audit", "External audit"
        INSPECTION = "inspection_preparation", "Inspection preparation"

    class Status(models.TextChoices):
        PLANNING = "planning", "Planning"
        FIELDWORK = "fieldwork", "Fieldwork"
        REPORTING = "reporting", "Reporting"
        CLOSED = "closed", "Closed"

    ref = models.CharField(max_length=30, unique=True)
    kind = models.CharField(max_length=30, choices=Kind.choices)
    title = models.CharField(max_length=200)
    period_start = models.DateField()
    period_end = models.DateField()
    scope = models.TextField(blank=True)
    lead_auditor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="engagements")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNING)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [models.CheckConstraint(condition=Q(period_end__gte=F("period_start")), name="engagement_period_order")]

    def __str__(self) -> str:
        return self.ref


class TestRun(models.Model):
    engagement = models.ForeignKey(Engagement, on_delete=models.PROTECT, related_name="runs")
    run_at = models.DateTimeField()
    data_fingerprint = models.CharField(max_length=64)
    engine_version = models.CharField(max_length=20)
    run_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    class Meta:
        ordering = ["-run_at", "-id"]


class TestResult(models.Model):
    run = models.ForeignKey(TestRun, on_delete=models.PROTECT, related_name="results")
    code = models.CharField(max_length=10)
    title = models.CharField(max_length=200)
    regulation = models.CharField(max_length=80)
    severity = models.CharField(max_length=10, choices=SEVERITIES)
    population = models.PositiveIntegerField()
    exceptions = models.JSONField(default=list)          # [[ref, detail], ...]
    passed = models.BooleanField()

    class Meta:
        ordering = ["code"]
        constraints = [models.UniqueConstraint(fields=["run", "code"], name="one_result_per_test_per_run")]

    @property
    def outcome(self) -> str:
        if self.population == 0:
            return "nothing to test"
        return "pass" if self.passed else "fail"


class Sample(models.Model):
    engagement = models.ForeignKey(Engagement, on_delete=models.PROTECT, related_name="samples")
    population_name = models.CharField(max_length=80)
    method = models.CharField(max_length=120)
    seed = models.BigIntegerField()
    population_size = models.PositiveIntegerField()
    selected = models.JSONField(default=list)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class ProgrammeStep(models.Model):
    class Result(models.TextChoices):
        SATISFACTORY = "satisfactory", "Satisfactory"
        EXCEPTION = "exception", "Exception"
        NOT_APPLICABLE = "not_applicable", "Not applicable"

    engagement = models.ForeignKey(Engagement, on_delete=models.PROTECT, related_name="steps")
    step_no = models.PositiveIntegerField()
    area = models.CharField(max_length=60)
    regulation = models.CharField(max_length=80)
    procedure = models.TextField()
    test_code = models.CharField(max_length=10, blank=True)
    result = models.CharField(max_length=20, choices=Result.choices, blank=True)
    workpaper_ref = models.CharField(max_length=60, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["step_no"]
        constraints = [models.UniqueConstraint(fields=["engagement", "step_no"], name="one_step_number_per_engagement")]


class Finding(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In progress"
        CLOSED = "closed", "Closed"
        RISK_ACCEPTED = "risk_accepted", "Risk accepted by the board"

    engagement = models.ForeignKey(Engagement, on_delete=models.PROTECT, related_name="findings")
    ref = models.CharField(max_length=10)
    title = models.CharField(max_length=200)
    regulation = models.CharField(max_length=80)
    severity = models.CharField(max_length=10, choices=SEVERITIES)
    condition = models.TextField()
    recommendation = models.TextField()
    test_code = models.CharField(max_length=10, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    closed_on = models.DateField(null=True, blank=True)
    closure_evidence = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["engagement", "ref"]
        constraints = [
            models.UniqueConstraint(fields=["engagement", "ref"], name="one_finding_ref_per_engagement"),
            models.CheckConstraint(condition=~Q(status="closed") | (Q(closed_on__isnull=False) & ~Q(closure_evidence="")),
                                   name="closed_finding_needs_evidence"),
        ]

    @property
    def severity_rank(self) -> int:
        return ["high", "medium", "low"].index(self.severity)


class FindingResponse(models.Model):
    """Management's answer, kept apart from the finding so management can respond
    without being able to edit the auditor's words."""
    finding = models.OneToOneField(Finding, on_delete=models.PROTECT, related_name="response")
    response = models.TextField()
    owner = models.CharField(max_length=120)
    due_on = models.DateField()
    responded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    responded_at = models.DateTimeField(auto_now=True)


class TrainingRecord(models.Model):
    """AML training completed by a member of staff (audit test AT16, board report)."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="training")
    course = models.CharField(max_length=160)
    completed_on = models.DateField()
    valid_until = models.DateField()
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-completed_on", "-id"]
        constraints = [models.CheckConstraint(condition=Q(valid_until__gte=F("completed_on")), name="training_valid_after_completion")]


class RiskAssessmentApproval(models.Model):
    """Board approval of the institutional ML/TF risk assessment (reg. 6; test AT17)."""
    approved_on = models.DateField()
    board_minute_ref = models.CharField(max_length=80)
    document_name = models.CharField(max_length=200)
    risk_settings_version = models.CharField(max_length=60, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-approved_on", "-id"]
