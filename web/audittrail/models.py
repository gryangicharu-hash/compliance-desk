from django.db import models


class AuditEvent(models.Model):
    """One entry in the hash-chained audit trail. Append-only: a database trigger
    (migration 0002) refuses UPDATE, DELETE and TRUNCATE."""
    seq = models.BigIntegerField(primary_key=True)
    at = models.DateTimeField(db_index=True)
    at_text = models.CharField(max_length=40)      # exact timestamp string that was hashed
    actor = models.CharField(max_length=150)
    action = models.CharField(max_length=80, db_index=True)
    subject = models.CharField(max_length=150, db_index=True)
    detail = models.JSONField(default=dict)
    prev_hash = models.CharField(max_length=64)
    hash = models.CharField(max_length=64, unique=True)

    class Meta:
        ordering = ["-seq"]

    def __str__(self) -> str:
        return f"{self.seq} {self.action} {self.subject}"
