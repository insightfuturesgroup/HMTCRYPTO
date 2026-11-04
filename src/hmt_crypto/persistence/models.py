"""Explicit aggregate import for schema tooling; never creates an engine or DB.

Append-only grants/triggers and immutable-scope/version enforcement are deferred
to later reviewed migration units. Import this module before inspecting metadata.
"""

from hmt_crypto.persistence.catalogue_models import CatalogueProduct, CatalogueSnapshot
from hmt_crypto.persistence.db import Base
from hmt_crypto.persistence.identity_models import Account, CredentialVersion
from hmt_crypto.persistence.signal_models import Signal

metadata = Base.metadata

__all__ = [
    "Account",
    "CredentialVersion",
    "CatalogueSnapshot",
    "CatalogueProduct",
    "Signal",
    "metadata",
]
