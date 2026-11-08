# 车间工时与计件 Go MVP

独立的 `go-mvp/` 应用，保留仓库原 Python 实现。包含管理后台、工单文件导入、工序派工、公共终端、协作/并行计时、分批报工、审核修正、月度查询和 CSV 导出。使用真实 PostgreSQL；演示工单不代表已接通易飞 ERP 9.012。

## 最快启动

安装 Docker 与 Compose 后，在此目录执行：

```sh
docker compose up --build
```

浏览器打开 **http://127.0.0.1:8080**。首次启动自动迁移、幂等初始化演示资料。停止用 `docker compose down`；数据库卷保留。镜像首次构建需要网络，运行页面资源全部由 Go 本地提供。

本机已有 **Go 1.26+、PostgreSQL 15+** 时，也可执行（不要以 root 运行 initdb）：

```sh
make demo
```

脚本在本目录 `.pg/` 建隔离实例，监听 `127.0.0.1:55432`，前台运行应用。Ctrl+C 停止应用及脚本启动的数据库，资料保留，下次可恢复。脚本使用演示密码，不要复用到生产。端口冲突会直接失败，不会操作其他数据库。

已有专用 PostgreSQL 数据库时：

```sh
export DATABASE_URL='postgres://USER:PASSWORD@127.0.0.1:5432/DATABASE?sslmode=disable'
go run . -migrate -seed -demo
```

`-migrate` 执行可重复迁移；`-seed` 只初始化空库，必须同时 `-demo`；`-once` 执行后退出。静态 CSS / HTMX 已入库并嵌入二进制，运行不需要 Node 或 CDN。默认只监听本机。`/healthz` 检查数据库连接。

## 演示身份

| 用途 | 账号或工牌 |
| --- | --- |
| 管理员 | 登录页展开“管理员 / 主管”，选管理员，密码 `WorkshopDemo!2026` |
| 主管 | 选主管，密码 `SupervisorDemo!2026` |
| 员工张明 / 李华 / 王芳 / 赵强 | 卡号 `1001` / `1002` / `1003` / `1004` |
| 同一员工的扫码值 | `EMP-001` / `EMP-002` / `EMP-003` / `EMP-004` |

读卡器 / 扫码枪按键盘输入加回车工作；可以手动输入同值演示。本次没有实测物理硬件。生产模式不传 `-demo`，必须配置至少12字符 `ADMIN_PASSWORD`，主管密码缺省则主管禁登；演示密码仅在明确的演示模式生效。

## 约五分钟演示

1. 管理员进入“基础资料”：两车间、四员工、产品及四个工序。精密切削 / 倒角打磨允许并行，协作装配允许多人协作，质量检验是默认单人、禁止并行的普通工序。新工序两个开关默认关闭，单件数量默认1。
2. “生产工单”可选示例 CSV 文件导入。文件按外部标识幂等更新；产品和中心用档案编码映射。已派工单的产品/中心/数量变化整批拦截并保留旧镜像，关闭工单禁止新派、允许在制收尾。
3. “派工管理”选未关闭工单、对应工序、员工、工序数量。工单首次派工冻结该产品整套工序版本；100产品单位、单件4对应400工序单位，派工/报工只换算一次。
4. 另开浏览器会话，以 `1001` 或 `EMP-001` 进入终端。选择精密切削：开工→填原因暂停→继续。任务卡显示服务器恢复的累计参与时间；普通协作成员只能查看，由当前组长统一操作。
5. 分批输入新增合格量30、40，最后输入30并点“提交最后一批并完工”。每次只填本次新增量，前两次不结束任务。退出/换人/刷新不停止后台计时；15秒不操作会清身份。
6. 管理员或主管进入“报工审核”，可驳回并填原因、修改数量重报、扣除异常参与时间。原始报工/区间不覆盖，修正另存。数量未齐的并行批次待核算，齐备后相互依赖的批次联合通过。
7. “工时与计件”按月份、员工、车间、工单、工序、日期和状态筛选并导出 CSV。汇总只计已通过记录；100合格工序单位×冻结1.50元＝150.00元。协作查看四人个人分量。原始、修正后有效、并行核算工时分列。

协作工序与其他任务并行时，所有涉及的工序都须开启并行；任一排他任务会拦截。组长可登记加入/离开，离场前先移交组长。暂停不计时，但保留人员占用。

