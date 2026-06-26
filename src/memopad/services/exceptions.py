class FileOperationError(Exception):
    """Raised when file operations fail"""

    pass


class EntityNotFoundError(Exception):
    """Raised when an entity cannot be found"""

    pass


class EntityCreationError(Exception):
    """Raised when an entity cannot be created"""

    pass


class EntityUpdateError(Exception):
    """Raised when an entity exists but cannot be updated (e.g. DB update is a no-op
    for a record that should be present).

    Distinct from EntityCreationError so callers can distinguish "create failed"
    from "update failed" from "bad input" (the latter remains ValueError).
    """

    pass


class EntityAlreadyExistsError(EntityCreationError):
    """Raised when an entity would collide with an existing one (duplicate title
    or permalink) and the caller asked for a strict create.

    Subclasses EntityCreationError so existing code/tests that catch
    EntityCreationError for the duplicate-create case keep working, while
    the router and clients can branch on this more specific type to map it to
    HTTP 409 (instead of the previous behavior of leaking as a 500). Lets
    higher layers (e.g. daily_note, write_note, assimilate) branch on 'already
    exists' without string-matching exception messages or HTTP status codes.
    """

    pass


class SyncFatalError(Exception):
    """Raised when sync encounters a fatal error that prevents continuation.

    Fatal errors include:
    - Project deleted during sync (FOREIGN KEY constraint)
    - Database corruption
    - Critical system failures

    When this exception is raised, the entire sync operation should be terminated
    immediately rather than attempting to continue with remaining files.
    """

    pass
