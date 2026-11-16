from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from audittrail.services import record

from . import totp
from .middleware import MFA_SESSION_KEY
from .security import cipher


class LoginForm(forms.Form):
    username = forms.CharField(max_length=150)
    password = forms.CharField(widget=forms.PasswordInput)


class CodeForm(forms.Form):
    code = forms.CharField(max_length=8, label="6-digit code",
                           widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code"}))


def _fail_key(username: str) -> str:
    return f"login-fail:{username.strip().lower()}"


def _safe_next(request, default="home"):
    nxt = request.POST.get("next") or request.GET.get("next")
    if nxt and url_has_allowed_host_and_scheme(nxt, {request.get_host()}, require_https=request.is_secure()):
        return nxt
    return default


def login_view(request):
    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        username = form.cleaned_data["username"]
        key = _fail_key(username)
        failures = cache.get(key, 0)
        if failures >= settings.LOGIN_MAX_FAILURES:
            record("system", "user.login_blocked", username, {"reason": "locked"})
            form.add_error(None, "Too many failed attempts. Try again in 15 minutes or ask an administrator.")
        else:
            user = authenticate(request, username=username, password=form.cleaned_data["password"])
            if user is None:
                cache.set(key, failures + 1, settings.LOGIN_LOCKOUT_SECONDS)
                record("system", "user.login_failed", username, {"failures": failures + 1})
                form.add_error(None, "Username or password is incorrect.")
            else:
                cache.delete(key)
                login(request, user)
                request.session[MFA_SESSION_KEY] = False
                record(user.username, "user.login", user.username, {"role": user.role})
                if user.requires_mfa:
                    return redirect("accounts:mfa_verify" if user.mfa_confirmed else "accounts:mfa_setup")
                return redirect(_safe_next(request))
    return render(request, "accounts/login.html", {"form": form, "next": request.GET.get("next", "")})


def mfa_setup(request):
    user = request.user
    if user.mfa_confirmed:
        return redirect("accounts:mfa_verify")
    if not user.mfa_secret_enc:
        user.mfa_secret_enc = cipher().encrypt(totp.new_secret())
        user.save(update_fields=["mfa_secret_enc"])
    secret = cipher().decrypt(user.mfa_secret_enc)
    form = CodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        step = totp.verify(secret, form.cleaned_data["code"])
        if step is None:
            form.add_error("code", "That code doesn't match. Check the time on your phone and try again.")
        else:
            user.mfa_confirmed, user.mfa_last_step = True, step
            user.save(update_fields=["mfa_confirmed", "mfa_last_step"])
            request.session[MFA_SESSION_KEY] = True
            record(user.username, "user.mfa_enrolled", user.username, {})
            messages.success(request, "Two-factor login is set up.")
            return redirect("home")
    grouped = " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))
    return render(request, "accounts/mfa_setup.html", {
        "form": form, "secret": grouped, "uri": totp.provisioning_uri(secret, user.username)})


def mfa_verify(request):
    user = request.user
    if not user.mfa_confirmed:
        return redirect("accounts:mfa_setup")
    form = CodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        key = f"mfa-fail:{user.pk}"
        step = totp.verify(cipher().decrypt(user.mfa_secret_enc), form.cleaned_data["code"],
                           last_used_step=user.mfa_last_step)
        if step is None:
            failures = cache.get(key, 0) + 1
            cache.set(key, failures, settings.LOGIN_LOCKOUT_SECONDS)
            record(user.username, "user.mfa_failed", user.username, {"failures": failures})
            if failures >= settings.LOGIN_MAX_FAILURES:
                cache.set(_fail_key(user.username), settings.LOGIN_MAX_FAILURES, settings.LOGIN_LOCKOUT_SECONDS)
                logout(request)
                return redirect("accounts:login")
            form.add_error("code", "That code is wrong or has already been used.")
        else:
            cache.delete(key)
            user.mfa_last_step = step
            user.save(update_fields=["mfa_last_step"])
            request.session[MFA_SESSION_KEY] = True
            return redirect(_safe_next(request))
    return render(request, "accounts/mfa_verify.html", {"form": form})


@require_POST
def logout_view(request):
    if request.user.is_authenticated:
        record(request.user.username, "user.logout", request.user.username, {})
    logout(request)
    return redirect("accounts:login")


def forbidden(request, exception=None):
    return render(request, "403.html", status=403)
