# WB-3 独立交付复核

2026-11-08。原交付提交 `3bb91bd`；本次从 issue 附件重新下载源码和证据，解压到新的目录，按 README 的 `make demo` 启动全新 PostgreSQL 实例，未复用原演示数据库。

## 复核结果

- 原源码包、演示证据包及 Linux amd64/arm64 包均通过所附 SHA256SUMS 校验；两个 Linux 程序的 ELF 架构正确，启动说明齐全。本次仍未在 Linux 执行。
- PostgreSQL15.18 与实际 Go 应用连通，`/healthz` 返回 ok；种子为2个工作中心、4名员工、4种普通/并行/协作工序及派工。
- 以真实 `supervisor` 会话完成文件导入、派工、审核；员工使用 `EMP-001` 和卡号1001完成开工、带原因暂停、继续、退出重登、30/40/30分批和最终完工。11项浏览器检查通过，含15秒空闲退出、CSV导出；无浏览器脚本错误和CDN请求。
- 直接查 PostgreSQL：任务 finished、3个批次原始新增量 `[300000,400000,300000]`（万分之一单位），累计100工序单位，冻结单价150分、标准120000000微秒，计件15000分；同派工不同批次计时区间交叠数0。审计中的派工和审核操作人均为 supervisor，生产操作人为员工会话。
- 再次执行迁移/seed后，仍为4名员工、3张工单、5张派工（含浏览器新增各1张），未重复播种。
- 针对性重跑5项测试函数，另含3个并行子场景：完整并行20/40分钟、部分重叠40/50分钟、暂停守恒、协作25/25/25/25及20/20/20/40、30/40/30、不完整依赖阻止审核、首次派工并发及调价后旧/新工单版本。全部通过，数据库用真实 PostgreSQL 隔离 schema。
- `npm ci`、前端构建通过；重新构建的 CSS/HTMX 与下载包字节一致，实际HTTP提供的CSS也相同。原完整13项测试、vet、race报告在首次证据包中，本次未机械重复。

## 本次修正

已读取原提交的 GitHub Actions 失败日志：PostgreSQL服务容器创建时，健康检查命令中的单引号没有保留含空格参数，Docker将 `-U` 解析成自身选项并报 `unknown shorthand flag: 'U'`，测试尚未开始。现将 `services.postgres.options` 改为折叠字符串，并使用双引号保留健康检查命令参数。配置结构参考 [GitHub官方PostgreSQL服务容器文档](https://docs.github.com/en/actions/tutorials/use-containerized-services/create-postgresql-service-containers)；修正后的工作流通过 actionlint1.7.12。

本次修改为CI配置及复核记录，应用与数据库核算代码沿用原交付；原Linux程序继续适用。PR仍为同一分支的PR #6。推送后由GitHub执行新CI，未在本地等待或宣称新CI已通过。

## 试用与边界

使用本次复核源码包，进入 `go-mvp/` 执行 `make demo`（Go1.26+、PostgreSQL15+）；也有Docker Compose入口。演示主管密码 `SupervisorDemo!2026`，管理员 `WorkshopDemo!2026`；员工卡号1001–1004，工牌EMP-001–004。

原 `DECISIONS.md` 中的并行切批敏感性、协作排他、数值精度和例外处理仍有效。工单来自演示/文件导入，未接通真实易飞或物理刷卡硬件；完整工资月结等后续边界保持原说明。临时应用与本次新建数据库在复核结束时停止，不将本机测试URL作为持续预览交付。
