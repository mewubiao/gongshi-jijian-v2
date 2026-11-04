# 工时计件 v2

车间按工时、计件核算产量的新系统。这是工时计件的 v2，从空仓库重新搭起。

当前已有一期的领域数据模型。产品规则见 [docs/DESIGN.md](docs/DESIGN.md)，模型说明见 [docs/DATA_MODEL.md](docs/DATA_MODEL.md)。

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

`/admin/` 是 Django 自带后台，用来查看员工、工单、派工、报工和月结期间。

本地数据库是 SQLite 文件 `db.sqlite3`（已忽略，不会进仓库），并默认打开调试模式。`SECRET_KEY` 的本地默认值只为了本机能启动，部署时换成自己的密钥。

部署前设置这些环境变量：

| 变量 | 作用 |
| --- | --- |
| `DJANGO_SECRET_KEY` | 正式密钥 |
| `DJANGO_DEBUG` | 设为 `false` |
| `DJANGO_ALLOWED_HOSTS` | 允许的域名，用逗号分隔 |

## 检查

```bash
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py test
uv run ruff check .
uv run ruff format --check .
```

## 目录

- `config/`：配置、路由，以及 `/health/`。
- `masterdata/`：员工、班组、工作中心、产品与工序。
- `erp/`：易飞工单和入库只读镜像、字段映射、同步日志。
- `dispatch/`：派工、整组组员与小组长移交。
- `reporting/`：开工、暂停、报工、审核留痕、整组加入和离开。
- `timekeeping/`：辅助工时。
- `settlement/`：月结期间、锁账留痕、辅助工时汇总。

## 后续再做

- 车间终端、派工和审核页面
- 易飞工单同步
- 月结汇总、整组分配和导出
- 登录与权限
