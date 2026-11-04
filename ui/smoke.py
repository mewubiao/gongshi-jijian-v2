from dataclasses import dataclass


@dataclass(frozen=True)
class PageCheck:
    """一条页面冒烟检查。新增页面时往 PAGES 加一行。"""

    url: str
    status: int = 200
    contains: tuple[str, ...] = ()
    absent: tuple[str, ...] = ()
    access: str = "public"


# access: public 直接打开；debug 在 DEBUG 下打开；auth 登录后打开。
PAGES = (
    PageCheck(
        "/",
        contains=(
            'lang="zh-Hans"',
            'id="app-topnav"',
            'id="app-sidebar"',
            'id="app-content"',
            "htmx.min.js",
            "css/app.css",
            "工时计件",
        ),
    ),
    PageCheck(
        "/terminal/",
        contains=(
            'lang="zh-Hans"',
            'id="app-terminal"',
            'id="app-topnav"',
            "btn-xl",
            "请刷工牌",
        ),
        absent=('id="app-sidebar"',),
    ),
    PageCheck(
        "/ui/",
        access="debug",
        contains=(
            'id="app-sidebar"',
            "组件示例",
            "暂无数据",
            'data-slot="pagination"',
            'id="demo-modal"',
            "这个字段是必填项",
            "input-error",
        ),
    ),
    PageCheck(
        "/ui/modal/",
        access="debug",
        contains=("由 HTMX",),
        absent=('id="app-sidebar"', 'id="app-topnav"'),
    ),
    PageCheck("/health/", contains=("ok",)),
    PageCheck("/admin/", status=302),
    PageCheck(
        "/admin/login/",
        contains=("登录", "用户名", 'lang="zh-hans"'),
    ),
)
