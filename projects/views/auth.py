import math
import random
from django.utils import timezone
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.db.models import Q, F as models_F, Count
from django.views.decorators.http import require_POST
from datetime import date, timedelta
import json

from ..models import Project, ProjectLog, Customer, Comment, Message, Notification, TeamMessage, StaffProfile, LeaveRequest, InstallationReport, ReportPhoto, SatisfactionNote, CustomerProfile, ProjectDocument, Product, PickingList, PickingListItem, PickingTemplate, PickingTemplateItem, MaterialPrice, ProjectCost, ProjectCostLine, UprightAccessory, AccessoryOverride, Reminder, FittingCrew, Supplier, PurchaseOrder, PurchaseOrderLine, StockMovement, FittingNote, ProjectQuote, PriceListItem, QuotePhoto, QuoteAttachedPhoto, ProformaInvoice, DeliveryPhase, ProjectPresence
from ..forms import RegisterForm, ProjectForm


# ── Auth ──────────────────────────────────────────────────────────────────────

from django.contrib.auth.views import (
    PasswordResetView, PasswordResetDoneView,
    PasswordResetConfirmView, PasswordResetCompleteView,
)

def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    error = None
    if request.method == 'POST':
        identifier = request.POST.get('identifier', '').strip()
        password   = request.POST.get('password', '')
        user = None
        try:
            u = User.objects.get(email=identifier)
            user = authenticate(request, username=u.username, password=password)
        except User.DoesNotExist:
            for u in User.objects.all():
                if u.get_full_name().lower() == identifier.lower():
                    user = authenticate(request, username=u.username, password=password)
                    break
        if user:
            login(request, user)
            return redirect('dashboard')
        else:
            # Check if account exists but is inactive (pending approval)
            try:
                u = User.objects.get(email=identifier)
                if not u.is_active:
                    error = 'Your account is pending approval. Please contact your manager.'
                else:
                    error = 'Invalid name/email or password.'
            except User.DoesNotExist:
                error = 'Invalid name/email or password.'
    return render(request, 'projects/login.html', {'error': error})




def register_view(request):
    form = RegisterForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        return render(request, 'projects/register_pending.html', {'user': user})
    return render(request, 'projects/register.html', {'form': form})




def logout_view(request):
    logout(request)
    return redirect('login')


# ── Dashboard ─────────────────────────────────────────────────────────────────



def create_superuser_once(request):
    from django.http import HttpResponse
    from django.contrib.auth.models import User
    from django.middleware.csrf import get_token
    if User.objects.filter(is_superuser=True).exists():
        return HttpResponse("Superuser already exists.", status=403)
    token = get_token(request)
    if request.method == 'POST':
        email = request.POST.get('email','').strip()
        password = request.POST.get('password','').strip()
        if email and password:
            try:
                user = User.objects.filter(username=email).first()
                if user:
                    user.is_active = True
                    user.is_staff = True
                    user.is_superuser = True
                    user.set_password(password)
                    user.save()
                else:
                    parts = email.split('@')[0].split('.')
                    user = User.objects.create_superuser(
                        username=email,
                        email=email,
                        password=password,
                        first_name=parts[0].title() if parts else '',
                        last_name=parts[1].title() if len(parts)>1 else '',
                    )
                return HttpResponse(f"<h2>✅ Done! <a href='/login/'>Click here to log in</a></h2>")
            except Exception as e:
                return HttpResponse(f"<h2>Error: {e}</h2>", status=500)
    html = f"""<!DOCTYPE html><html><head><title>Setup</title>
    <style>body{{font-family:Arial;background:#1a1a1a;color:#fff;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
    .b{{background:#2a2a2a;border:1px solid #444;border-radius:10px;padding:2rem;width:340px}}
    h2{{color:#f97316;margin:0 0 1rem}}input{{width:100%;padding:.6rem;margin-bottom:.8rem;border:1px solid #555;border-radius:6px;background:#333;color:#fff;font-size:.95rem;box-sizing:border-box}}
    button{{width:100%;padding:.7rem;background:#f97316;border:none;border-radius:6px;color:#fff;font-weight:700;cursor:pointer;font-size:1rem}}</style>
    </head><body><div class="b"><h2>⚙ Create Admin Account</h2>
    <form method="post"><input type="hidden" name="csrfmiddlewaretoken" value="{token}">
    <input type="email" name="email" placeholder="Your email" required>
    <input type="password" name="password" placeholder="Password" required>
    <button type="submit">Create</button></form>
    <p style="font-size:.75rem;color:#888;margin-top:.8rem">⚠ Disables after first use</p>
    </div></body></html>"""
    return HttpResponse(html)


# ── Reminders ─────────────────────────────────────────────────────────────────


# ── Password reset (class-based) ───────────────────────────────────────────────
from django.contrib.auth.views import (
    PasswordResetView, PasswordResetDoneView,
    PasswordResetConfirmView, PasswordResetCompleteView,
)
import smtplib, socket


class SafePasswordResetView(PasswordResetView):
    """If the email genuinely can't be sent (SMTP unreachable, timed out, bad
    credentials), show a clear error instead of a blank hang or a raw 500 —
    the same failure that used to leave the page stuck loading."""
    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except (smtplib.SMTPException, socket.error, socket.timeout, TimeoutError) as e:
            form.add_error(None, "Couldn't send the reset email right now — the mail server didn't respond. Please try again shortly, or contact Tasos if this keeps happening.")
            return self.form_invalid(form)


password_reset_request = SafePasswordResetView.as_view(
    template_name='projects/password_reset.html',
    email_template_name='projects/password_reset_email.txt',
    subject_template_name='projects/password_reset_subject.txt',
    success_url='/password-reset/done/',
)
password_reset_done = PasswordResetDoneView.as_view(
    template_name='projects/password_reset_done.html',
)
password_reset_confirm = PasswordResetConfirmView.as_view(
    template_name='projects/password_reset_confirm.html',
    success_url='/password-reset/complete/',
)
password_reset_complete = PasswordResetCompleteView.as_view(
    template_name='projects/password_reset_complete.html',
)
