from django.db import models


class JobRun(models.Model):
    """One run of a scheduled job, so the compliance officer and auditors can see that
    the daily list check and the nightly monitoring actually ran."""

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        NOTHING_TO_DO = "nothing_to_do", "Nothing to do"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped (already running)"

    job = models.CharField(max_length=40, db_index=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    summary = models.JSONField(default=dict)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at", "-id"]
