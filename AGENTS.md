# 给后续开发的要求

做这个仓库的功能时，遵守下面的约定。产品规则见 [docs/DESIGN.md](docs/DESIGN.md)。前端写法见 [docs/FRONTEND.md](docs/FRONTEND.md)。

## 前端硬规则

以下六条必须遵守：

1. 使用统一的页面布局、侧边栏和顶部导航。
2. 表格、按钮、弹窗、表单统一封装为可复用组件。
3. 优先使用 daisyUI 组件，不随意创建新样式。
4. 所有页面支持中文、只考虑桌面端。
5. 不随意引入 React、Vue 等额外框架。
6. 修改功能时运行自动化测试，检查其他页面是否受影响。

样式锁定 Tailwind CSS 4.3.4 和 daisyUI 5.7.50，不要换成别的版本，也不要改回 daisyUI 4 的类名。入口保持 `assets/css/input.css` 里的 `@import "tailwindcss"`、`@plugin "daisyui"` 和指向 Django 模板的 `@source`。

业务页面继承 `templates/base.html`。车间终端继承 `templates/terminal_base.html`。按钮、表格、弹窗、表单、提示消息使用 `templates/components/` 里的 include，不要在新页面里另写一套。

新页面要在 `ui/smoke.py` 的 `PAGES` 里加一行，让冒烟测试覆盖到。

后台管理使用 Django Unfold。新的 `ModelAdmin` 继承 `unfold.admin.ModelAdmin`。不要把 Unfold、Alpine、React 或 Vue 带到业务页面。

## 提交前跑全量测试

提交前运行：

```bash
./scripts/build-css.sh
git diff --exit-code -- static/css/app.css
uv run ruff check .
uv run ruff format --check .
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py test
```

`./scripts/build-css.sh` 在本机还没有 Tailwind CLI 时会下载对应平台的二进制到 `var/tailwindcss/`。没有改样式时，编译结果必须和已提交的 `static/css/app.css` 一致。改了 class 就要把新的 CSS 一起提交。

CI 执行同一组检查。测试没过不要提交。
