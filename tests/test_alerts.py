from unittest.mock import MagicMock
from shopaware.alerts.base import AlertDispatcher, AlertEvent
from shopaware.alerts.smtp import SMTPProvider


def test_smtp_subject_context_attachment_and_starttls(tmp_path, monkeypatch):
    import smtplib
    for key, value in {'SMTP_HOST':'smtp.test.invalid', 'SMTP_FROM':'from@example.test',
                       'SMTP_TO':'to@example.test', 'SMTP_USERNAME':'user', 'SMTP_PASSWORD':'synthetic-pass'}.items():
        monkeypatch.setenv(key, value)
    server = MagicMock()
    server.__enter__.return_value = server
    monkeypatch.setattr(smtplib, 'SMTP', MagicMock(return_value=server))
    image = tmp_path / 'snapshot.jpg'
    image.write_bytes(b'jpeg')
    event = AlertEvent('i', 'Aisle', 'suspected_concealment', 'review', .8, image)
    assert SMTPProvider().send(event) == 'sent'
    message = server.send_message.call_args.args[0]
    assert message['Subject'] == 'ShopAware alert: suspected_concealment on Aisle'
    assert '80/100' in message.get_body().get_content()
    assert len(list(message.iter_attachments())) == 1
    assert server.starttls.called


def test_provider_failure_redacts_and_dispatcher_drains(tmp_path, caplog):
    class Provider:
        def send(self, event):
            raise RuntimeError('smtp secret password')
    statuses = []
    dispatcher = AlertDispatcher(Provider(), lambda i,s: statuses.append((i,s)))
    assert dispatcher.enqueue(AlertEvent('i','A','high_risk_activity','review',.7,tmp_path/'missing'))
    dispatcher.close()
    assert statuses == [('i','failed')]
    assert 'smtp secret password' not in caplog.text
    assert not dispatcher.thread.is_alive()
