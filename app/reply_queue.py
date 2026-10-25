"""Small persistent retry queue for recent, definitely unanswered messages."""

import time


class ReplyQueue:
    def __init__(self, store):
        self.store = store

    def enqueue(self, contact, event, now=None):
        now = time.time() if now is None else now
        self.store.set(
            f"reply_job:{contact}",
            {
                "event": event,
                "attempts": 0,
                "due": now + 3,
                "expires": now + 900,
            },
        )

    def begin(self, contact):
        job = self.store.get(f"reply_job:{contact}")
        if not job or job["expires"] < time.time() or job["attempts"] >= 3:
            self.clear(contact)
            return None
        job["attempts"] += 1
        self.store.set(f"reply_job:{contact}", job)
        return job

    def finish(self, contact, event):
        job = self.store.get(f"reply_job:{contact}")
        if job and job["event"] == event:
            self.clear(contact)

    def retry(self, contact, event, now=None):
        now = time.time() if now is None else now
        key = f"reply_job:{contact}"
        job = self.store.get(key)
        if not job or job["event"] != event:
            return
        if job["attempts"] >= 3 or now > job["expires"]:
            self.clear(contact)
        else:
            job["due"] = now + 60 * job["attempts"]
            self.store.set(key, job)

    def ready(self, now=None):
        now = time.time() if now is None else now
        contacts = []
        for row in self.store.db.execute(
            "SELECT key FROM state WHERE key LIKE 'reply_job:%'"
        ).fetchall():
            contact = row["key"].split(":", 1)[1]
            job = self.store.get(row["key"])
            if job["expires"] < now or job["attempts"] >= 3:
                self.clear(contact)
            elif job["due"] <= now:
                contacts.append(contact)
        return contacts

    def clear(self, contact):
        self.store.db.execute(
            "DELETE FROM state WHERE key=?", (f"reply_job:{contact}",)
        )
