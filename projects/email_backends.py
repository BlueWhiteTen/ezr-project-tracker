"""
A Django email backend that sends via Resend's HTTPS API instead of raw SMTP.

Why this exists: Railway (and several other PaaS hosts) block outbound raw
SMTP connections at the network level, for anti-spam reasons — this affects
ALL ports (25, 465, 587 alike), not just the default 587. Confirmed on this
deployment via a direct connection test: `OSError: [Errno 101] Network is
unreachable` when connecting to smtp.gmail.com. That's a network-level
block, not a credentials problem, so no SMTP port/config change fixes it.

Resend (and other transactional-email providers) send over HTTPS instead —
port 443, which is never blocked — sidestepping the problem entirely rather
than fighting it.

Built against the stdlib only (urllib), not the `requests` package or
Resend's own SDK, so there's nothing new to add to requirements.txt or
risk failing to install on deploy.

Usage: set RESEND_API_KEY as an environment variable. When it's set,
settings.py switches EMAIL_BACKEND to this one automatically — no code
changes needed elsewhere, since it implements Django's standard email
backend interface. Every existing `send_mail(...)` call in the app (like
the password reset flow) keeps working exactly as before.
"""
import json
import socket
import urllib.request
import urllib.error
from django.core.mail.backends.base import BaseEmailBackend
from django.conf import settings


def _force_ipv4(fn):
    """Temporarily make getaddrinfo return IPv4 addresses only, for the
    duration of the wrapped call. Needed because this container has no IPv6
    route — Python's default connection logic tries IPv6 first via
    getaddrinfo and fails outright (Errno 101, Network unreachable) rather
    than falling back to IPv4, even though a direct IPv4 connection works
    fine. Confirmed via a live test: connecting to the resolved IPv4 address
    directly succeeds, only the hostname-based connection through the
    default (IPv6-first) resolution fails."""
    def wrapper(*args, **kwargs):
        original_getaddrinfo = socket.getaddrinfo

        def ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
            return original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

        socket.getaddrinfo = ipv4_only_getaddrinfo
        try:
            return fn(*args, **kwargs)
        finally:
            socket.getaddrinfo = original_getaddrinfo
    return wrapper


class ResendEmailBackend(BaseEmailBackend):
    API_URL = 'https://api.resend.com/emails'

    @_force_ipv4
    def _post(self, req):
        return urllib.request.urlopen(req, timeout=10)

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        api_key = getattr(settings, 'RESEND_API_KEY', '')
        if not api_key:
            if not self.fail_silently:
                raise ValueError('RESEND_API_KEY is not set.')
            return 0

        sent_count = 0
        for message in email_messages:
            payload = {
                'from': message.from_email,
                'to': list(message.to),
                'subject': message.subject,
                'text': message.body,
            }
            if message.cc:
                payload['cc'] = list(message.cc)
            if message.bcc:
                payload['bcc'] = list(message.bcc)

            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(
                self.API_URL, data=data, method='POST',
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'Content-Type': 'application/json',
                },
            )
            try:
                with self._post(req) as resp:
                    if 200 <= resp.status < 300:
                        sent_count += 1
                    elif not self.fail_silently:
                        raise RuntimeError(f'Resend API returned status {resp.status}')
            except urllib.error.HTTPError as e:
                body = e.read().decode('utf-8', errors='replace')
                if not self.fail_silently:
                    raise RuntimeError(f'Resend API error {e.code}: {body}')
            except urllib.error.URLError as e:
                if not self.fail_silently:
                    raise RuntimeError(f'Could not reach Resend API: {e.reason}')
        return sent_count
