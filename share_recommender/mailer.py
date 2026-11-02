"""Send email over SMTP with STARTTLS (standard library only).

Gmail works with an app password (https://myaccount.google.com/apppasswords), which needs
2-step verification: SMTP_HOST=smtp.gmail.com, SMTP_PORT=587, SMTP_USER=your address.
"""

import smtplib
import ssl
from email.message import EmailMessage
from typing import Protocol


class EmailSender(Protocol):
    def send(self, to: str, subject: str, text: str, html: str) -> None: ...


class SmtpSender:
    def __init__(self, host: str, port: int, user: str, password: str, sender: str):
        self.host, self.port, self.user, self.sender = host, port, user, sender
        self.password = password.replace(" ", "")  # Google shows app passwords in groups of 4

    def send(self, to: str, subject: str, text: str, html: str) -> None:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.sender
        msg["To"] = to
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")
        with smtplib.SMTP(self.host, self.port, timeout=30) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(self.user, self.password)
            smtp.send_message(msg)


class PrintSender:
    """For --dry-run: show the email instead of sending it."""

    def send(self, to: str, subject: str, text: str, html: str) -> None:
        print(f"--- To: {to}\n--- Subject: {subject}\n{text}\n", flush=True)
