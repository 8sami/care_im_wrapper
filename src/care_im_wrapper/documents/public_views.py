"""Public, unauthenticated document payload for the patient-facing page.

Sibling of ``document_redirect``: same capability model, same failure discipline, but it
returns the document's data rather than bouncing to storage, so the page can draw
care_fe's print view for a patient who has no CARE account.

There is no user here by design. The token in the URL is the capability; everything this
endpoint will disclose is fixed by the DocumentLink that was minted for one patient and
one record, never by anything the caller sends.
"""

from __future__ import annotations

import logging

from django.http import Http404, HttpRequest, JsonResponse

from care_im_wrapper.core.rate_limit import is_rate_limited
from care_im_wrapper.documents import kinds
from care_im_wrapper.documents.exceptions import DocumentUnavailableError
from care_im_wrapper.models import DocumentLink
from care_im_wrapper.settings import plugin_settings

logger = logging.getLogger(__name__)


def public_document(request: HttpRequest, token: str) -> JsonResponse:
    """
    Missing, invalid, expired, and unknown-kind tokens all 404 identically -- a caller
    learns nothing from the difference, including whether a token ever existed.

    Rate-limited per token rather than per client IP, for the reason given in
    ``document_redirect``: behind a reverse proxy every request shares one REMOTE_ADDR,
    so an IP-keyed limit is a single global one. The token is 256 bits, so guessing is
    not the threat; capping reuse of a forwarded link is.
    """
    if is_rate_limited(
        f"doc_payload:{token}",
        window=int(plugin_settings.DOCUMENT_LINK_RATE_LIMIT_WINDOW_SECONDS),
        max_hits=int(plugin_settings.DOCUMENT_LINK_RATE_LIMIT_MAX),
    ):
        return JsonResponse({"detail": "Too many requests."}, status=429)

    link = DocumentLink.objects.filter(token=token).first()
    if link is None or not link.is_valid():
        raise Http404

    kind = kinds.get(link.document_type)
    if kind is None:
        logger.error(
            "public_document: DocumentLink %s has unregistered document_type=%s",
            link.external_id,
            link.document_type,
        )
        raise Http404

    try:
        body = kind.build(link)
    except DocumentUnavailableError:
        # The subject was deleted or its file became unreadable after the link was minted.
        logger.warning("public_document: document unavailable for link=%s", link.external_id)
        raise Http404 from None
    except Exception:
        logger.exception("public_document: failed to build payload for link=%s", link.external_id)
        raise Http404 from None

    link.access_count += 1  # pyright: ignore[reportOperatorIssue]
    link.save(update_fields=["access_count"])
    logger.info(
        "DocumentLink payload served: document_type=%s object_kind=%s patient=%s access_count=%s",
        link.document_type,
        link.object_kind,
        link.patient_external_id,
        link.access_count,
    )

    return JsonResponse(
        {
            "kind": kind.slug,
            "mode": kind.mode,
            "template_slug": kind.template_slug,
            **body,
        }
    )
