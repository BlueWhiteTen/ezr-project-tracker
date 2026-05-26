from django import template

register = template.Library()

AVATAR_COLOURS = [
    '#d4700a',  # orange
    '#2563eb',  # blue
    '#16a34a',  # green
    '#9333ea',  # purple
    '#dc2626',  # red
    '#0891b2',  # cyan
    '#ca8a04',  # yellow
    '#be185d',  # pink
]

@register.filter
def avatar_colour(user_pk):
    try:
        return AVATAR_COLOURS[int(user_pk) % len(AVATAR_COLOURS)]
    except (ValueError, TypeError):
        return AVATAR_COLOURS[0]
