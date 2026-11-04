"""用 pyodbc 只读连接易飞 SQL Server。

不把易飞放进 Django 的 DATABASES：表结构还没核对，ORM 模型会把未验证的列写死，
也更容易误迁移、误写入。这里只执行本模块拼出来的一条 SELECT。
系统自带的 ODBC 驱动是 Microsoft ODBC Driver 18 for SQL Server，见 docs/ERP_SYNC.md。
"""

from erp.queries import (
    assert_single_select,
    build_receipt_query,
    build_work_order_query,
    parse_receipt_records,
    parse_work_order_records,
)
from erp.sync_rows import FetchResult


class YifeiQueryError(RuntimeError):
    """连上或读易飞失败。消息里不应带密码。"""


def records_from_cursor(cursor):
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


class SqlServerYifeiSource:
    """易飞 9.012 的只读数据源。构造时不连接，拉取时才连接。"""

    def __init__(self, connection, work_order_map, receipt_map):
        self.connection = connection
        self.work_order_map = work_order_map
        self.receipt_map = receipt_map

    def fetch_open_work_orders(self):
        sql, params = build_work_order_query(self.work_order_map)
        records = self._select(sql, params)
        rows, warnings = parse_work_order_records(records, self.work_order_map)
        return FetchResult(rows=rows, warnings=warnings)

    def fetch_production_receipts(self):
        sql, params = build_receipt_query(self.receipt_map)
        records = self._select(sql, params)
        rows, warnings = parse_receipt_records(records, self.receipt_map)
        return FetchResult(rows=rows, warnings=warnings)

    def _select(self, sql, params):
        assert_single_select(sql)
        try:
            import pyodbc
        except ImportError as exc:
            raise YifeiQueryError(
                "没有 pyodbc。Linux 容器还需安装 "
                "Microsoft ODBC Driver 18 for SQL Server"
            ) from exc
        try:
            connection = pyodbc.connect(
                self.connection.odbc_connection_string(),
                timeout=self.connection.login_timeout,
                autocommit=True,
            )
        except Exception as exc:
            raise YifeiQueryError("连接易飞失败") from exc
        try:
            cursor = connection.cursor()
            cursor.timeout = self.connection.query_timeout
            try:
                cursor.execute(sql, params)
                if cursor.description is None:
                    raise YifeiQueryError("易飞没有返回结果集")
                return records_from_cursor(cursor)
            except YifeiQueryError:
                raise
            except Exception as exc:
                raise YifeiQueryError("读取易飞失败") from exc
        finally:
            connection.close()
