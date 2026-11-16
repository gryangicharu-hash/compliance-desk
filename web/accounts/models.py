from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models


class Role(models.TextChoices):
    FRONT_OFFICE = "front_office", "Front office"
    COMPLIANCE_OFFICER = "compliance_officer", "Compliance officer"
    SENIOR_MANAGEMENT = "senior_management", "Senior management"
    AUDITOR = "auditor", "Auditor"
    ADMINISTRATOR = "administrator", "Administrator"


# Roles that can see suspicious-activity information must use two-factor login.
MFA_EXEMPT_ROLES = {Role.FRONT_OFFICE}


class ComplianceUserManager(UserManager):
    def create_superuser(self, username, email=None, password=None, **extra):
        extra.setdefault("role", Role.ADMINISTRATOR)
        return super().create_superuser(username, email, password, **extra)


class User(AbstractUser):
    role = models.CharField(max_length=32, choices=Role.choices, default=Role.FRONT_OFFICE)
    mfa_secret_enc = models.TextField(blank=True, default="")      # encrypted with FieldCipher
    mfa_confirmed = models.BooleanField(default=False)
    mfa_last_step = models.BigIntegerField(null=True, blank=True)  # replay protection

    objects = ComplianceUserManager()

    @property
    def requires_mfa(self) -> bool:
        return self.role not in MFA_EXEMPT_ROLES or self.mfa_confirmed

    def has_role(self, *roles: str) -> bool:
        return self.is_active and self.role in roles

    def __str__(self) -> str:
        return self.get_full_name() or self.username
