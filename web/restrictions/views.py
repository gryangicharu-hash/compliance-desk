from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render

from accounts.models import Role
from accounts.security import role_required

from . import services
from .models import Level, RestrictionChange, RestrictionExport, RestrictionState

CAN_VIEW = (Role.COMPLIANCE_OFFICER, Role.SENIOR_MANAGEMENT, Role.AUDITOR, Role.ADMINISTRATOR)


@role_required(*CAN_VIEW)
def overview(request):
    r = services.reconcile()
    counts = {Level(l).label: RestrictionState.objects.filter(level=l).count()
              for l in (Level.FREEZE, Level.HOLD, Level.NO_NEW_BUSINESS)}
    return render(request, "restrictions/overview.html", {
        "r": r, "counts": counts, "webhook": bool(settings.CORE_BANKING_WEBHOOK_URL),
        "outbox": bool(settings.EXPORT_OUTBOX),
        "pending": RestrictionChange.objects.filter(delivered_at__isnull=True).select_related("member")[:50],
        "recent": RestrictionChange.objects.select_related("member").order_by("-created_at", "-id")[:30],
        "exports": RestrictionExport.objects.all()[:10]})


@role_required(Role.COMPLIANCE_OFFICER, Role.ADMINISTRATOR)
def download(request):
    text, exp = services.export_csv(request.user.username, "download")
    resp = HttpResponse(text, content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{exp.filename}"'
    return resp
