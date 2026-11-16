from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="AuditEvent",
            fields=[
                ("seq", models.BigIntegerField(primary_key=True, serialize=False)),
                ("at", models.DateTimeField(db_index=True)),
                ("at_text", models.CharField(max_length=40)),
                ("actor", models.CharField(max_length=150)),
                ("action", models.CharField(db_index=True, max_length=80)),
                ("subject", models.CharField(db_index=True, max_length=150)),
                ("detail", models.JSONField(default=dict)),
                ("prev_hash", models.CharField(max_length=64)),
                ("hash", models.CharField(max_length=64, unique=True)),
            ],
            options={"ordering": ["-seq"]},
        ),
    ]
