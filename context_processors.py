from django.conf import settings


def feature_flags(request):
    """Makes FEATURE_FLAGS available in every template as `features`, so
    nav links and page sections can hide themselves without each view
    needing to pass it explicitly."""
    return {'features': settings.FEATURE_FLAGS}
