from django.conf import settings


def institution(request):
    return {"institution_name": settings.INSTITUTION_NAME}
