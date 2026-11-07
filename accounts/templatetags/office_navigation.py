from django import template

from accounts.navigation import office_navigation

register = template.Library()
register.simple_tag(office_navigation)
