from django import template

register = template.Library()


@register.filter
def duration(value):
    if value is None:
        return "—"
    hours, remainder = divmod(int(value), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours} 小时 {minutes} 分 {seconds} 秒"
