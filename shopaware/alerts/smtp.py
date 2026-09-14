from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage

from shopaware.alerts.base import AlertEvent


class SMTPProvider:
    def send(self, event: AlertEvent) -> str:
        host = os.getenv('SMTP_HOST', '').strip()
        username = os.getenv('SMTP_USERNAME', '').strip()
        password = os.getenv('SMTP_PASSWORD', '')
        sender = os.getenv('SMTP_FROM', username).strip()
        recipient = os.getenv('SMTP_TO', '').strip()
        if not all([host, sender, recipient]):
            return 'disabled'
        message = EmailMessage()
        message['From'], message['To'] = sender, recipient
        message['Subject'] = f'ShopAware alert: {event.event_type} on {event.camera_name}'
        message.set_content(f'Camera: {event.camera_name}\nEvent: {event.event_type}\n'
                            f'Heuristic risk score: {event.risk_score * 100:.0f}/100\n'
                            f'Incident: {event.incident_id}\nDetails: {event.message}\n\n'
                            'Automated candidate. Human review required. This is not proof of theft.')
        if event.snapshot.is_file():
            message.add_attachment(event.snapshot.read_bytes(), maintype='image', subtype='jpeg', filename=event.snapshot.name)
        with smtplib.SMTP(host, int(os.getenv('SMTP_PORT', '587')), timeout=20) as server:
            server.starttls(context=ssl.create_default_context())
            if username:
                server.login(username, password)
            server.send_message(message)
        return 'sent'
