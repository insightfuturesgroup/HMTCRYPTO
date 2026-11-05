"""Isolated integrated scenarios for the static private-read admission boundary."""

from __future__ import annotations

import copy
import inspect
import os
import pickle
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import SecretStr

from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
from hmt_crypto.domain.account import AccountRef
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange import credentials
from hmt_crypto.exchange.delta import private_admission
from hmt_crypto.exchange.delta.private_limiter_registry import (
    _compose_private_limiter_registry,
)
from hmt_crypto.exchange.delta.private_origin import _canonical_private_origin
from hmt_crypto.exchange.delta.private_read_wire import (
    _freeze_balances_private_read,
    _freeze_private_read,
)
from hmt_crypto.exchange.delta.private_routes import PrivateRouteId
from hmt_crypto.exchange.delta.rate_limit_policy import (
    DELTA_RATE_LIMIT_POLICY,
    RateLimitScope,
    ScopeKind,
)
from hmt_crypto.exchange.private_requests import (
    FillQuery,
    LatestPositionQuery,
    LeverageQuery,
    MarginedPositionQuery,
    OpenOrderQuery,
    OrderByClientIdQuery,
    OrderByIdQuery,
    OrderHistoryQuery,
    WalletTransactionQuery,
)
from tests.integration.support import task27_private_credentials_issuer as credential_issuer
from tests.integration.support import task210_worker_clock_issuer as worker_issuer
from tests.unit import delta_private_limiter_test_support as limiter_support

_ISSUED = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
_INCARNATION = UUID("00000000-0000-0000-0000-00000000b401")
_ACCOUNT_ID = UUID("00000000-0000-0000-0000-00000000b402")
_EXTERNAL_USER_ID = "12345"


def _account(
    *,
    environment: DeltaEnvironment = DeltaEnvironment.TESTNET,
    external_user_id: str = _EXTERNAL_USER_ID,
) -> AccountRef:
    return AccountRef(
        account_id=_ACCOUNT_ID,
        external_user_id=external_user_id,
        region=DeltaRegion.INDIA,
        environment=environment,
        mode=ExecutionMode.PAPER,
        credential_version=7,
    )


def _credentials() -> tuple[object, object, object]:
    origin = _canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.TESTNET)
    signer, _enrollment = credential_issuer.issue_enrollment_credential(
        credential_issuer._FIXTURE_MARKER,
        snapshot_id=UUID("00000000-0000-0000-0000-00000000b403"),
        credential_slot=credentials.IndiaCredentialSlot.INDIA_TESTNET,
        credential_version=7,
        account_id=_ACCOUNT_ID,
        external_user_id=_EXTERNAL_USER_ID,
        origin=origin,
        mode=ExecutionMode.PAPER,
        credential_epoch=11,
        api_key=SecretStr("b4-test-api-key"),
        api_secret=SecretStr("b4-test-api-secret"),
        user_agent="hmt-crypto/b4-test",
    )
    proof, currentness = credential_issuer.issue_account_binding_proof(
        credential_issuer._FIXTURE_MARKER,
        signer,
        proof_id=UUID("00000000-0000-0000-0000-00000000b404"),
        account=_account(),
        origin=origin,
        binding_ref=UUID("00000000-0000-0000-0000-00000000b405"),
        probe_ref=UUID("00000000-0000-0000-0000-00000000b406"),
        credential_epoch=11,
        proof_epoch=13,
        observed_at=_ISSUED,
        expires_at=_ISSUED + timedelta(minutes=5),
    )
    return signer, proof, currentness


def _context() -> dict[str, object]:
    worker, worker_currentness = limiter_support.issue_worker(
        worker_issuer._FIXTURE_MARKER,
        _INCARNATION,
    )
    clock, clock_currentness = limiter_support.issue_clock(
        worker_issuer._FIXTURE_MARKER,
        _INCARNATION,
        observed_monotonic_ns=400_000_000_000,
    )
    registry = _compose_private_limiter_registry(
        ownership=worker,
        ownership_currentness=worker_currentness,
        process_incarnation_id=_INCARNATION,
        policy=DELTA_RATE_LIMIT_POLICY,
    )
    scope = RateLimitScope(
        "india",
        "testnet",
        ScopeKind.EXTERNAL_USER,
        _EXTERNAL_USER_ID,
    )
    signer, proof, credential_currentness = _credentials()
    return {
        "account": _account(),
        "signer": signer,
        "account_binding": proof,
        "credential_currentness": credential_currentness,
        "worker_proof": worker,
        "worker_currentness": worker_currentness,
        "clock": clock,
        "clock_currentness": clock_currentness,
        "limiter_lease": registry.lease_external_user(scope=scope),
        "registry": registry,
    }


