# 前端规范

业务页面用服务端渲染。本文是实现约定。产品规则仍以 [DESIGN.md](DESIGN.md) 为准。给后续开发的硬性要求同时写在仓库根目录的 [AGENTS.md](../AGENTS.md)。

## 硬规则

以下六条原文照录，必须遵守：

1. 使用统一的页面布局、侧边栏和顶部导航。
2. 表格、按钮、弹窗、表单统一封装为可复用组件。
3. 优先使用 daisyUI 组件，不随意创建新样式。
4. 所有页面支持中文、只考虑桌面端。
5. 不随意引入 React、Vue 等额外框架。
6. 修改功能时运行自动化测试，检查其他页面是否受影响。

## 技术栈

| 用途 | 选择 |
| --- | --- |
| 页面 | Django 模板 |
| 局部刷新 | HTMX 2.0.8，文件在 `static/vendor/htmx.min.js` |
| 样式 | Tailwind CSS 4.3.4 + daisyUI 5.7.50 |
| 后台皮肤 | Django Unfold |
| 正式数据库 | PostgreSQL（本仓库的页面骨架仍用本地 SQLite） |
| 部署 | Linux + Docker（本篇不写部署） |

界面语言是中文。`LANGUAGE_CODE` 为 `zh-hans`，`TIME_ZONE` 为 `Asia/Shanghai`。业务页面的 `<html lang>` 使用 `zh-Hans`。只做桌面端，不为手机单做断点。

HTMX 固定在 2.0.8，不使用刚发布的 4.x。页面用到的是 `hx-get`、`hx-post`、`hx-target`、`hx-swap`，2.0 的这组接口已经稳定。

样式锁定在 **Tailwind CSS 4.3.4** 和 **daisyUI 5.7.50**。这是目前两边的最新稳定版。版本写在 `scripts/build-css.sh` 和 `assets/css/node_modules/daisyui/package.json`，不要单独改一边。

### 为什么用 Tailwind 独立 CLI

样式编译用 Tailwind CSS 4 的独立可执行文件，不引入 Node，也不使用 django-tailwind。

django-tailwind 会在仓库里再挂一套 npm 主题工程。这个项目的依赖已经由 uv 锁定，页面又是 Django 模板渲染，单独为了编译 CSS 维护一套 Node 工程不划算。独立 CLI 是一个文件，daisyUI 5 的插件放在 `assets/css/node_modules/daisyui/`，让 CLI 能按包名解析，而不必安装 npm。

入口是 CSS 优先配置，`assets/css/input.css`：

```css
@import "tailwindcss" source(none);

@source "../../templates";
@source "../../ui";

@plugin "daisyui" {
  themes: light --default;
}
```

`@import "tailwindcss"` 和 `@plugin "daisyui"` 是 Tailwind 4 / daisyUI 5 的写法。`source(none)` 关掉自动扫描，再由 `@source` 指向 Django 模板和 `ui` 应用。表单控件的 class 写在 Python 里，所以 `ui` 也要扫到。主题用 daisyUI 5 的 `themes: light --default`，页面根节点是 `data-theme="light"`。

组件用 daisyUI 5 的类名，例如 `btn`、`btn-primary`、`btn-xl`、`table`、`modal`、`fieldset`、`input`、`select`、`textarea`、`checkbox`、`alert`、`menu`、`menu-active`、`join`。不要用 daisyUI 4 的 `input-bordered`、`form-control`、`label-text`。

编译结果提交在 `static/css/app.css`。本地和厂内没有外网时，直接 `runserver` 就能看到样式。HTMX 和 daisyUI 插件都在仓库里，页面运行不访问 CDN。

Tailwind CLI 大约一百兆，而且按操作系统区分，所以不提交。`./scripts/build-css.sh` 会把对应平台的 4.3.4 下载到 `var/tailwindcss/`（已忽略）。第一次编译需要网络，之后使用缓存的二进制。

CI 执行同一条脚本，再跑 `git diff --exit-code -- static/css/app.css`。源文件和已提交的 CSS 不一致时，检查失败。

不使用 `--minify`。daisyUI 5 的 `@scope` 在压缩优化时会报空选择器警告，未压缩的产物更稳，体积大约 100KB。

## 目录

