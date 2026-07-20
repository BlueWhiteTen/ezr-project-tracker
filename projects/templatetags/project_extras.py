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
    """Return the user's chosen avatar colour, falling back to pk % 8 palette."""
    try:
        pk = int(user_pk)
        from projects.models import StaffProfile
        profile = StaffProfile.objects.filter(user_id=pk).first()
        if profile and profile.colour:
            return profile.colour
        return AVATAR_COLOURS[pk % len(AVATAR_COLOURS)]
    except (ValueError, TypeError):
        return AVATAR_COLOURS[0]


@register.simple_tag
def user_colour_map():
    """Return a JS object literal mapping user id → avatar colour."""
    from projects.models import StaffProfile
    from django.contrib.auth.models import User
    profiles = {p.user_id: p.colour for p in StaffProfile.objects.exclude(colour='').only('user_id', 'colour')}
    users = User.objects.only('id')
    parts = []
    for u in users:
        colour = profiles.get(u.id) or AVATAR_COLOURS[u.id % len(AVATAR_COLOURS)]
        parts.append(f'{u.id}:"{colour}"')
    return '{' + ','.join(parts) + '}'
