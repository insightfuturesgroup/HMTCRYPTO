"""Closed identity set for the reviewed Delta private REST registry."""

from hmt_crypto.domain.private_primitives import ExactStrEnum


class PrivateRouteId(ExactStrEnum):
    """Stable identities for every reviewed private read or inert codec route."""

    BALANCES = "balances"
    TRANSACTIONS = "transactions"
    MARGINED_POSITIONS = "margined_positions"
    LATEST_POSITION = "latest_position"
    OPEN_ORDERS = "open_orders"
    ORDER_HISTORY = "order_history"
    FILLS = "fills"
    ORDER_BY_ID = "order_by_id"
    ORDER_BY_CLIENT_ID = "order_by_client_id"
    GET_LEVERAGE = "get_leverage"
    PLACE_ORDER = "place_order"
    MODIFY_ORDER = "modify_order"
    CANCEL_ORDER = "cancel_order"
    CANCEL_ALL = "cancel_all"
    SET_LEVERAGE = "set_leverage"
    SET_MARGIN_MODE = "set_margin_mode"
    CHANGE_POSITION_MARGIN = "change_position_margin"
    CREATE_BRACKET = "create_bracket"
    EDIT_BRACKET = "edit_bracket"


__all__ = ["PrivateRouteId"]
