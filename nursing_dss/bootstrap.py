"""Wire the whole application together in one place."""
from typing import Optional

from . import config
from .database import Database
from .database.seed import seed_demo
from .output.notifier import Notifier, SimulatedNotifier
from .routing import RoutingSystem


def create_system(db_path: Optional[str] = None, seed_if_empty: bool = True,
                  notifier: Optional[Notifier] = None) -> RoutingSystem:
    """Open (and migrate) the database, optionally seed demo data into an empty
    one, and return the RoutingSystem that everything else connects to."""
    db = Database(db_path or config.DEFAULT_DB_PATH)
    if seed_if_empty and not db.list_units():
        seed_demo(db)
    return RoutingSystem(db, notifier or SimulatedNotifier())
