from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class MemberType(models.TextChoices):
    INDIVIDUAL = "individual", "Individual"
    GROUP = "group", "Group or chama"
    CORPORATE = "corporate", "Company"


class Channel(models.TextChoices):
    BRANCH = "branch", "Branch"
    AGENT = "agent", "Agent"
    MOBILE_APP = "mobile_app", "Mobile app"
    ONLINE = "online", "Online"


class Pep(models.TextChoices):
    NONE = "none", "Not a PEP"
    DOMESTIC = "domestic", "Domestic PEP"
    FOREIGN = "foreign", "Foreign PEP"
    INTERNATIONAL_ORG = "international_organisation", "International organisation PEP"
    FAMILY_OR_ASSOCIATE = "family_or_close_associate", "Family member or close associate of a PEP"


class Status(models.TextChoices):
    ACTIVE = "active", "Active"
    PENDING_APPROVAL = "pending_approval", "Awaiting approval"
    SCREENING_HOLD = "screening_hold", "Screening hold"
    BLOCKED = "blocked", "Blocked"
    EXITED = "exited", "Exited"


class Level(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"


PRODUCTS = [
    ("bosa_deposits", "BOSA deposits"), ("fosa_account", "FOSA account"), ("development_loan", "Development loan"),
    ("mobile_loans", "Mobile loans"), ("fixed_deposit", "Fixed deposit"), ("asset_finance", "Asset finance"),
    ("foreign_currency_account", "Foreign currency account"),
]


class Member(models.Model):
    member_no = models.CharField(max_length=20, unique=True)
    member_type = models.CharField(max_length=20, choices=MemberType.choices, default=MemberType.INDIVIDUAL)
    name = models.CharField(max_length=200, db_index=True)
    id_number_enc = models.TextField()
    id_number_bidx = models.CharField(max_length=64, unique=True)
    date_of_birth = models.DateField(null=True, blank=True)
    nationality = models.CharField(max_length=80, default="Kenya")
    residence = models.CharField(max_length=80, default="Kenya")
    occupation = models.CharField(max_length=120, blank=True)
    employer = models.CharField(max_length=160, blank=True)
    checkoff = models.BooleanField(default=False)
    channel = models.CharField(max_length=20, choices=Channel.choices, default=Channel.BRANCH)
    products = models.JSONField(default=list)
    pep = models.CharField(max_length=40, choices=Pep.choices, default=Pep.NONE)
    pep_approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="approved_members")
    pep_approved_at = models.DateTimeField(null=True, blank=True)
    source_of_funds = models.TextField(blank=True)
    source_of_wealth = models.TextField(blank=True)
    declared_monthly_income = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    expected_cash = models.CharField(max_length=10, choices=Level.choices, default=Level.LOW)
    kyc_complete = models.BooleanField(default=False)
    adverse_media = models.BooleanField(default=False)
    id_expiry = models.DateField(null=True, blank=True)
    joined_on = models.DateField(default=timezone.localdate)
    last_review_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING_APPROVAL)
    exited_on = models.DateField(null=True, blank=True)
    onboarded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT,
                                     related_name="onboarded_members")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["member_no"]
        constraints = [
            models.CheckConstraint(condition=Q(declared_monthly_income__gte=0), name="income_not_negative"),
            models.CheckConstraint(
                condition=(Q(status="exited") & Q(exited_on__isnull=False)) | (~Q(status="exited") & Q(exited_on__isnull=True)),
                name="exit_date_when_exited"),
            models.CheckConstraint(
                condition=Q(pep="none") | ~Q(status="active") | Q(pep_approved_by__isnull=False),
                name="active_pep_is_approved"),
            models.CheckConstraint(
                condition=Q(pep="none") | ~Q(status="active") | ~Q(source_of_wealth=""),
                name="active_pep_has_source_of_wealth"),
        ]

    def __str__(self) -> str:
        return f"{self.member_no} {self.name}"

    @property
    def latest_assessment(self):
        return self.assessments.order_by("-assessed_at", "-id").first()


class MemberPerson(models.Model):
    class Kind(models.TextChoices):
        OFFICIAL = "official", "Official or signatory"
        BENEFICIAL_OWNER = "beneficial_owner", "Beneficial owner"

    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="persons")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    name = models.CharField(max_length=200)
    role = models.CharField(max_length=80, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    nationality = models.CharField(max_length=80, blank=True)
    ownership_pct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(
            condition=Q(ownership_pct__isnull=True) | (Q(ownership_pct__gte=0) & Q(ownership_pct__lte=100)),
            name="ownership_pct_range")]


class RiskAssessment(models.Model):
    """A rating is never edited: every re-assessment adds a row, so history is kept."""
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="assessments")
    assessed_at = models.DateTimeField(auto_now_add=True)
    score = models.PositiveSmallIntegerField()
    points = models.JSONField()
    base_rating = models.CharField(max_length=12)
    rating = models.CharField(max_length=12, db_index=True)
    reasons = models.JSONField(default=list)
    overrides = models.JSONField(default=list)
    due_diligence = models.CharField(max_length=12)
    next_review_on = models.DateField(null=True, blank=True)
    settings_version = models.CharField(max_length=60)
    assessed_by = models.CharField(max_length=150)

    class Meta:
        ordering = ["-assessed_at", "-id"]
