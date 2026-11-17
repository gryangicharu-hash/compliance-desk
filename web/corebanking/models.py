"""Data brought in from the SACCO's core banking system: members, loans with their
guarantors, and transactions. Every file is an ImportBatch, so each record can be
traced to the file it came from."""
from django.conf import settings
from django.db import models
from django.db.models import Q

from members.models import Member


class ImportKind(models.TextChoices):
    MEMBERS = "members", "Members"
    LOANS = "loans", "Loans and guarantors"
    TRANSACTIONS = "transactions", "Transactions"
    RESTRICTED = "restricted_accounts", "Restricted accounts (confirmation from core banking)"


class ImportProfile(models.Model):
    """How one core banking system names its columns: {their column: our column}."""
    name = models.CharField(max_length=80, unique=True)
    kind = models.CharField(max_length=20, choices=ImportKind.choices)
    mapping = models.JSONField(default=dict)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["kind", "name"]

    def __str__(self) -> str:
        return self.name


class ImportBatch(models.Model):
    class Status(models.TextChoices):
        IMPORTED = "imported", "Imported"
        REJECTED = "rejected", "Rejected"

    kind = models.CharField(max_length=20, choices=ImportKind.choices)
    source = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64, db_index=True)
    profile = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices)
    rows_total = models.PositiveIntegerField(default=0)
    created = models.PositiveIntegerField(default=0)
    updated = models.PositiveIntegerField(default=0)
    unchanged = models.PositiveIntegerField(default=0)
    errors = models.JSONField(default=list)            # ["Row 4: amount must be a number", ...]
    summary = models.JSONField(default=dict)
    imported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-imported_at", "-id"]


class Loan(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CLOSED = "closed", "Closed"

    loan_no = models.CharField(max_length=40, unique=True)
    borrower = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="loans")
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    disbursed_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    batch = models.ForeignKey(ImportBatch, on_delete=models.PROTECT, related_name="loans")

    class Meta:
        ordering = ["loan_no"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="loan_amount_positive")]


class Guarantee(models.Model):
    loan = models.ForeignKey(Loan, on_delete=models.CASCADE, related_name="guarantees")
    guarantor = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="guarantees_given")
    amount = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["loan", "guarantor"], name="one_guarantee_per_guarantor_per_loan"),
            models.CheckConstraint(condition=Q(amount__gte=0), name="guarantee_not_negative"),
        ]


class Transaction(models.Model):
    class Direction(models.TextChoices):
        IN = "in", "Credit"
        OUT = "out", "Debit"

    class Channel(models.TextChoices):
        CASH = "cash", "Cash"
        MOBILE_MONEY = "mobile_money", "Mobile money"
        BANK_TRANSFER = "bank_transfer", "Bank transfer"
        CHECKOFF = "checkoff", "Check-off"
        INTERNAL = "internal", "Internal transfer"

    txn_id = models.CharField(max_length=60, unique=True)
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="transactions")
    at = models.DateTimeField()
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    direction = models.CharField(max_length=3, choices=Direction.choices)
    channel = models.CharField(max_length=20, choices=Channel.choices)
    counterparty = models.CharField(max_length=120, blank=True)
    purpose = models.CharField(max_length=60, blank=True)
    batch = models.ForeignKey(ImportBatch, on_delete=models.PROTECT, related_name="transactions")

    class Meta:
        ordering = ["at", "id"]
        indexes = [models.Index(fields=["member", "at"], name="txn_member_time")]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="txn_amount_positive")]


class NetworkFlag(models.Model):
    """Guarantor-network findings, recalculated as a whole (corebanking.network)
    so that risk assessment can read them cheaply."""
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="network_flags")
    code = models.CharField(max_length=30)
    severity = models.CharField(max_length=10)
    text = models.CharField(max_length=300)
    computed_at = models.DateTimeField()

    class Meta:
        ordering = ["member__member_no", "code"]
