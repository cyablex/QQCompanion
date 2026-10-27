"""Contact-scoped task phase revisions for invalidating an older chat draft.

The SQL triggers cover direct domain writes as well as the normal task helpers.
Bookkeeping timestamps and fine-grained progress do not change the phase epoch.
This is separate from the existing configuration and memory-generation guards.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS task_epochs(
    contact TEXT PRIMARY KEY, version INTEGER NOT NULL DEFAULT 0);

CREATE TRIGGER IF NOT EXISTS task_epoch_turn_insert AFTER INSERT ON turn_tasks
WHEN NEW.contact IS NOT NULL
BEGIN
    INSERT INTO task_epochs(contact,version) VALUES(NEW.contact,1)
    ON CONFLICT(contact) DO UPDATE SET version=task_epochs.version+1;
END;
CREATE TRIGGER IF NOT EXISTS task_epoch_turn_delete AFTER DELETE ON turn_tasks
WHEN OLD.contact IS NOT NULL
BEGIN
    INSERT INTO task_epochs(contact,version) VALUES(OLD.contact,1)
    ON CONFLICT(contact) DO UPDATE SET version=task_epochs.version+1;
END;
CREATE TRIGGER IF NOT EXISTS task_epoch_turn_update AFTER UPDATE ON turn_tasks
WHEN OLD.contact IS NOT NEW.contact OR OLD.state IS NOT NEW.state
    OR OLD.result IS NOT NEW.result OR OLD.kind IS NOT NEW.kind
    OR OLD.owner IS NOT NEW.owner OR OLD.bot IS NOT NEW.bot
    OR OLD.generation IS NOT NEW.generation
BEGIN
    INSERT INTO task_epochs(contact,version)
    SELECT NEW.contact,1 WHERE NEW.contact IS NOT NULL
    ON CONFLICT(contact) DO UPDATE SET version=task_epochs.version+1;
    INSERT INTO task_epochs(contact,version)
    SELECT OLD.contact,1 WHERE OLD.contact IS NOT NULL AND OLD.contact IS NOT NEW.contact
    ON CONFLICT(contact) DO UPDATE SET version=task_epochs.version+1;
END;

CREATE TRIGGER IF NOT EXISTS task_epoch_request_insert AFTER INSERT ON song_requests
WHEN NEW.contact IS NOT NULL
BEGIN
    INSERT INTO task_epochs(contact,version) VALUES(NEW.contact,1)
    ON CONFLICT(contact) DO UPDATE SET version=task_epochs.version+1;
END;
CREATE TRIGGER IF NOT EXISTS task_epoch_request_delete AFTER DELETE ON song_requests
WHEN OLD.contact IS NOT NULL
BEGIN
    INSERT INTO task_epochs(contact,version) VALUES(OLD.contact,1)
    ON CONFLICT(contact) DO UPDATE SET version=task_epochs.version+1;
END;
CREATE TRIGGER IF NOT EXISTS task_epoch_request_update AFTER UPDATE ON song_requests
WHEN OLD.contact IS NOT NEW.contact OR OLD.state IS NOT NEW.state
    OR OLD.result IS NOT NEW.result OR OLD.song_id IS NOT NEW.song_id
    OR OLD.owner IS NOT NEW.owner OR OLD.bot IS NOT NEW.bot
    OR OLD.requester IS NOT NEW.requester OR OLD.generation IS NOT NEW.generation
BEGIN
    INSERT INTO task_epochs(contact,version)
    SELECT NEW.contact,1 WHERE NEW.contact IS NOT NULL
    ON CONFLICT(contact) DO UPDATE SET version=task_epochs.version+1;
    INSERT INTO task_epochs(contact,version)
    SELECT OLD.contact,1 WHERE OLD.contact IS NOT NULL AND OLD.contact IS NOT NEW.contact
    ON CONFLICT(contact) DO UPDATE SET version=task_epochs.version+1;
END;

CREATE TRIGGER IF NOT EXISTS task_epoch_song_update AFTER UPDATE ON songs
WHEN OLD.state IS NOT NEW.state OR OLD.error IS NOT NEW.error
    OR OLD.title IS NOT NEW.title OR OLD.artist IS NOT NEW.artist
    OR OLD.file IS NOT NEW.file
BEGIN
    INSERT INTO task_epochs(contact,version)
    SELECT DISTINCT contact,1 FROM song_requests WHERE song_id=NEW.id AND contact IS NOT NULL
    ON CONFLICT(contact) DO UPDATE SET version=task_epochs.version+1;
END;
CREATE TRIGGER IF NOT EXISTS task_epoch_song_delete AFTER DELETE ON songs
BEGIN
    INSERT INTO task_epochs(contact,version)
    SELECT DISTINCT contact,1 FROM song_requests WHERE song_id=OLD.id AND contact IS NOT NULL
    ON CONFLICT(contact) DO UPDATE SET version=task_epochs.version+1;
END;
"""


def version(store, contact):
    """Read an epoch without creating state, including on read-only connections."""
    row = store.db.execute('SELECT version FROM task_epochs WHERE contact=?', (contact,)).fetchone()
    return int(row[0]) if row else 0


def unchanged(store, contact, captured):
    return captured == version(store, contact)
