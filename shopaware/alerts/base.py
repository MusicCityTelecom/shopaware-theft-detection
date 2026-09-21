from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol


@dataclass(frozen=True)
class AlertEvent:
    incident_id: str
    camera_name: str
    event_type: str
    message: str
    risk_score: float
    snapshot: Path


class AlertProvider(Protocol):
    def send(self, event: AlertEvent) -> str: ...


class AlertDispatcher:
    def __init__(self, provider: AlertProvider, on_status: Callable[[str, str], None], capacity: int = 128):
        self.provider, self.on_status = provider, on_status
        self.queue: queue.Queue = queue.Queue(maxsize=capacity)
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.closed = False
        self.status = 'idle'

    def enqueue(self, event: AlertEvent) -> bool:
        with self.lock:
            if self.closed:
                return False
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name='alerts', daemon=True)
                self.thread.start()
            try:
                self.queue.put_nowait(event)
                return True
            except queue.Full:
                self.status = 'queue_full'
                return False

    def _run(self):
        while True:
            event = self.queue.get()
            try:
                if event is None:
                    return
                try:
                    self.status = self.provider.send(event)
                except Exception:
                    # Providers may throw sensitive SMTP authentication responses.
                    logging.getLogger(__name__).error('Alert delivery failed', extra={'incident_id': event.incident_id})
                    self.status = 'failed'
                try:
                    self.on_status(event.incident_id, self.status)
                except Exception:
                    logging.getLogger(__name__).error('Alert status persistence failed')
            finally:
                self.queue.task_done()

    def close(self):
        with self.lock:
            self.closed = True
        if self.thread:
            self.queue.put(None)
            self.thread.join()
