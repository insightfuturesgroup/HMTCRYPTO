"""Structured application logging with a final fail-closed redaction step."""

from __future__ import annotations

import logging as stdlib_logging
import sys
from collections.abc import Mapping
from threading import RLock
from types import TracebackType
from typing import IO, cast

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from hmt_crypto.persistence.redaction import redact, safe_exception_summary

_LOGGER_NAMESPACE = "hmt_crypto"
_HANDLER_MARKER = "_hmt_crypto_handler"
_CONFIGURATION_LOCK = RLock()
_CONFIGURED_COMPONENTS: set[str] = set()


def _component_processor(
    logger: WrappedLogger, _method_name: str, event_dict: EventDict
) -> EventDict:
    name = getattr(logger, "name", _LOGGER_NAMESPACE)
    component = name.removeprefix(f"{_LOGGER_NAMESPACE}.")
    event_dict.setdefault(
        "component", component if component != _LOGGER_NAMESPACE else "application"
    )
    return event_dict


def _exception_from_info(value: object) -> BaseException | None:
    if isinstance(value, BaseException):
        return value
    if value is True:
        current = sys.exc_info()[1]
        return current if isinstance(current, BaseException) else None
    if isinstance(value, tuple) and len(value) == 3:
        exception = value[1]
        traceback = value[2]
        if (traceback is None or isinstance(traceback, TracebackType)) and isinstance(
            exception, BaseException
        ):
            return exception
    return None


def _safe_exception_processor(
    _logger: WrappedLogger, _method_name: str, event_dict: EventDict
) -> EventDict:
    exc_info = event_dict.pop("exc_info", None)
    exception = _exception_from_info(exc_info)
    if exception is not None:
        event_dict["error"] = safe_exception_summary(exception)
    return event_dict


def _redaction_processor(
    _logger: WrappedLogger, _method_name: str, event_dict: EventDict
) -> EventDict:
    redacted = redact(dict(event_dict))
    if not isinstance(redacted, dict):
        return {"event": "<invalid-log-event>"}
    return cast(EventDict, redacted)


def _coerce_level(level: str | int) -> int:
    if isinstance(level, int):
        return level
    candidate = stdlib_logging.getLevelNamesMapping().get(level.upper())
    if candidate is None:
        raise ValueError(f"Unknown log level: {level}")
    return candidate


def _shared_processors() -> list[Processor]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
        _component_processor,
        _safe_exception_processor,
        _redaction_processor,
    ]


def configure_logging(
    *,
    json_output: bool = True,
    default_level: str | int = "INFO",
    component_levels: Mapping[str, str | int] | None = None,
    stream: IO[str] | None = None,
) -> None:
    """Configure one idempotent handler for HMT-Crypto structured logs."""
    renderer: Processor = (
        structlog.processors.JSONRenderer(sort_keys=True)
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    with _CONFIGURATION_LOCK:
        application_logger = stdlib_logging.getLogger(_LOGGER_NAMESPACE)
        for handler in tuple(application_logger.handlers):
            if getattr(handler, _HANDLER_MARKER, False):
                application_logger.removeHandler(handler)
                handler.close()

        for component in _CONFIGURED_COMPONENTS:
            stdlib_logging.getLogger(f"{_LOGGER_NAMESPACE}.{component}").setLevel(
                stdlib_logging.NOTSET
            )
        _CONFIGURED_COMPONENTS.clear()

        handler = stdlib_logging.StreamHandler(stream)
        setattr(handler, _HANDLER_MARKER, True)
        handler.setLevel(stdlib_logging.NOTSET)
        handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                foreign_pre_chain=_shared_processors(),
                processors=[
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    _redaction_processor,
                    renderer,
                ],
            )
        )
        application_logger.handlers.clear()
        application_logger.addHandler(handler)
        application_logger.setLevel(_coerce_level(default_level))
        application_logger.propagate = False
        application_logger.disabled = False

        for component, level in (component_levels or {}).items():
            normalized = component.strip().strip(".")
            if not normalized:
                raise ValueError("Component log-level names must not be empty")
            stdlib_logging.getLogger(f"{_LOGGER_NAMESPACE}.{normalized}").setLevel(
                _coerce_level(level)
            )
            _CONFIGURED_COMPONENTS.add(normalized)

        structlog.configure(
            processors=[
                *_shared_processors(),
                structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
            ],
            wrapper_class=structlog.stdlib.BoundLogger,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=False,
        )


def get_logger(component: str) -> structlog.stdlib.BoundLogger:
    """Return a namespaced logger whose records include ``component``."""
    normalized = component.strip().strip(".")
    if not normalized:
        raise ValueError("Logger component must not be empty")
    logger = structlog.get_logger(f"{_LOGGER_NAMESPACE}.{normalized}")
    return cast(structlog.stdlib.BoundLogger, logger)


def bind_correlation_id(correlation_id: str) -> None:
    """Bind a correlation identifier to the current async context."""
    if not correlation_id.strip():
        raise ValueError("Correlation ID must not be empty")
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)


def clear_correlation_id() -> None:
    """Remove the correlation identifier from the current async context."""
    structlog.contextvars.unbind_contextvars("correlation_id")
