"""Public, unauthenticated signed-URL entry point for a stored document.

The token is the capability; there is no user auth here, by design.

Only stored-file documents resolve here. A rendered one (a lab report) has no file to
presign -- it is drawn by the care_fe page, and its link points there directly. Such a
token 404s here rather than being forwarded, so a message template still addressed at
this route fails visibly instead of quietly working through a redirect."""

from __future__ import annotations

import logging

from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect

from care_im_wrapper.core.rate_limit import is_rate_limited
from care_im_wrapper.models import DocumentLink
from care_im_wrapper.settings import plugin_settings

logger = logging.getLogger(__name__)


def document_redirect(request: HttpRequest, token: str) -> HttpResponse:
    """
    Missing, invalid, and expired tokens all 404 identically -- no enumeration signal.

    Rate-limited per token, not per client IP: behind a reverse proxy REMOTE_ADDR is the
    proxy for every request, so an IP-keyed limit is really a single global one shared by
    every patient. The token is 256 bits, so guessing is not the threat -- capping reuse
    of one leaked/forwarded link is.
    """
    if is_rate_limited(
        f"doc_link:{token}",
        window=int(plugin_settings.DOCUMENT_LINK_RATE_LIMIT_WINDOW_SECONDS),
        max_hits=int(plugin_settings.DOCUMENT_LINK_RATE_LIMIT_MAX),
    ):
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
