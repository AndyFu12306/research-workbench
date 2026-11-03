"""Persistence failures exposed without SQL parameters or research content."""


class PersistenceError(RuntimeError):
    """An operation could not be committed; the original exception is its cause."""


class MigrationError(PersistenceError):
    """Schema initialization or migration failed."""


class EntityNotFoundError(LookupError):
    def __init__(self, entity: str, entity_id: int) -> None:
        self.entity = entity
        self.entity_id = entity_id
        super().__init__(f"{entity} {entity_id} does not exist.")
