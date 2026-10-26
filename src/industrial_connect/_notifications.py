from queue import Queue
from threading import Lock


class Notifications[EventT]:
    """Bounded, nonblocking producer queue. Overflow drops the newest event."""

    def __init__(self, capacity: int):
        if type(capacity) is not int or capacity < 1:
            raise ValueError("Notification capacity must be positive")
        self.notifications: Queue[EventT] = Queue(maxsize=capacity)
        self.dropped_count = 0
        self._notification_lock = Lock()

    def _emit(self, event: EventT) -> None:
        from queue import Full

        try:
            self.notifications.put_nowait(event)
        except Full:
            with self._notification_lock:
                self.dropped_count += 1

    def get_notification(self, timeout: float | None = None) -> EventT:
        """Return the next event; raise queue.Empty when a supplied timeout expires."""
        return self.notifications.get(timeout=timeout)