```
assets/css/input.css          Tailwind 4 入口（@import / @plugin / @source）
assets/css/node_modules/daisyui/   daisyUI 5.7.50，供 @plugin "daisyui" 解析
scripts/build-css.sh          编译样式
static/css/app.css            编译产物，提交到仓库
static/vendor/htmx.min.js     htmx 2.0.8（0BSD）
static/vendor/htmx.LICENSE
templates/shell.html          公共 head：语言、CSS、HTMX、CSRF 头
templates/base.html           顶部导航 + 侧边栏 + 内容区
templates/terminal_base.html  车间终端：大字号、大按钮、无侧边栏
templates/components/         可复用组件，统一用 {% include %}
templates/home.html           占位首页
templates/terminal.html       终端占位
templates/ui/                 组件示例和弹窗片段
ui/                           页面、表单、冒烟清单
```

组件统一用 Django 的 `{% include %}`，不用自定义 template tag。页面引入按钮、表格时加上 `only`，避免外层变量串进组件。表单组件要渲染 CSRF，所以不使用 `only`。

业务页面继承 `templates/base.html`。车间终端继承 `templates/terminal_base.html`，它和后台页面共用 `shell.html` 的设计语言（同一份 CSS、同一套 daisyUI 主题 `light`），但没有侧边栏。

不要在业务页面里使用 Unfold 的 class，也不要引入 Alpine、React 或 Vue。Unfold 自带的 Alpine 和字体只服务于 `/admin/`。

## 组件

主题固定为 `data-theme="light"`。下面的 `variant` 只映射到 daisyUI 已有的颜色 class，不要在调用处拼新的 class 名。`extra_class` 只放 daisyUI 自己的修饰，例如 `btn-outline`。

### 按钮 `components/button.html`

| 参数 | 作用 |
| --- | --- |
| `label` | 按钮文字 |
| `variant` | `primary` `secondary` `accent` `neutral` `info` `success` `warning` `error` `ghost`，默认 primary |
| `size` | `xs` `sm` `lg` `xl`，不传则是 daisyUI 的默认尺寸 |
| `href` | 有链接且未禁用时渲染 `<a class="btn">` |
| `type` | `button` 或 `submit`，默认 `button` |
| `disabled` | 禁用 |
| `modal` | 对话框 id，点击时调用 `showModal()` |
| `hx_get` / `hx_post` | HTMX 地址 |
| `hx_target` / `hx_swap` | HTMX 目标。配合 `modal` 时，默认把内容放进 `#<id>-body` |
| `extra_class` | 额外的 daisyUI class |

```django
{% include "components/button.html" with label="保存" variant="primary" only %}
{% url 'terminal' as terminal_url %}
{% include "components/button.html" with label="进入终端" href=terminal_url size="lg" only %}
```

### 表格 `components/table.html`

包含空状态和分页位。`rows` 为空时显示 `empty_text`，默认「暂无数据」。传入 Django 的 `Page` 对象后，分页位渲染上一页、页码和下一页；不传 `page` 时分页位留空。

| 参数 | 作用 |
| --- | --- |
| `columns` | 表头列表 |
| `rows` | 行，每行是单元格列表。内容按文本转义 |
| `caption` | 表格标题 |
| `empty_text` | 空状态文案 |
| `page` | `Paginator.get_page()` 的结果 |

```django
{% include "components/table.html" with columns=columns rows=rows caption="报工" page=page only %}
```

分页是表格组件的一部分，使用 daisyUI 的 `join`。业务页面不要自己再画一套分页。

### 弹窗 `components/modal.html`

使用 daisyUI 推荐的 `<dialog class="modal">`。打开方式是按钮组件的 `modal` 参数；需要向对话框填内容时，给按钮加上 `hx_get` 或 `hx_post`。

| 参数 | 作用 |
| --- | --- |
| `id` | 页面上唯一的 id |
| `title` | 标题 |
| `body` | 初始文字，会被转义。HTMX 返回后替换 `#<id>-body` |
| `close_label` | 关闭按钮，默认「关闭」 |

```django
{% url 'ui-modal' as modal_url %}
{% include "components/button.html" with label="打开弹窗" modal="demo-modal" hx_get=modal_url only %}
{% include "components/modal.html" with id="demo-modal" title="说明" body="正在加载…" only %}
```

HTMX 片段不要继承 `base.html`，否则会把整页嵌进对话框。

### 表单 `components/form.html`

