"""Reviewed built-in-scalar rows for the inert Delta India private registry."""

PRIVATE_ROUTE_ROWS: tuple[tuple[object, ...], ...] = (
    (
        ("balances", "india_only", "GET", "/v2/wallet/balances")
        + ((), (), (), ())
        + ("balance_snapshot", 524288, "none", None, None, None, 256)
        + ("read", "hmac_signed_external_user", "safe_read", "safe_read")
        + ("balances", 3, "none", "read_only", "reviewed")
    ),
    (
        ("transactions", "india_only", "GET", "/v2/wallet/transactions")
        + (
            (),
            (
                "asset_ids",
                "transaction_types",
                "start_time",
                "end_time",
                "after",
                "before",
                "page_size",
            ),
            (),
            (),
        )
        + ("transaction_page", 4194304, "forward_cursor", 10, 50, 200, 10000)
        + ("read", "hmac_signed_external_user", "safe_read", "safe_read")
        + ("transactions", 10, "none", "read_only", "reviewed")
    ),
    (
        ("margined_positions", "india_only", "GET", "/v2/positions/margined")
        + ((), ("product_ids", "contract_types"), (), ())
        + ("margined_position_list", 4194304, "none", None, None, None, 4096)
        + ("read", "hmac_signed_external_user", "safe_read", "safe_read")
        + ("margined_positions", 3, "none", "read_only", "reviewed")
    ),
    (
        ("latest_position", "india_only", "GET", "/v2/positions")
        + ((), ("product_id",), (), ())
        + ("latest_position", 524288, "none", None, None, None, 1)
        + ("read", "hmac_signed_external_user", "safe_read", "safe_read")
        + ("latest_position", 3, "none", "read_only", "reviewed")
    ),
    (
        ("open_orders", "india_only", "GET", "/v2/orders")
        + (
            (),
            (
                "product_ids",
                "states",
                "contract_types",
                "order_types",
                "start_time",
                "end_time",
                "after",
                "before",
                "page_size",
            ),
            (),
            (),
        )
        + ("order_page", 4194304, "forward_cursor", 10, 50, 100, 5000)
        + ("read", "hmac_signed_external_user", "safe_read", "safe_read")
        + ("open_orders", 3, "none", "read_only", "reviewed")
    ),
    (
        ("order_history", "india_only", "GET", "/v2/orders/history")
        + (
            (),
            (
                "product_ids",
                "contract_types",
                "order_types",
                "start_time",
                "end_time",
                "after",
                "before",
                "page_size",
            ),
            (),
            (),
        )
        + ("order_page", 4194304, "forward_cursor", 10, 50, 200, 10000)
        + ("read", "hmac_signed_external_user", "safe_read", "safe_read")
        + ("order_history", 10, "none", "read_only", "reviewed")
    ),
    (
        ("fills", "india_only", "GET", "/v2/fills")
        + (
            (),
            (
                "product_ids",
                "contract_types",
                "start_time",
                "end_time",
                "after",
                "before",
                "page_size",
            ),
            (),
            (),
        )
        + ("fill_page", 8388608, "forward_cursor", 10, 50, 200, 10000)
        + ("read", "hmac_signed_external_user", "safe_read", "safe_read")
        + ("fills", 10, "none", "read_only", "reviewed")
    ),
    (
        ("order_by_id", "india_only", "GET", "/v2/orders/{order_id}")
        + (("order_id",), (), (), ())
        + ("order", 524288, "none", None, None, None, 1)
        + ("read", "hmac_signed_external_user", "safe_read", "safe_read")
        + ("order_by_id", 1, "none", "read_only", "reviewed")
    ),
    (
        (
            "order_by_client_id",
            "india_only",
            "GET",
            "/v2/orders/client_order_id/{client_oid}",
        )
        + (("client_oid",), (), (), ())
        + ("order", 524288, "none", None, None, None, 1)
        + ("read", "hmac_signed_external_user", "safe_read", "safe_read")
        + ("order_by_client_id", 1, "none", "read_only", "reviewed")
    ),
    (
        (
            "get_leverage",
            "india_only",
            "GET",
            "/v2/products/{product_id}/orders/leverage",
        )
        + (("product_id",), (), (), ())
        + ("leverage", 524288, "none", None, None, None, 1)
        + ("read", "hmac_signed_external_user", "safe_read", "safe_read")
        + ("get_leverage", 1, "none", "read_only", "reviewed")
    ),
    (
        ("place_order", "india_only", "POST", "/v2/orders")
        + (
            (),
            (),
            (
                "product_id",
                "size",
                "side",
                "order_type",
                "limit_price",
                "stop_order_type",
                "stop_price",
                "trail_amount",
                "stop_trigger_method",
                "bracket_stop_trigger_method",
                "bracket_stop_loss_limit_price",
                "bracket_stop_loss_price",
                "bracket_trail_amount",
                "bracket_take_profit_limit_price",
                "bracket_take_profit_price",
                "time_in_force",
                "mmp",
                "post_only",
                "reduce_only",
                "client_order_id",
            ),
            (),
        )
        + ("order", 1048576, "none", None, None, None, 1)
        + ("write", "hmac_signed_external_user", "create_order", "no_exchange_guarantee")
        + ("place_order", 5, "one_product_operation", "none", "offline_codec_only")
    ),
    (
        ("modify_order", "india_only", "PUT", "/v2/orders")
        + (
            (),
            (),
            (
                "id",
                "product_id",
                "size",
                "limit_price",
                "mmp",
                "post_only",
                "stop_price",
                "trail_amount",
            ),
            (),
        )
        + ("order", 1048576, "none", None, None, None, 1)
        + ("write", "hmac_signed_external_user", "modify_order", "no_exchange_guarantee")
        + ("modify_order", 5, "one_product_operation", "none", "offline_codec_only")
    ),
    (
        ("cancel_order", "india_only", "DELETE", "/v2/orders")
        + ((), (), ("id", "product_id"), ())
        + ("order", 1048576, "none", None, None, None, 1)
        + ("write", "hmac_signed_external_user", "cancel_order", "no_exchange_guarantee")
        + ("cancel_order", 5, "cancellation_exempt", "none", "offline_codec_only")
    ),
    (
        ("cancel_all", "india_only", "DELETE", "/v2/orders/all")
        + (
            (),
            (),
            (
                "product_id",
                "cancel_limit_orders",
                "cancel_stop_orders",
                "cancel_reduce_only_orders",
            ),
            (),
        )
        + ("acknowledgement", 262144, "none", None, None, None, 1)
        + ("write", "hmac_signed_external_user", "cancel_all", "no_exchange_guarantee")
        + ("cancel_all", 1, "cancellation_exempt", "none", "offline_codec_only")
    ),
    (
        (
            "set_leverage",
            "india_only",
            "POST",
            "/v2/products/{product_id}/orders/leverage",
        )
        + (("product_id",), (), ("leverage",), ())
        + ("leverage", 262144, "none", None, None, None, 1)
        + ("write", "hmac_signed_external_user", "set_leverage", "no_exchange_guarantee")
        + ("set_leverage", 1, "none", "none", "offline_codec_only")
    ),
    (
        ("set_margin_mode", "india_only", "PUT", "/v2/users/margin_mode")
        + ((), (), ("margin_mode", "subaccount_user_id"), ())
        + ("margin_mode", 262144, "none", None, None, None, 1)
        + ("write", "hmac_signed_external_user", "set_margin_mode", "no_exchange_guarantee")
        + ("set_margin_mode", 1, "none", "none", "offline_codec_only")
    ),
    (
        (
            "change_position_margin",
            "india_only",
            "POST",
            "/v2/positions/change_margin",
        )
        + ((), (), ("product_id", "delta_margin"), ())
        + ("margined_position", 1048576, "none", None, None, None, 1)
        + ("write", "hmac_signed_external_user", "add_margin", "no_exchange_guarantee")
        + ("change_position_margin", 5, "none", "none", "offline_codec_only")
    ),
    (
        ("create_bracket", "india_only", "POST", "/v2/orders/bracket")
        + (
            (),
            (),
            (
                "product_id",
                "stop_loss_order",
                "take_profit_order",
                "bracket_stop_trigger_method",
            ),
            (
                "stop_loss_order.order_type",
                "stop_loss_order.stop_price",
                "stop_loss_order.trail_amount",
                "stop_loss_order.limit_price",
                "take_profit_order.order_type",
                "take_profit_order.stop_price",
                "take_profit_order.limit_price",
            ),
        )
        + ("acknowledgement", 262144, "none", None, None, None, 1)
        + ("write", "hmac_signed_external_user", "create_bracket", "no_exchange_guarantee")
        + ("create_bracket", 1, "unverified_disabled", "none", "offline_codec_only")
    ),
    (
        ("edit_bracket", "india_only", "PUT", "/v2/orders/bracket")
        + (
            (),
            (),
            (
                "id",
                "product_id",
                "bracket_stop_loss_limit_price",
                "bracket_stop_loss_price",
                "bracket_take_profit_limit_price",
                "bracket_take_profit_price",
                "bracket_trail_amount",
                "bracket_stop_trigger_method",
            ),
            (),
        )
        + ("acknowledgement", 262144, "none", None, None, None, 1)
        + ("write", "hmac_signed_external_user", "edit_bracket", "no_exchange_guarantee")
        + ("edit_bracket", 1, "unverified_disabled", "none", "offline_codec_only")
    ),
)
