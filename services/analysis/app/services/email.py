"""Transactional email delivery for account security messages."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from html import escape

from app.config import Settings

logger = logging.getLogger(__name__)


def send_password_reset_email(settings: Settings, recipient: str, name: str, reset_url: str) -> bool:
    """Send a password reset message through the configured SMTP service.

    Returns False when email is not configured or delivery fails. The caller
    deliberately returns the same public response in every case to prevent
    account enumeration.
    """

    if not settings.smtp_host or not settings.smtp_from_email:
        logger.warning("password reset email requested but SMTP is not configured")
        return False

    display_name = name.strip() or "there"
    subject = "Reset your Kale Forge password"
    text = (
        f"Hi {display_name},\n\n"
        "We received a request to reset your Kale Forge password.\n\n"
        f"Reset your password: {reset_url}\n\n"
        f"This link expires in {settings.password_reset_minutes} minutes and can be used once. "
        "If you did not request this, you can ignore this email.\n\n"
        "Kale Forge"
    )
    html = f"""\
<!doctype html>
<html lang="en">
  <body style="margin:0;background:#f4f7f5;color:#15211b;font-family:Arial,sans-serif">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
      <tr><td align="center" style="padding:36px 16px">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
               style="max-width:560px;background:white;border:1px solid #dfe8e2;border-radius:16px">
          <tr><td style="padding:34px">
            <div style="font-size:14px;font-weight:700;color:#287552;letter-spacing:.08em">KALE FORGE</div>
            <h1 style="margin:22px 0 10px;font-size:26px">Reset your password</h1>
            <p style="line-height:1.6;color:#52615a">Hi {escape(display_name)}, we received a request
              to reset the password for your Kale Forge account.</p>
            <p style="margin:28px 0">
              <a href="{escape(reset_url, quote=True)}"
                 style="display:inline-block;padding:13px 20px;border-radius:9px;background:#287552;
                        color:white;text-decoration:none;font-weight:700">Choose a new password</a>
            </p>
            <p style="font-size:13px;line-height:1.6;color:#69766f">
              This secure link expires in {settings.password_reset_minutes} minutes and works once.
              If you did not request it, you can safely ignore this message.
            </p>
          </td></tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>"""

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((settings.smtp_from_name, settings.smtp_from_email))
    message["To"] = recipient
    message.set_content(text)
    message.add_alternative(html, subtype="html")

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as client:
            client.ehlo()
            if settings.smtp_starttls:
                client.starttls()
                client.ehlo()
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(message)
        return True
    except (OSError, smtplib.SMTPException):
        logger.exception("password reset email delivery failed")
        return False