渲染一个 Django Form 的字段、帮助文字、字段错误和非字段错误。控件 class 来自 `ui.forms.DaisyFormMixin`，不要在模板里给单个输入框另写样式。

已覆盖的控件：文本、数字、下拉、多行文本、勾选、文件。单选组还没有统一样式，用到时先补 `DaisyFormMixin`，不要在页面上散落 class。

| 参数 | 作用 |
| --- | --- |
| `form` | 继承 `DaisyFormMixin` 的表单 |
| `submit_label` | 提交按钮文字，默认「提交」 |
| `show_submit` | 设为 `False` 时不渲染提交按钮 |
| `action` | 表单地址，不传则提交到当前页 |
| `hx_post` / `hx_swap` | 用 HTMX 提交 |
| `autocomplete` | 传给 `<form>` |
| `form_id` | 表单 id |

```django
{% include "components/form.html" with form=form submit_label="保存" %}
```

业务表单：

```python
class ReportForm(DaisyFormMixin, forms.Form):
    worker = forms.CharField(label="工人")
```

表单带 `novalidate`，校验错误以服务端返回为准，并用中文显示。

### 提示消息 `components/messages.html`

两个布局已经引入。视图里使用 Django 的 `messages`。等级对应 daisyUI：`success`、`warning`、`error`，其余（含 `info`、`debug`）使用 `alert-info`。

```python
messages.success(request, "已保存。")
```

## 页面

| 路径 | 说明 |
| --- | --- |
| `/` | 占位首页，使用统一布局 |
| `/terminal/` | 车间终端占位，大按钮，无侧边栏 |
| `/ui/` | 组件示例。`DEBUG` 或已登录可见，否则 403 |
| `/ui/modal/` | 弹窗示例的 HTMX 片段，权限与 `/ui/` 相同 |
| `/admin/` | Unfold 后台 |
| `/health/` | 健康检查，JSON，不使用页面布局 |

组件示例只用于对照组件，不写入业务数据。

## 新增页面

1. 模板继承 `base.html` 或 `terminal_base.html`，文案用中文。
2. 在对应应用里加视图和 URL。导航放进 `templates/base.html` 的侧边栏，仍然用 daisyUI 的 `menu`。
3. 表格、按钮、弹窗、表单、提示消息使用上面的组件。
4. 在 `ui/smoke.py` 的 `PAGES` 里加一行 `PageCheck`。`access` 可以是 `public`、`debug` 或 `auth`。
5. 如果新加了 Tailwind / daisyUI class，运行 `./scripts/build-css.sh` 并提交 `static/css/app.css`。class 名要在模板或 Python 里完整出现，不要用字符串拼接，否则扫描不到，CSS 里不会生成。

`PageCheck` 的字段：

| 字段 | 作用 |
| --- | --- |
| `url` | 路径 |
| `status` | 期望状态码，默认 200 |
| `contains` | 响应正文里必须有的字符串 |
| `absent` | 响应正文里不能出现的字符串 |
| `access` | `public` 直接访问；`debug` 在 `DEBUG=True` 下访问；`auth` 登录后访问 |

布局页面至少检查 `id="app-topnav"`。使用 `base.html` 的页面还要检查 `id="app-sidebar"`。终端页面检查 `id="app-terminal"`，并确认没有 `id="app-sidebar"`。

## 后台

`unfold` 放在 `INSTALLED_APPS` 的 `django.contrib.admin` 之前。`UNFOLD` 里设置了站点名称「工时计件」、浅色主题，以及开发 / 正式的环境标记。

Unfold 登录页上的 “Welcome back to” 和 “Return to site” 没有中文译文。`templates/unfold/helpers/unauthenticated_title.html` 和 `unauthenticated_header.html` 只改这两句文案，class 仍用 Unfold 原来的。

新增 `ModelAdmin` 时继承 `unfold.admin.ModelAdmin`，否则变更表单不会用上 Unfold 的控件。业务列表页仍然走本文件的布局和组件，不把后台页面当成车间终端或审核页。

## 提交前

改功能后跑全量检查，确认别的页面没有被带坏：

```bash
./scripts/build-css.sh
git diff --exit-code -- static/css/app.css
uv run ruff check .
uv run ruff format --check .
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py test
```

没有改 class 时，CSS 重编结果应与仓库里的文件一致。CI 会执行同一组命令。
