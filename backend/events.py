"""Server-Sent Events broadcaster. Each browser tab gets its own bounded queue."""
import json
import queue
import threading
import time


class EventBus:
    def __init__(self):
        self._clients = []
        self._lock = threading.Lock()
        self._listeners = []  # in-process callbacks: fn(event, data)

    def subscribe(self):
        q = queue.Queue(maxsize=2000)
        with self._lock:
            self._clients.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._clients:
                self._clients.remove(q)

    def add_listener(self, fn):
        with self._lock:
            self._listeners.append(fn)

    def remove_listener(self, fn):
        with self._lock:
            if fn in self._listeners:
                self._listeners.remove(fn)

    def publish(self, event, data):
        payload = f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
        with self._lock:
            clients = list(self._clients)
            listeners = list(self._listeners)
        for q in clients:
            try:
                q.put_nowait(payload)
            except queue.Full:
                # Slow client: drop its oldest message rather than block acquisition.
                try:
                    q.get_nowait()
                    q.put_nowait(payload)
                except queue.Empty:
                    pass
        for fn in listeners:
            try:
                fn(event, data)
            except Exception:  # listeners must never break acquisition
                pass

    def stream(self, q, heartbeat_s=15):
        last = time.time()
        yield "retry: 2000\n\n"
        while True:
            try:
                msg = q.get(timeout=1.0)
                yield msg
                last = time.time()
            except queue.Empty:
                if time.time() - last > heartbeat_s:
                    yield ": keep-alive\n\n"
                    last = time.time()


bus = EventBus()
