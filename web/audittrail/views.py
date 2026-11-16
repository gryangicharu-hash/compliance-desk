from django.core.paginator import Paginator
from django.shortcuts import render

from accounts.models import Role
from accounts.security import role_required

from .models import AuditEvent
from .services import verify_all


@role_required(Role.COMPLIANCE_OFFICER, Role.SENIOR_MANAGEMENT, Role.AUDITOR, Role.ADMINISTRATOR)
def event_list(request):
    qs = AuditEvent.objects.all()
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(subject__icontains=q) | qs.filter(action__icontains=q) | qs.filter(actor__icontains=q)
    page = Paginator(qs, 100).get_page(request.GET.get("page"))
    broken = verify_all() if request.GET.get("verify") else "not checked"
    return render(request, "audittrail/list.html", {"page": page, "q": q, "broken": broken})
