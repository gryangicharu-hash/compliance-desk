import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("members", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Case",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("ref", models.CharField(max_length=20, unique=True)),
                ("case_type", models.CharField(choices=[("unusual_activity", "Unusual activity"), ("sanctions_match", "Sanctions match"), ("incomplete_kyc", "Incomplete KYC (reg. 21)"), ("other", "Other suspicion")], max_length=30)),
                ("reason", models.TextField()),
                ("suspicion_formed_at", models.DateTimeField()),
                ("str_due_at", models.DateTimeField()),
                ("status", models.CharField(choices=[("open", "Open"), ("str_filed", "STR filed"), ("closed_not_suspicious", "Closed: not suspicious")], default="open", max_length=30)),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                ("decision_note", models.TextField(blank=True)),
                ("frc_reference", models.CharField(blank=True, max_length=80)),
                ("decided_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="cases_decided", to=settings.AUTH_USER_MODEL)),
                ("member", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="cases", to="members.member")),
                ("opened_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="cases_opened", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["status", "str_due_at"]},
        ),
        migrations.AddConstraint(
            model_name="case",
            constraint=models.CheckConstraint(condition=models.Q(("str_due_at__gt", models.F("suspicion_formed_at"))), name="due_after_suspicion"),
        ),
        migrations.AddConstraint(
            model_name="case",
            constraint=models.CheckConstraint(condition=models.Q(("status", "open"), models.Q(("decided_by__isnull", False), ("decided_at__isnull", False), models.Q(("decision_note", ""), _negated=True)), _connector="OR"), name="case_decision_recorded"),
        ),
        migrations.AddConstraint(
            model_name="case",
            constraint=models.CheckConstraint(condition=models.Q(models.Q(("status", "str_filed"), _negated=True), models.Q(("frc_reference", ""), _negated=True), _connector="OR"), name="filed_has_frc_reference"),
        ),
    ]
