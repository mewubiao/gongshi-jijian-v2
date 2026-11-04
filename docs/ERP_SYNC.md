# 易飞只读同步

本系统部署在 Linux + Docker，易飞 9.012 的数据库是 Microsoft SQL Server。同步只读：只执行 `SELECT`，不把计件数量、工时、金额回写易飞。数据库登录账号应只给 `db_datareader`（或等价的只读权限）。应用侧再限制语句只能是一条 `SELECT`。

默认每 5 分钟拉一次。启动调度进程后会先跑一轮，然后按间隔重复。

## 为什么用 pyodbc，而不是 Django 的第二个数据库

易飞表名和字段还没对照客户库。若把 SQL Server 配成 Django 的第二数据库，就要为未核对的表建 ORM 模型，迁移和写入也更容易误碰到易飞。

同步只需要参数化的 `SELECT`。连接用 `pyodbc` 加 Microsoft ODBC Driver 18 for SQL Server，不放进 `DATABASES`。测试用假数据源，CI 不连接 SQL Server，也不需要安装 ODBC 驱动。

## 为什么用 APScheduler，而不是 django-q2

定时任务只有这一个：每 5 分钟跑一次同步。django-q2 要队列、独立 worker，通常还要 Redis 或它自己的表。APScheduler 的阻塞调度器由一个单独进程跑就够了。

`max_instances=1` 且 `coalesce=True`，调度器不会叠着跑自己。另外 `SyncLock` 用一行数据库锁，手工执行 `sync_erp_workorders` 时如果上一轮还没结束，这一轮记为已跳过。锁默认 15 分钟过期（`YIFEI_SYNC_STALE_SECONDS`），必须大于同步间隔。过期后下一轮可以接管，并把仍标着“进行中”的同步记录改成失败。

不要把调度器放进 gunicorn 或 `runserver`。多个网页进程会各自起一个调度器。

## 待按实际库核对

下面的表名和列名是易飞 9.x 里常见的写法，**不是对本客户数据库的核对结果**。代码里的常量 `erp.yifei_config.VERIFICATION_NOTE` 就是“待按实际库核对”。上线前必须用客户库改环境变量。在核对完成之前，不要启动定时任务。

工单单头默认表 `MOCTA`：

| 含义 | 默认列 | 环境变量 |
| --- | --- | --- |
| 单别 | `TA001` | `YIFEI_COL_ORDER_TYPE`（空字符串表示不用单别） |
| 单号 | `TA002` | `YIFEI_COL_ORDER_NO` |
| 产品品号 | `TA006` | `YIFEI_COL_PRODUCT` |
| 计划数量 | `TA015` | `YIFEI_COL_PLANNED_QTY` |
| 已完成量 | `TA017` | `YIFEI_COL_COMPLETED_QTY` |
| 剩余量 | 不默认 | `YIFEI_COL_REMAINING_QTY` |
| 工作中心 | `TA021` | `YIFEI_COL_WORK_CENTER` |
| 状态 | `TA011` | `YIFEI_COL_STATUS` |
| 更新时间 | `MODI_DATE` | `YIFEI_COL_UPDATED_AT` |

本地工单号默认是 `单别-单号`。没有剩余量列时，用计划数量减已完成量；已完成量为空则按 0。算出来为负的行跳过，不改已有镜像。数量超过两位小数时四舍五入到分，并在同步记录里告警。

状态默认：`1`、`2`、`3` 为未结，`Y`、`y` 为已关闭。查询只拉未结。本地原先未结、这次不在未结结果里的工单标为已关闭。未知状态只告警，不改本地镜像，避免状态码配错时把工单关掉。结果为空且本地还有未结工单时，仍会全部标为已关闭，并写一条醒目告警。所以状态码没核对之前不要开定时任务。

未知产品、未知工作中心只告警并跳过该行，不让整次同步失败。已经在本地的工单不会因此被关掉。

生产入库默认表 `MOCTG`（常见的入库单身，**待核对**）：

| 含义 | 默认列 | 环境变量 |
| --- | --- | --- |
| 工单单别 | `TG014` | `YIFEI_RECEIPT_COL_ORDER_TYPE` |
| 工单单号 | `TG015` | `YIFEI_RECEIPT_COL_ORDER_NO` |
| 入库单别 | `TG001` | `YIFEI_RECEIPT_COL_TYPE` |
| 入库单号 | `TG002` | `YIFEI_RECEIPT_COL_NO` |
| 序号 | `TG003` | `YIFEI_RECEIPT_COL_LINE` |
| 品号 | `TG004` | `YIFEI_RECEIPT_COL_PRODUCT` |
| 数量 | `TG011` | `YIFEI_RECEIPT_COL_QTY` |
| 日期 | 不默认 | `YIFEI_RECEIPT_COL_DATE` |
| 单独的来源键 | 不默认 | `YIFEI_RECEIPT_COL_SOURCE_KEY` |

来源键默认是 `入库单别-入库单号-序号`。也可以改成一列来源键。数量列可能是验收数量而不是入库数量，必须核对。退库若用负数表示，合计时会减掉；若客户库用另一张表表示退库，要改映射。

入库行上的工单在本地不存在时跳过并告警，不因此新建工单。品号和工单产品不一致时仍按工单记入，并告警。是否必须一致，待核对。

`YIFEI_RECEIPT_DELETE_MISSING` 默认 `no`：只新增和更新，不删除本地多出来的入库行。查询被确认是全量快照之后，再设为 `yes`。默认不删，是为了避免未核对的过滤条件把入库删掉。

入库要落到 `ProductionReceipt`。这张表由数据模型分支（PR #3，`cursor/domain-models-569b`）按旧系统的 ProductionReceipt 增加。本分支不另建入库表。模型合并进来之前，工单同步照常写入，入库只拉取并在 `SyncRun.warnings` 里说明没写入。

