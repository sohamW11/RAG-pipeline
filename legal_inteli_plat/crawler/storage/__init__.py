"""Storage backends and factory.

Concrete implementations live here; the abstract contract lives in
``crawler.interfaces.storage``. Import backends from this package.
"""

from crawler.interfaces.storage import StorageInterface, StoredObject
from crawler.storage.factory import create_storage
from crawler.storage.local import LocalStorage

__all__ = [
    "StorageInterface",
    "StoredObject",
    "LocalStorage",
    "create_storage",
]
