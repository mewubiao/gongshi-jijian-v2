# 工时计件 v2

车间按工时、计件核算产量的新系统。这是工时计件的 v2，从空仓库重新搭起。

当前已有一期的领域数据模型，以及易飞未结工单和生产入库的只读同步。入库和每一道工序报工的对照见数据模型说明。产品规则见 [docs/DESIGN.md](docs/DESIGN.md)，模型说明见 [docs/DATA_MODEL.md](docs/DATA_MODEL.md)，易飞连接和待核对项见 [docs/ERP_SYNC.md](docs/ERP_SYNC.md)。

已接入账号登录、五类员工角色权限和按班组隔离的业务查询。登录入口为 `/accounts/login/`，首次建账号及权限规则见 [docs/ACCESS_CONTROL.md](docs/ACCESS_CONTROL.md)，开发进度见 [PROJECT_STATUS.md](PROJECT_STATUS.md)。

班组长可在 `/dispatch/` 新建单人／整组派工，在 `/reports/` 和 `/auxiliary/` 审核报工与辅助工时，支持修正、驳回、超量确认和留痕。操作说明与当前边界见 [docs/DISPATCH_REVIEW.md](docs/DISPATCH_REVIEW.md)。

现已有可试用的报工 MVP：管理员授权终端、员工摄像头扫码／外接工牌标识认人、单人和整组开完工、暂停恢复、辅助工时登记、并行工时冻结及审核闭环。直接试用见 [MVP_START.md](MVP_START.md)，终端规则见 [docs/TERMINAL.md](docs/TERMINAL.md)。

五类角色的代码检查、易用性和查询效率改进见 [docs/USABILITY_REVIEW.md](docs/USABILITY_REVIEW.md)。

管理端 `/settlement/` 提供计件月结试算、整组按冻结工时分配、锁账版本与 Excel 导出，见 [docs/SETTLEMENT.md](docs/SETTLEMENT.md)。

## 技术选择

- Python 3.12，Django 5.2 LTS。旧版工时计件使用 Django，v2 继续用它。
- 骨架锁定在 Django 5.2。这一支的安全更新支持到 2028 年 4 月，适合会长期运行的车间系统。
- 依赖用 [uv](https://docs.astral.sh/uv/) 管理，锁定文件是 `uv.lock`。
- 检查和格式化用 Ruff。

## 本地运行

需要已安装 Python 3.12 和 uv。

```bash
uv sync
uv run python manage.py migrate
uv run python manage.py runserver
```

打开 <http://127.0.0.1:8000/health/>，返回：

```json
{"status": "ok"}
```

`/admin/` 使用 Django Admin 和 Unfold。按角色显示人员与组织、工艺与类别、易飞与同步菜单，提供派工、报工、辅助工时、终端授权和计件月结的入口。业务页面顶部可返回管理后台。

本地数据库是 SQLite 文件 `db.sqlite3`（已忽略，不会进仓库），并默认打开调试模式。`SECRET_KEY` 的本地默认值只为了本机能启动，部署时换成自己的密钥。

部署前设置这些环境变量：

| 变量 | 作用 |
| --- | --- |
| `DJANGO_SECRET_KEY` | 正式密钥 |
| `DJANGO_DEBUG` | 设为 `false` |
| `DJANGO_ALLOWED_HOSTS` | 允许的域名，用逗号分隔 |
| `DJANGO_DB_PATH` | 可选 SQLite 文件路径；默认项目根目录 `db.sqlite3` |

## 检查

全部检查需 Node.js 22，以运行终端二维码解码和空闲退出的 JavaScript 测试；应用运行本身不需要 Node。

```bash
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py test
uv run ruff check .
uv run ruff format --check .
```

## 目录

- `config/`：配置、路由，以及 `/health/`。
- `accounts/`：登录、退出、改密码、角色授权、查询范围和工作台。
- `masterdata/`：员工、班组、工作中心、产品与工序。
- `erp/`：易飞工单和入库只读镜像、字段映射、同步日志，以及只读同步。
- `dispatch/`：派工、整组组员与小组长移交。
- `reporting/`：开工、暂停、报工、审核留痕、整组加入和离开。
- `terminal/`：终端授权、员工识别、触摸报工页面和本地摄像头扫码。
- `timekeeping/`：辅助工时。
- `settlement/`：计件月结、锁账快照、辅助工时汇总和 Excel 导出。

## 后续再做

- 辅助工时驳回后的重报
- 真实摄像头与 NFC 设备、易飞现场映射联调和生产数据库并发验证
