"""Safe database failures, excluding raw SQL, parameter values and driver text."""

from sqlalchemy.exc import DontWrapMixin

from hmt_crypto.errors import HmtCryptoError


class PersistenceError(HmtCryptoError):
    """A database operation failed without retaining unsafe driver diagnostics."""

    _code = "persistence_error"
    _message = "Database operation failed"


class PersistenceValueError(PersistenceError, DontWrapMixin):
    """A value was rejected before persistence or while validating stored data."""

    _code = "persistence_value_error"
    _message = "Database value is invalid"
