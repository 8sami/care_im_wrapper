"""Public, unauthenticated signed-URL entry point. The token is the capability; there is
no user auth here, by design."""

from __future__ import annotations

import logging

from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect

from care_im_wrapper.core.rate_limit import is_rate_limited
from care_im_wrapper.models import DocumentLink

logger = logging.getLogger(__name__)


def document_redirect(request: HttpRequest, token: str) -> HttpResponse:
    """
    Missing, invalid, and expired tokens all 404 identically -- no enumeration signal.
    Rate-limited by client IP (checked before the token is even looked up) so a burst of
    guesses from one source can't be used to brute-force a valid token.
    """
    client_ip = request.META.get("REMOTE_ADDR", "")
    if is_rate_limited(f"doc_link:{client_ip}"):
        return HttpResponse(status=429)

    link = DocumentLink.objects.filter(token=token).first()
    if link is None or not link.is_valid():
        raise Http404

    try:
        read_url = link.mint_read_url()
    except Exception:
        logger.exception("document_redirect: failed to mint read url for document_link=%s", link.external_id)
        raise Http404 from None

    link.access_count += 1  # pyright: ignore[reportOperatorIssue]
    link.save(update_fields=["access_count"])
    logger.info(
        "DocumentLink accessed: document_type=%s object_kind=%s patient=%s access_count=%s",
        link.document_type,
        link.object_kind,
        link.patient_external_id,
        link.access_count,
    )

    return HttpResponseRedirect(read_url)