def _admit(request: object, context: dict[str, object]) -> object:
    return private_admission._admit_private_read_nonblocking(
        request=request,
        account=context["account"],
        signer=context["signer"],
        account_binding=context["account_binding"],
        credential_currentness=context["credential_currentness"],
        worker_proof=context["worker_proof"],
        worker_currentness=context["worker_currentness"],
        clock=context["clock"],
        clock_currentness=context["clock_currentness"],
        limiter_lease=context["limiter_lease"],
    )


def _request_cases() -> tuple[tuple[PrivateRouteId, object], ...]:
    return (
        (
            PrivateRouteId.BALANCES,
            _freeze_balances_private_read(
                region=DeltaRegion.INDIA,
                environment=DeltaEnvironment.TESTNET,
            ),
        ),
        (
            PrivateRouteId.MARGINED_POSITIONS,
            _freeze_private_read(
                MarginedPositionQuery(),
                region=DeltaRegion.INDIA,
                environment=DeltaEnvironment.TESTNET,
            ),
        ),
        (
            PrivateRouteId.LATEST_POSITION,
            _freeze_private_read(
                LatestPositionQuery(product_id=27),
                region=DeltaRegion.INDIA,
                environment=DeltaEnvironment.TESTNET,
            ),
        ),
        (
            PrivateRouteId.OPEN_ORDERS,
            _freeze_private_read(
                OpenOrderQuery(),
                region=DeltaRegion.INDIA,
                environment=DeltaEnvironment.TESTNET,
            ),
        ),
        (
            PrivateRouteId.ORDER_HISTORY,
            _freeze_private_read(
                OrderHistoryQuery(),
                region=DeltaRegion.INDIA,
                environment=DeltaEnvironment.TESTNET,
            ),
        ),
        (
            PrivateRouteId.ORDER_BY_ID,
            _freeze_private_read(
                OrderByIdQuery(order_id=123),
                region=DeltaRegion.INDIA,
                environment=DeltaEnvironment.TESTNET,
            ),
        ),
        (
            PrivateRouteId.ORDER_BY_CLIENT_ID,
            _freeze_private_read(
                OrderByClientIdQuery(client_order_id="b4-safe"),
                region=DeltaRegion.INDIA,
                environment=DeltaEnvironment.TESTNET,
            ),
        ),
        (
            PrivateRouteId.GET_LEVERAGE,
            _freeze_private_read(
                LeverageQuery(product_id=27),
                region=DeltaRegion.INDIA,
                environment=DeltaEnvironment.TESTNET,
            ),
        ),
    )


def _expect_admission_error(call: object) -> None:
    assert callable(call)
    try:
        call()  # type: ignore[operator]
    except ConfigurationError as error:
        assert error.context == {"operation": "private_read_admission"}
        assert error.__cause__ is None
        assert "b4-test-api" not in f"{error!s} {error!r}"
    else:
        raise AssertionError("unsafe admission succeeded")


