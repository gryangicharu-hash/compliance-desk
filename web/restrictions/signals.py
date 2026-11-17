from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from members.models import Member


@receiver(post_save, sender=Member, dispatch_uid="restrictions.member_saved")
def member_saved(sender, instance: Member, created, update_fields=None, raw=False, **kwargs):
    if raw or (update_fields is not None and "status" not in update_fields and not created):
        return
    from .services import deliver_pending, sync_member
    if sync_member(instance):
        transaction.on_commit(lambda: deliver_pending(limit=20, quiet=True))
