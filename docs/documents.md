# Documents

A patient gets a clinical document by opening a link the bot sent them. They have no CARE
account, so the link itself is the permission.

## What a link is

`DocumentLink` is a row naming one patient, one record, and an expiry — plus a 256-bit
token. The token is the whole capability: whoever holds it sees the document, which is why
it is unguessable, expires (`DOCUMENT_LINK_TTL_SECONDS`, 7 days), and is rate limited per
token rather than per IP.

The URL carries no document type:

```
https://<care_fe-host>/public/documents/<token>
```

Every document uses that one address. The token decides what comes back, so adding a new
document type changes no URL, no provider template, and no configuration.

`DOCUMENT_PAGE_BASE_URL` sets the care_fe origin; it falls back to `CURRENT_DOMAIN`.

## What happens when it is opened

1. The care_fe page asks `GET /api/care_im_wrapper/public/documents/<token>/`.
2. The endpoint checks the token is live, finds the registered kind for its
   `document_type`, and builds that kind's payload.
3. The page draws it.

Unknown, expired, revoked and unregistered tokens all 404 identically, so a caller cannot
learn whether a token ever existed.

## Two modes

CARE itself splits documents in two, and this follows that split.

| Mode | What it is | What the endpoint returns |
| --- | --- | --- |
| `render` | care_fe has a print view and no PDF exists anywhere — the browser makes one when staff press Print | the record's data, and the facility's print template config |
| `file` | a pre-rendered artifact in storage (a template-builder report, or an uploaded file) | a short-lived signed URL to the file |

A `render` kind carries a `template_slug` matching care_fe's `PrintTemplateType`, so the
patient's page uses the same facility letterhead staff see. A `file` kind has no print view
and so no slug.

## Registered kinds

| Kind | Mode | Subject | Notes |
| --- | --- | --- | --- |
| `diagnostic_report` | `render` | `DiagnosticReport` | Drawn as care_fe's diagnostic report print view. Uploaded files are attachments *inside* it — all of them, not the newest — so a report with no attachment is still deliverable. |
| `discharge_summary` | `file` | `ReportUpload` | Generated from the facility's `discharge_summary` template, resolved by `template_type` as care_fe's picker does. |

## Adding a document type

Say prescriptions, which care_fe has a print view for.

**1. Register the kind** in `documents/kinds.py`:

```python
def _build_prescription(link: DocumentLink) -> dict[str, Any]:
    from care.emr.models.medication_request import MedicationRequest
    from care.emr.resources.medication.request.spec import MedicationRequestReadSpec

    obj = MedicationRequest.objects.filter(external_id=link.object_external_id).first()
    if obj is None:
        raise DocumentUnavailableError("Prescription no longer exists.")
    return {
        "prescription": MedicationRequestReadSpec.serialize(obj).to_json(),
        "facility": _facility_payload(obj.encounter.facility),
    }


register(
    DocumentKind(
        slug="prescription",
        mode=RENDER,
        object_kind=DocumentLinkObjectKind.PRESCRIPTION,
        build=_build_prescription,
        template_slug="prescription",  # care_fe PrintTemplateType.prescription
    )
)
```

Serialise with core's own read spec wherever one exists. The page renders a copy of
care_fe's print view, so it needs the shape that view already consumes; a hand-rolled
payload is a second thing to keep in step with core.

**2. Add the object kind** to `DocumentLinkObjectKind` and run `makemigrations`. It is one
`AlterField` on the choices, with no data change.

**3. Mint a link** wherever the document should be sent, then put
`build_document_url(link)` in the message.

**4. Add a renderer** in the plug frontend, keyed on the same slug. See its
`how-to/support-a-new-document` guide.

A `file` kind is shorter: reuse `_build_stored_file` and skip step 4 entirely, since the
page already knows how to hand a file to the browser.

## Things worth knowing

**No fallback.** A lab report resolves to its own record or to nothing. It never falls back
to an encounter or discharge document — those describe a different subject, and sending one
in place of a lab result misrepresents what was asked for.

**A `render` kind has nothing to redirect to.** There is no stored file behind it, so the
link must point at the care_fe page. `build_document_url` does that for every kind.

**Tag names are resolved server-side.** care_fe prints a qualified range's conditions and
looks up tag names through an authenticated endpoint. A patient has no session for that, so
`_resolve_condition_tag_displays` resolves them into the payload instead.