## 开发与验证

```sh
npm ci
npm run build
make generate                       # sqlc v1.31.1，生成代码已入库
export TEST_DATABASE_URL="$DATABASE_URL"
make test                            # go test / go vet / go test -race
```

集成测试创建独立随机 schema，并在结束后清理；测试角色需建 schema 权限。没有 `TEST_DATABASE_URL` 时明确跳过数据库测试，不以假库代替。测试不要指向共享生产库。

浏览器冒烟需 Node22+ 及 Playwright Chromium：

```sh
npx playwright install chromium
BASE_URL=http://127.0.0.1:18080 npm run smoke
```

也可通过 `BROWSER_EXECUTABLE` 指向现有 Chromium / Edge。脚本使用真实页面和数据库：CSV文件导入与更新→派工→刷工牌→开工/暂停/继续→退出重登→30/40/30→驳回重报→审核→统计/CSV→实测15秒退出，保存截图与报告到 `artifacts/`。每次新建独立演示工单，适用于演示库。

## 部署与结构

`go build -trimpath -o bin/workshop .` 生成含模板、迁移和前端资产的单文件应用。Ubuntu 可使用 `deploy/workshop.service`、`deploy/workshop.env.example`：以独立 `workshop` 用户运行，二进制放 `/opt/workshop/workshop`，环境配置放 `/etc/workshop/workshop.env`（权限600），本机 PostgreSQL 另行建专用角色/库，再 `systemctl enable --now workshop`。迁移需要该库内建表权限。HTTPS 反向代理下 `COOKIE_SECURE=true`。本交付仅提供模板，没有安装服务或部署公网。

`internal/app/` 保存领域规则、核算、会话和 HTTP；`db/schema.sql` 是幂等迁移，`db/queries.sql` 与 `sqlc.yaml` 生成 `internal/store/`；`web/` 是 Go Templates、Tailwind4+daisyUI5 源码和构建产物。数据库是有主外键、唯一键和 CHECK 的关系表；JSON仅用于 sqlc 输入/读取投影及审计载荷，不替代数据库实体关系。

请一并阅读 [决策与核算限制](docs/DECISIONS.md) 和 [实测报告](docs/TEST-REPORT.md)。完整工资发放/月结锁账、班次归属、辅助作业、返工、真实 ERP 对接、缺失时间补录和已审核工资更正属于后续，当前是生产闭环 MVP。


## WB-5 优化版

当前新增报工使用 `interval-v2`；原有批次保留 `interval-v1`，已审核结果不重算。主管可在审核卡的“核算版本处理”中记录原因，统一关联未审批次版本。算法依据、兼容迁移和本地升级记录见 [WB-5-RELEASE.md](docs/WB-5-RELEASE.md)。

浏览器脚本会新增业务数据，必须显式指定隔离实例，例如 `BASE_URL=http://127.0.0.1:18080 npm run smoke` 和 `npm run smoke:wb5`（同样指定 BASE_URL）。集成测试的 `TEST_DATABASE_URL` 也应指向独立实例；请勿指向正在试用的本地业务库。

## WB-6 流程与响应优化

本轮新增“多人各自派工”，与多人协作一单分别呈现；一次选择工单工序，按人填量，展示合计与冻结工序余额，保存时整批校验。员工开工、暂停、恢复与新增量报工就地更新任务卡并恢复输入焦点。手机端压缩身份导航、按需展开筛选；客户端时钟不会续身份。

统计改为数据库筛选和每页50条，汇总、员工合计及CSV使用相同完整筛选；终端读取仅加载相关任务，业务保存仅写变化的行。现有事务锁、首次冻结、interval-v1/v2 与已审历史规则保留。实测、复现及本地回退见 [WB-6-RELEASE.md](docs/WB-6-RELEASE.md)。

```sh
BASE_URL=http://127.0.0.1:18080 npm run smoke:wb6
BASELINE_URL=http://127.0.0.1:18081 BASE_URL=http://127.0.0.1:18080 node scripts/compare-wb6.mjs
```

两条浏览器脚本均会写入隔离演示数据，不能指向8080用户环境。性能脚本 `scripts/measure-wb6.py --help` 提供独立数据库、两个版本及SQL日志计数的参数。