def run() -> None:
    """Exercise one real process owner without contaminating the outer pytest process."""
    context = _context()
    admissions: list[object] = []
    for route_id, request in _request_cases():
        admission = _admit(request, context)
        admissions.append(admission)
        assert type(admission) is private_admission.PrivateReadAdmission
        assert admission.route_id is route_id
        assert admission.region is DeltaRegion.INDIA
        assert admission.environment is DeltaEnvironment.TESTNET
        assert admission.account_id == _ACCOUNT_ID
        assert admission.external_user_id == _EXTERNAL_USER_ID
        assert admission.credential_version == 7
        assert admission.request_evidence_sha256 == request.request_evidence_sha256
        assert admission.worker_proof_id == context["worker_proof"].proof_id
        assert admission.process_id == os.getpid()
        assert admission.process_incarnation_id == _INCARNATION
        assert admission.clock_owner_id == context["clock"].clock_owner_id
        assert admission.clock_epoch == context["clock"].clock_epoch
        assert admission.limiter_lease_id == context["limiter_lease"].lease_id
        assert admission.limiter_instance_id == context["limiter_lease"].limiter_instance_id
        assert (
            private_admission._attest_private_read_admission(admission, request=request)
            is admission
        )

    admission = admissions[0]
    assert not hasattr(private_admission, "_issue_admission")
    assert not hasattr(private_admission, "_state")
    assert repr(admission) == "PrivateReadAdmission(<redacted>)"
    assert not hasattr(admission, "__dict__")
    assert not any(
        fragment in name.casefold()
        for name in dir(admission)
        if not name.startswith("_")
        for fragment in ("sign", "send", "transport", "acquire", "secret", "api_key")
    )
    for operation in (
        lambda: copy.copy(admission),
        lambda: copy.deepcopy(admission),
        lambda: pickle.dumps(admission),
        lambda: hash(admission),
        lambda: admission == admission,
    ):
        try:
            operation()
        except TypeError:
            pass
        else:
            raise AssertionError("admission evidence was forgeable or serializable")

    counterfeit = object.__new__(private_admission.PrivateReadAdmission)
    _expect_admission_error(
        lambda: private_admission._attest_private_read_admission(
            counterfeit,
            request=_request_cases()[0][1],
        )
    )
    try:
        private_admission.PrivateReadAdmission()
    except TypeError:
        pass
    else:
        raise AssertionError("admission type was directly constructible")

    # Reflection must not expose either a mutable canonical record or a raw
    # issuer that accepts a caller-constructed record.  Both would turn this
    # point-in-time evidence into a forgeable confused-deputy token.
    base_request = _request_cases()[0][1]
    read_state = private_admission._admission_attester.__closure__[0].cell_contents
    canonical_state = read_state(admission)
    try:
        object.__setattr__(
            canonical_state,
            "account_id",
            UUID("00000000-0000-0000-0000-00000000dead"),
        )
    except (AttributeError, TypeError):
        pass
    else:
        raise AssertionError("canonical admission state was reflectively mutable")

    state_fields = getattr(type(canonical_state), "_fields", type(canonical_state).__slots__)
    forged_values = []
    for name in state_fields:
        if name == "admission_evidence_sha256":
            continue
        value = getattr(canonical_state, name)
        if name == "account_id":
            value = UUID("00000000-0000-0000-0000-00000000dead")
        forged_values.append(value)
    forged_state = type(canonical_state)(
        *forged_values,
        private_admission._admission_digest(*forged_values),
    )
    for cell in private_admission._admit_private_read_nonblocking.__closure__ or ():
        candidate_issuer = cell.cell_contents
        if not callable(candidate_issuer):
            continue
        try:
            forged_admission = candidate_issuer(forged_state)
        except Exception:
            continue
        if type(forged_admission) is private_admission.PrivateReadAdmission:
            _expect_admission_error(
                lambda forged_admission=forged_admission: (
                    private_admission._attest_private_read_admission(
                        forged_admission,
                        request=base_request,
                    )
                )
            )

    for query in (WalletTransactionQuery(), FillQuery()):
        held = _freeze_private_read(
            query,
            region=DeltaRegion.INDIA,
            environment=DeltaEnvironment.TESTNET,
        )
        _expect_admission_error(lambda held=held: _admit(held, context))

    wrong_request = _request_cases()[1][1]
    _expect_admission_error(
        lambda: private_admission._attest_private_read_admission(
            admission,
            request=wrong_request,
        )
    )

    request = _request_cases()[0][1]
    foreign_account = _account(external_user_id="54321")
    original_account = context["account"]
    context["account"] = foreign_account
    _expect_admission_error(lambda: _admit(request, context))
    context["account"] = original_account
    assert _admit(request, context).route_id is PrivateRouteId.BALANCES

    prod_request = _freeze_balances_private_read(
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.PROD,
    )
    _expect_admission_error(lambda: _admit(prod_request, context))
    assert _admit(request, context).route_id is PrivateRouteId.BALANCES

    class AccountSubclass(AccountRef):
        pass

    subclass_account = AccountSubclass.model_construct(**context["account"].model_dump())
    context["account"] = subclass_account
    _expect_admission_error(lambda: _admit(request, context))
    context["account"] = original_account
    assert _admit(request, context).route_id is PrivateRouteId.BALANCES

    original_method = object.__getattribute__(request, "_method")
    object.__setattr__(request, "_method", "POST")
    try:
        _expect_admission_error(lambda: _admit(request, context))
    finally:
        object.__setattr__(request, "_method", original_method)
    assert _admit(request, context).route_id is PrivateRouteId.BALANCES

    source = inspect.getsource(private_admission)
    forbidden_calls = (
        ".sign_at(",
        "_sign_owned_private_read_at(",
        ".acquire_mutation_once(",
        ".observe_nonblocking(",
        "httpx",
        "requests",
        "socket",
    )
    assert not any(fragment in source for fragment in forbidden_calls)

    stale_signer, stale_proof, stale_currentness = _credentials()
    credential_issuer.advance_credential_epoch(
        credential_issuer._FIXTURE_MARKER,
        stale_currentness,
    )
    original_credential = (
        context["signer"],
        context["account_binding"],
        context["credential_currentness"],
    )
    context["signer"] = stale_signer
    context["account_binding"] = stale_proof
    context["credential_currentness"] = stale_currentness
    _expect_admission_error(lambda: _admit(request, context))
    (
        context["signer"],
        context["account_binding"],
        context["credential_currentness"],
    ) = original_credential
    assert _admit(request, context).route_id is PrivateRouteId.BALANCES

    foreign_signer, foreign_proof, foreign_credential_currentness = _credentials()
    context["signer"] = foreign_signer
    _expect_admission_error(lambda: _admit(request, context))
    context["signer"] = original_credential[0]
    context["account_binding"] = foreign_proof
    context["credential_currentness"] = foreign_credential_currentness
    _expect_admission_error(lambda: _admit(request, context))
    (
        context["signer"],
        context["account_binding"],
        context["credential_currentness"],
    ) = original_credential
    assert _admit(request, context).route_id is PrivateRouteId.BALANCES

    stale_worker, stale_worker_currentness = limiter_support.issue_worker(
        worker_issuer._FIXTURE_MARKER,
        _INCARNATION,
    )
    limiter_support.invalidate_worker(worker_issuer._FIXTURE_MARKER, stale_worker_currentness)
    original_worker = context["worker_proof"], context["worker_currentness"]
    context["worker_proof"] = stale_worker
    context["worker_currentness"] = stale_worker_currentness
    _expect_admission_error(lambda: _admit(request, context))
    context["worker_proof"], context["worker_currentness"] = original_worker
    assert _admit(request, context).route_id is PrivateRouteId.BALANCES

    foreign_worker, foreign_worker_currentness = limiter_support.issue_worker(
        worker_issuer._FIXTURE_MARKER,
        _INCARNATION,
    )
    context["worker_proof"] = foreign_worker
    _expect_admission_error(lambda: _admit(request, context))
    context["worker_currentness"] = foreign_worker_currentness
    _expect_admission_error(lambda: _admit(request, context))
    context["worker_proof"], context["worker_currentness"] = original_worker
    assert _admit(request, context).route_id is PrivateRouteId.BALANCES

    original_lease = context["limiter_lease"]
    context["limiter_lease"] = object.__new__(type(original_lease))
    _expect_admission_error(lambda: _admit(request, context))
    context["limiter_lease"] = original_lease
    assert _admit(request, context).route_id is PrivateRouteId.BALANCES

    wrong_clock, wrong_clock_currentness = limiter_support.issue_clock(
        worker_issuer._FIXTURE_MARKER,
        UUID("00000000-0000-0000-0000-00000000b499"),
        observed_monotonic_ns=400_000_000_000,
    )
    original_clock = context["clock"], context["clock_currentness"]
    context["clock"] = wrong_clock
    context["clock_currentness"] = wrong_clock_currentness
    _expect_admission_error(lambda: _admit(request, context))
    context["clock"], context["clock_currentness"] = original_clock
    assert _admit(request, context).route_id is PrivateRouteId.BALANCES

    context["clock_currentness"].invalidate_signature_expired_nonblocking(
        context["clock"],
        correlation_id=UUID("00000000-0000-0000-0000-00000000b498"),
    )
    _expect_admission_error(lambda: _admit(request, context))

    replacement_clock, replacement_clock_currentness = limiter_support.issue_clock(
        worker_issuer._FIXTURE_MARKER,
        _INCARNATION,
        observed_monotonic_ns=400_000_000_000,
    )
    context["clock"] = replacement_clock
    context["clock_currentness"] = replacement_clock_currentness
    point_in_time = _admit(request, context)
    assert point_in_time.route_id is PrivateRouteId.BALANCES
    context["registry"].close()
    # The value deliberately remains attestable as historical identity only;
    # it has no send/sign/acquire authority.  Any later entry boundary must
    # revalidate live dependencies, while a new admission already fails here.
    assert (
        private_admission._attest_private_read_admission(
            point_in_time,
            request=request,
        )
        is point_in_time
    )
    _expect_admission_error(lambda: _admit(request, context))


if __name__ == "__main__":
    run()
    print("ok")
