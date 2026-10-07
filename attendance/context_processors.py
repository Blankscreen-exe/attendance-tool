from .models import LeaveRequest, MissingTimeRequest, RequestStatus


def pending_requests(request):
    """Number of requests waiting for a decision, for the admin navigation."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not user.is_staff:
        return {}
    count = sum(
        model.objects.filter(status=RequestStatus.PENDING).count()
        for model in (MissingTimeRequest, LeaveRequest)
    )
    return {"pending_request_count": count}
