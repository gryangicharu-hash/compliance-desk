"""Staff accounts, managed by the administrator role. Every change is in the audit trail.

New accounts and password resets get a one-time password, shown once, which the
person must change at first login. Accounts are deactivated, never deleted, so the
audit trail always resolves to a person."""
import secrets

from django import forms
from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from audittrail.services import record

from .models import Role, User
from .security import role_required

ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def one_time_password() -> str:
    return "-".join("".join(secrets.choice(ALPHABET) for _ in range(5)) for _ in range(4))


class StaffForm(forms.Form):
    username = forms.RegexField(regex=r"^[a-z0-9._-]{3,40}$", max_length=40,
                                help_text="Lower case letters, numbers, dot, dash or underscore")
    first_name = forms.CharField(max_length=60)
    last_name = forms.CharField(max_length=60)
    email = forms.EmailField(required=False, help_text="Needed for reminder emails (compliance officers)")
    role = forms.ChoiceField(choices=Role.choices)

    def clean_username(self):
        u = self.cleaned_data["username"]
        if User.objects.filter(username=u).exists():
            raise forms.ValidationError("That username is taken.")
        return u


@role_required(Role.ADMINISTRATOR)
def staff_list(request):
    form = StaffForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        pw = one_time_password()
        u = User.objects.create_user(password=pw, must_change_password=True, **form.cleaned_data)
        record(request.user.username, "user.created", u.username, {"role": u.role})
        messages.warning(request, f"Account {u.username} created. One-time password: {pw} "
                                  "(shown once; give it to the person directly. They must change it at first login.)")
        return redirect("accounts:staff")
    staff = User.objects.exclude(username="system").order_by("-is_active", "role", "username")
    return render(request, "accounts/staff.html", {"form": form, "staff": staff, "roles": Role.choices})


@require_POST
@role_required(Role.ADMINISTRATOR)
def staff_action(request, pk, action):
    u = get_object_or_404(User, pk=pk)
    if u.username == "system":
        raise Http404
    if u == request.user and action in ("deactivate", "role"):
        messages.error(request, "You can't deactivate or change the role of your own account.")
        return redirect("accounts:staff")
    if action == "deactivate":
        u.is_active = False
        u.save(update_fields=["is_active"])
        record(request.user.username, "user.deactivated", u.username, {})
        messages.success(request, f"{u.username} deactivated.")
    elif action == "reactivate":
        u.is_active = True
        u.save(update_fields=["is_active"])
        record(request.user.username, "user.reactivated", u.username, {})
        messages.success(request, f"{u.username} reactivated.")
    elif action == "reset-password":
        pw = one_time_password()
        u.set_password(pw)
        u.must_change_password = True
        u.save(update_fields=["password", "must_change_password"])
        record(request.user.username, "user.password_reset", u.username, {})
        messages.warning(request, f"New one-time password for {u.username}: {pw} (shown once).")
    elif action == "reset-2fa":
        u.mfa_secret_enc, u.mfa_confirmed, u.mfa_last_step = "", False, None
        u.save(update_fields=["mfa_secret_enc", "mfa_confirmed", "mfa_last_step"])
        record(request.user.username, "user.mfa_reset", u.username, {})
        messages.success(request, f"{u.username} will set up two-factor login again at next login.")
    elif action == "role":
        role = request.POST.get("role")
        if role not in Role.values:
            raise Http404
        old, u.role = u.role, role
        u.save(update_fields=["role"])
        record(request.user.username, "user.role_changed", u.username, {"from": old, "to": role})
        messages.success(request, f"{u.username} is now {u.get_role_display().lower()}.")
    else:
        raise Http404
    return redirect("accounts:staff")
