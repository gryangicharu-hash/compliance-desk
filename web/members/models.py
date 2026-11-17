from django.conf import settings
from django.db import models
from django.db.models import F, Q
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
    last_screened_at = models.DateTimeField(null=True, blank=True)     # audit test AT10
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


class MemberChange(models.Model):
    """One edit of a member's details. Previous values are kept encrypted (they include
    personal data) so the file's history can be shown to compliance and auditors."""
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="changes")
    changed_at = models.DateTimeField(auto_now_add=True)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    fields = models.JSONField(default=list)
    previous_enc = models.TextField()
    reason = models.TextField()

    class Meta:
        ordering = ["-changed_at", "-id"]


class DocType(models.TextChoices):
    NATIONAL_ID = "national_id", "National ID"
    PASSPORT = "passport", "Passport"
    KRA_PIN = "kra_pin", "KRA PIN certificate"
    PAYSLIP = "payslip", "Payslip"
    BANK_STATEMENT = "bank_statement", "Bank or M-Pesa statement"
    PROOF_OF_ADDRESS = "proof_of_address", "Proof of address"
    REGISTRATION = "registration", "Registration certificate"
    RESOLUTION = "resolution", "Resolution or minutes"
    OFFICIALS_IDS = "officials_ids", "Officials' or owners' IDs"
    SOURCE_OF_WEALTH = "source_of_wealth", "Source of wealth evidence"
    OTHER = "other", "Other"


class MemberDocument(models.Model):
    """A KYC document. The file is encrypted on disk; the sha256 of the original is
    kept so any later copy can be shown to be the same file. Documents are never
    deleted while the record must be kept; a replaced one is marked superseded."""
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="documents")
    doc_type = models.CharField(max_length=30, choices=DocType.choices)
    description = models.CharField(max_length=200, blank=True)
    filename = models.CharField(max_length=200)
    content_type = models.CharField(max_length=60)
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    stored_name = models.CharField(max_length=80, unique=True)
    expires_on = models.DateField(null=True, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    superseded_at = models.DateTimeField(null=True, blank=True)
    superseded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    superseded_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["superseded_at", "-uploaded_at", "-id"]
        constraints = [models.CheckConstraint(
            condition=Q(superseded_at__isnull=True) | ~Q(superseded_reason=""), name="superseded_document_has_reason")]


class StatusRelease(models.Model):
    """Lifting a sanctions freeze: one compliance officer asks, a different person
    (compliance officer or senior management) decides."""

    class State(models.TextChoices):
        PENDING = "pending", "Waiting for a second person"
        APPROVED = "approved", "Approved: freeze lifted"
        REJECTED = "rejected", "Rejected"

    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="releases")
    reason = models.TextField()
    evidence = models.TextField()
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    requested_at = models.DateTimeField(auto_now_add=True)
    state = models.CharField(max_length=10, choices=State.choices, default=State.PENDING)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-requested_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["member"], condition=Q(state="pending"), name="one_pending_release_per_member"),
            models.CheckConstraint(condition=~Q(decided_by=F("requested_by")), name="release_needs_two_people"),
            models.CheckConstraint(condition=Q(state="pending") | (Q(decided_by__isnull=False) & ~Q(decision_note="")),
                                   name="release_decision_recorded"),
        ]
