"""Typer entry point for Phase 1 read-only Delta diagnostics and market data."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable, Coroutine, Sequence
from typing import Annotated, Any
from uuid import UUID

import typer
from pydantic import ValidationError
from typer.core import TyperGroup

from hmt_crypto import cli_runtime
from hmt_crypto.errors import HmtCryptoError
from hmt_crypto.logging import configure_logging
from hmt_crypto.persistence.redaction import JsonValue


class _JsonBoundaryGroup(TyperGroup):
    """Normalize parser failures without rendering caller-controlled command text."""

    def main(
        self,
        args: Sequence[str] | None = None,
        prog_name: str | None = None,
        complete_var: str | None = None,
        standalone_mode: bool = True,
        windows_expand_args: bool = True,
        **extra: Any,
    ) -> Any:
        try:
            result = super().main(
                args=args,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=False,
                windows_expand_args=windows_expand_args,
                **extra,
            )
        except typer.TyperException:
            _emit({"code": "invalid_input", "message": "Command input is invalid"}, error=True)
            if standalone_mode:
                raise SystemExit(2) from None
            return 2
        if standalone_mode and isinstance(result, int):
            raise SystemExit(result)
        return result


app = typer.Typer(
    name="hmt-crypto",
    help="Read-only Phase 1 Delta diagnostics and public market data.",
    cls=_JsonBoundaryGroup,
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    rich_markup_mode=None,
)
products_app = typer.Typer(
    name="products",
    help="Process-local product catalogue.",
    cls=_JsonBoundaryGroup,
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    rich_markup_mode=None,
)
account_app = typer.Typer(
    name="account",
    help="Explicit database account setup.",
    cls=_JsonBoundaryGroup,
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    rich_markup_mode=None,
)
database_app = typer.Typer(
    name="database",
    help="Explicit database lifecycle commands.",
    cls=_JsonBoundaryGroup,
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    rich_markup_mode=None,
)
platform_app = typer.Typer(
    name="platform",
    help="Operator-controlled multi-user platform lifecycle.",
    cls=_JsonBoundaryGroup,
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    rich_markup_mode=None,
)
app.add_typer(products_app, name="products")
app.add_typer(account_app, name="account")
app.add_typer(database_app, name="database")
app.add_typer(platform_app, name="platform")


@app.callback()
def configure_cli() -> None:
    """Keep structured redacted logs on stderr and command results on stdout."""
    configure_logging(json_output=True, stream=sys.stderr)


def _emit(payload: dict[str, JsonValue], *, error: bool = False) -> None:
    typer.echo(
        json.dumps(
            payload, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True
        ),
        err=error,
    )


def _error_payload(error: BaseException) -> dict[str, JsonValue]:
    if isinstance(error, HmtCryptoError):
        return error.to_dict()
    if isinstance(error, (ValidationError, TypeError, ValueError)):
        return {"code": "invalid_input", "message": "Command input is invalid"}
    return {"code": "internal_error", "message": "Command failed safely"}


def _run_sync(action: Callable[[], dict[str, JsonValue]]) -> None:
    try:
        payload = action()
    except Exception as error:
        _emit(_error_payload(error), error=True)
        raise typer.Exit(code=2) from None
    _emit(payload)


def _run_async(action: Callable[[], Coroutine[Any, Any, dict[str, JsonValue]]]) -> None:
    try:
        payload: dict[str, JsonValue] = asyncio.run(action())
    except Exception as error:
        _emit(_error_payload(error), error=True)
        raise typer.Exit(code=2) from None
    _emit(payload)


@app.command("config-check")
def config_check() -> None:
    """Show safe local configuration diagnostics; performs no network I/O."""
    _run_sync(lambda: cli_runtime.config_check_payload(cli_runtime.load_settings()))


@app.command("clock-check")
def clock_check() -> None:
    """Show absent Phase 1 clock/private-probe evidence without network I/O."""
    _run_sync(cli_runtime.clock_check_payload)


@account_app.command("bootstrap")
def account_bootstrap(
    profile_name: Annotated[str, typer.Option("--profile")] = "paper-primary",
    confirm_locked_live: Annotated[bool, typer.Option("--confirm-locked-live")] = False,
) -> None:
    """Create a credential-free profile; LIVE must be explicitly confirmed locked."""
    _run_async(
        lambda: cli_runtime.account_bootstrap_payload(
            cli_runtime.load_settings(),
            profile_name=profile_name,
            confirm_locked_live=confirm_locked_live,
        )
    )


@database_app.command("init-dev")
def database_init_dev() -> None:
    """Create current tables for single-process SQLite development only."""
    _run_async(lambda: cli_runtime.database_init_dev_payload(cli_runtime.load_settings()))


@platform_app.command("bootstrap-admin")
def platform_bootstrap_admin(
    auth_user_id: Annotated[UUID, typer.Option("--auth-user-id")],
    email: Annotated[str, typer.Option("--email")],
    full_name: Annotated[str, typer.Option("--name")],
) -> None:
    """Bind the first Supabase Auth user to the sole initial admin profile."""
    _run_async(
        lambda: cli_runtime.platform_admin_bootstrap_payload(
            auth_user_id=auth_user_id,
            email=email,
            full_name=full_name,
        )
    )


@products_app.command("sync")
def products_sync() -> None:
    """Fetch one complete process-local product catalogue."""
    _run_async(lambda: cli_runtime.products_sync_payload(cli_runtime.load_settings()))


@products_app.command("show")
def products_show(
    product_id: Annotated[str | None, typer.Option("--id")] = None,
    symbol: Annotated[str | None, typer.Option("--symbol")] = None,
) -> None:
    """Refresh then show exactly one product by numeric ID or symbol."""
    _run_async(
        lambda: cli_runtime.product_show_payload(
            cli_runtime.load_settings(), product_id=product_id, symbol=symbol
        )
    )


@app.command("ticker")
def ticker(symbol: Annotated[str | None, typer.Argument()] = None) -> None:
    """Show one normalized public ticker."""
    _run_async(lambda: cli_runtime.ticker_payload(cli_runtime.load_settings(), symbol=symbol))


@app.command("candles")
def candles(
    symbol: Annotated[str | None, typer.Argument()] = None,
    resolution: Annotated[str | None, typer.Option("--resolution")] = None,
    start: Annotated[str | None, typer.Option("--start")] = None,
    end: Annotated[str | None, typer.Option("--end")] = None,
) -> None:
    """Show one normalized public candle interval using aware UTC inputs."""
    _run_async(
        lambda: cli_runtime.candles_payload(
            cli_runtime.load_settings(),
            symbol=symbol,
            resolution=resolution,
            start=start,
            end=end,
        )
    )


def main() -> None:
    """Run the installed command-line application."""
    app()


if __name__ == "__main__":  # pragma: no cover - console entry point covers this boundary
    main()
