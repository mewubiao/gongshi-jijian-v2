from ui.access import gallery_allowed


def gallery_flags(request):
    return {"show_ui_gallery": gallery_allowed(request)}
