from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpResponseForbidden
from django.shortcuts import render

from ui.access import gallery_allowed
from ui.forms import DemoReportForm

DEMO_ROWS = [
    ["WO-1001", "冲压", "12"],
    ["WO-1002", "焊接", "8"],
    ["WO-1003", "装配", "5"],
    ["WO-1004", "喷涂", "20"],
    ["WO-1005", "检验", "3"],
    ["WO-1006", "包装", "16"],
    ["WO-1007", "下料", "9"],
]

TABLE_COLUMNS = ["工单", "工序", "完工数量"]


def home(request):
    return render(request, "home.html")


def terminal(request):
    return render(request, "terminal.html")


def gallery(request):
    if not gallery_allowed(request):
        return HttpResponseForbidden("组件示例仅在调试模式或登录后可见。")

    messages.info(request, "这是统一的提示消息。")
    if request.method == "POST":
        form = DemoReportForm(request.POST, prefix="demo")
        if form.is_valid():
            messages.success(request, "示例表单已通过校验。")
            form = DemoReportForm(prefix="demo")
    else:
        form = DemoReportForm(prefix="demo")

    sample = DemoReportForm(
        data={
            "sample-worker": "",
            "sample-quantity": "0",
            "sample-shift": "day",
        },
        prefix="sample",
    )
    paginator = Paginator(DEMO_ROWS, 3)
    page = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "ui/gallery.html",
        {
            "form": form,
            "sample_form": sample,
            "columns": TABLE_COLUMNS,
            "rows": list(page.object_list),
            "page": page,
        },
    )


def gallery_modal(request):
    if not gallery_allowed(request):
        return HttpResponseForbidden("组件示例仅在调试模式或登录后可见。")
    return render(request, "ui/modal_body.html")
