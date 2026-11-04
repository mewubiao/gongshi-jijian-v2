"""易飞入库映射的占位数据。表名和字段都还没对照 9.012。"""

DEFAULT_RECEIPT_MAPPINGS = (
    {
        "code": "receipt.table",
        "erp_table": "",
        "erp_column": "",
        "local_model": "ProductionReceipt",
        "local_field": "",
        "note": "入库单身表名待核对",
    },
    {
        "code": "receipt.erp_key",
        "erp_table": "",
        "erp_column": "",
        "local_model": "ProductionReceipt",
        "local_field": "erp_key",
        "note": "入库行键字段待核对",
    },
    {
        "code": "receipt.type",
        "erp_table": "",
        "erp_column": "",
        "local_model": "ProductionReceipt",
        "local_field": "erp_receipt_type",
        "note": "入库单别字段待核对",
    },
    {
        "code": "receipt.no",
        "erp_table": "",
        "erp_column": "",
        "local_model": "ProductionReceipt",
        "local_field": "erp_receipt_no",
        "note": "入库单号字段待核对",
    },
    {
        "code": "receipt.line",
        "erp_table": "",
        "erp_column": "",
        "local_model": "ProductionReceipt",
        "local_field": "erp_line_no",
        "note": "入库行号字段待核对",
    },
    {
        "code": "receipt.date",
        "erp_table": "",
        "erp_column": "",
        "local_model": "ProductionReceipt",
        "local_field": "receipt_date",
        "note": "入库日期字段待核对",
    },
    {
        "code": "receipt.work_order_key",
        "erp_table": "",
        "erp_column": "",
        "local_model": "ProductionReceipt",
        "local_field": "erp_work_order_key",
        "note": "工单键字段待核对",
    },
    {
        "code": "receipt.qty",
        "erp_table": "",
        "erp_column": "TG013",
        "local_model": "ProductionReceipt",
        "local_field": "receipt_qty",
        "note": "对账入库数量。旧系统注释为 TG013，待核对",
    },
    {
        "code": "receipt.source_qty_11",
        "erp_table": "",
        "erp_column": "TG011",
        "local_model": "ProductionReceipt",
        "local_field": "source_qty_11",
        "note": "旧系统注释 TG011，待核对",
    },
    {
        "code": "receipt.scrap_qty_12",
        "erp_table": "",
        "erp_column": "TG012",
        "local_model": "ProductionReceipt",
        "local_field": "scrap_qty_12",
        "note": "旧系统注释 TG012，待核对",
    },
    {
        "code": "receipt.accepted_qty_13",
        "erp_table": "",
        "erp_column": "TG013",
        "local_model": "ProductionReceipt",
        "local_field": "accepted_qty_13",
        "note": "旧系统注释 TG013，待核对",
    },
)


def overlay_receipt_env(env):
    """映射表里已经填了的列，补到还没在环境变量里出现的项上。环境变量优先。"""

    from erp.models import ErpSourceMapping

    merged = dict(env)
    rows = {row.code: row for row in ErpSourceMapping.objects.all()}
    pairs = (
        ("receipt.table", "erp_table", "YIFEI_RECEIPT_TABLE"),
        ("receipt.type", "erp_column", "YIFEI_RECEIPT_COL_TYPE"),
        ("receipt.no", "erp_column", "YIFEI_RECEIPT_COL_NO"),
        ("receipt.line", "erp_column", "YIFEI_RECEIPT_COL_LINE"),
        ("receipt.erp_key", "erp_column", "YIFEI_RECEIPT_COL_SOURCE_KEY"),
        ("receipt.date", "erp_column", "YIFEI_RECEIPT_COL_DATE"),
        ("receipt.work_order_key", "erp_column", "YIFEI_RECEIPT_COL_WORK_ORDER_KEY"),
        ("receipt.qty", "erp_column", "YIFEI_RECEIPT_COL_QTY"),
        ("receipt.source_qty_11", "erp_column", "YIFEI_RECEIPT_COL_QTY_11"),
        ("receipt.scrap_qty_12", "erp_column", "YIFEI_RECEIPT_COL_QTY_12"),
        ("receipt.accepted_qty_13", "erp_column", "YIFEI_RECEIPT_COL_QTY_13"),
    )
    for code, attr, env_name in pairs:
        if env_name in env:
            continue
        row = rows.get(code)
        if row is None:
            continue
        value = getattr(row, attr)
        if value:
            merged[env_name] = value
    return merged


def seed_receipt_mappings(sender, **kwargs):
    from erp.models import ErpSourceMapping

    for row in DEFAULT_RECEIPT_MAPPINGS:
        ErpSourceMapping.objects.get_or_create(
            code=row["code"],
            defaults={**row, "verified": False},
        )