## 差异

`python manage.py work_order_variances` 按工单打印：

差额 = 生产入库数量合计 − 报工数量。

报工数量的暂定口径见 [DATA_MODEL.md](DATA_MODEL.md)：待审核和已审核通过的有效完工数量（含审核修正），不含已撤回和已驳回。入库模型还没落地时，入库和差额打印为 `—`，不当成 0。

## 连接

环境变量，仓库不放密码，也不要写进镜像。

| 变量 | 作用 | 默认 |
| --- | --- | --- |
| `YIFEI_SQLSERVER_HOST` | 主机 | 必填 |
| `YIFEI_SQLSERVER_PORT` | 端口 | `1433` |
| `YIFEI_SQLSERVER_DATABASE` | 数据库名 | 必填 |
| `YIFEI_SQLSERVER_USER` | 只读账号 | 必填 |
| `YIFEI_SQLSERVER_PASSWORD` | 密码 | 必填，可含空格，不要留空 |
| `YIFEI_SQLSERVER_DRIVER` | ODBC 驱动名 | `ODBC Driver 18 for SQL Server` |
| `YIFEI_SQLSERVER_ENCRYPT` | 是否加密 | `yes` |
| `YIFEI_SQLSERVER_TRUST_SERVER_CERTIFICATE` | 是否信任服务器证书 | `no` |
| `YIFEI_SQLSERVER_LOGIN_TIMEOUT` | 登录超时（秒） | `5` |
| `YIFEI_SQLSERVER_QUERY_TIMEOUT` | 查询超时（秒） | `60` |
| `YIFEI_SCHEMA` | 工单表架构 | 空，表示不加架构 |
| `YIFEI_TABLE` | 工单表 | `MOCTA` |
| `YIFEI_ORDER_NO_SEPARATOR` | 单别和单号的连接符 | `-` |
| `YIFEI_OPEN_STATUSES` | 未结状态，英文逗号分隔 | `1,2,3` |
| `YIFEI_CLOSED_STATUSES` | 已关闭状态 | `Y,y` |
| `YIFEI_EXTRA_FILTERS` | 附加等值条件，如 `TA013=Y` | 空 |
| `YIFEI_RECEIPT_TABLE` | 入库表 | `MOCTG` |
| `YIFEI_RECEIPT_SCHEMA` | 入库表架构 | 空 |
| `YIFEI_RECEIPT_EXTRA_FILTERS` | 入库附加条件 | 空 |
| `YIFEI_RECEIPT_KEY_SEPARATOR` | 入库来源键连接符 | `-` |
| `YIFEI_RECEIPT_DELETE_MISSING` | 全量快照时删掉缺失行 | `no` |
| `YIFEI_SYNC_INTERVAL_SECONDS` | 定时间隔 | `300` |
| `YIFEI_SYNC_STALE_SECONDS` | 锁过期 | `900` |

列名只允许字母、数字和下划线。过滤值走 SQL 参数。附加条件的值里不要写英文逗号或等号。

Driver 18 默认加密。易飞若用自签证书，把 `YIFEI_SQLSERVER_TRUST_SERVER_CERTIFICATE` 设为 `yes`。无时区的更新时间按项目时区 `Asia/Shanghai` 理解，不当作 UTC。`CHAR` 字段的尾部空格在解析时去掉。

连接失败时，同步记录里的错误会抹掉密码。

## 怎么跑

先核对映射，再手工跑一轮：

```bash
python manage.py sync_erp_workorders --dry-run
python manage.py sync_erp_workorders
```

`--dry-run` 会试算工单变更并回滚，不写 `SyncRun`，也不写入库。确认 `SyncRun` 的条数和告警之后，再单独起调度：

```bash
python manage.py run_erp_scheduler
```

差异：

```bash
python manage.py work_order_variances
```

管理后台可以查看工单镜像和同步记录，不能手改。

## Linux 容器里的 ODBC 驱动

项目还没有完整的部署 Dockerfile。运行同步的镜像需要 unixODBC 和 Microsoft ODBC Driver 18 for SQL Server。下面是 Debian 12 的安装片段，基础镜像或微软仓库地址变了要以微软当前文档为准（Install the Microsoft ODBC driver for SQL Server (Linux)）：

```dockerfile
RUN apt-get update \
    && apt-get install -y curl gnupg apt-transport-https \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
        > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y msodbcsql18 unixodbc \
    && rm -rf /var/lib/apt/lists/*
```

`pyodbc` 若没有对应轮子、需要在镜像里编译，再装 `unixodbc-dev` 和编译工具。CI 不装这些。

## 还需要客户提供的信息

1. SQL Server 的主机、端口、数据库名，以及只读账号。密码只放环境变量。
2. 服务器证书是正规证书，还是需要 `TrustServerCertificate=yes`。
3. 工单表的架构、表名，以及单别、单号、产品、计划数量、已完成量、剩余量、工作中心、状态、更新时间各在哪一列。默认的 `TA021` 工作中心和 `MODI_DATE` 都还没核对。
4. 哪些状态值是未结，哪些是已关闭。未审核的工单要不要排除（例如审核码列）。
5. 更新时间的真实列和格式（8 位日期、14 位日期时间，或 datetime）。
6. 生产入库在哪张表。数量是入库数量还是验收数量。退库怎么表示。
7. 入库如何对应工单（单别 + 单号，或别的键），来源键用单别、单号、序号，还是有单独的唯一列。
8. 入库查询是不是全量。是全量的话，才打开 `YIFEI_RECEIPT_DELETE_MISSING`。
9. 入库品号是否必须和工单产品一致。
10. 差异口径是否就是“入库合计减待审核与已审核通过的有效报工数量”。
