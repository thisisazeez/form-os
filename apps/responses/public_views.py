"""
PUBLIC response WRITE endpoints + the HTML runtime host page (openapi "Public Survey" tag).

All write endpoints are unauthenticated (``security: []``) and rate-limited per IP +
per survey/response (REVIEW.md §3.5). They are the take-a-survey loop:

operationId mapping (openapi paths — spec uses summaries; mapped by path+method):
  POST  /api/v1/{org_slug}/public/surveys/{survey_id}/responses
        → PublicResponseCreateView.post   (create partial → returns edit_token; ADR-009)
  PATCH /api/v1/{org_slug}/public/responses/{response_id}?edit_token=...
        → PublicResponseDetailView.patch  (save-as-you-go merge)
  POST  /api/v1/{org_slug}/public/responses/{response_id}/finalize?edit_token=...
        → PublicResponseFinalizeView.post (mark finished + consume single-use link)
  POST  /api/v1/{org_slug}/public/surveys/{survey_id}/displays
        → PublicDisplayView.post          (impression tracking; view→start→finish funnel)

HTML host page (NOT under /api — tenant HTML family `/{org_slug}/s/{survey_id}`):
  GET   /{org_slug}/s/{survey_id}  → public_runtime_host  (mounts the React runtime island)

CONTACT ASSOCIATION is server-derived from ``link_token`` ONLY (ADR-009 / rbac.md §11): a
client-supplied contact_id is never trusted — ``public_services`` reads
``SurveyLink.contact_id`` from a valid token. The ``edit_token`` (ADR-009) authorizes
PATCH/finalize for ONE response; it is a signed value (no DB column) verified with a max-age.

The presigned ``upload-url`` endpoint is DEFERRED (a follow-on, matching the frontend's
file-upload deferral) and is intentionally NOT implemented/routed here.
"""
from __future__ import annotations

from django.conf import settings
from django.db import connection
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.clickjacking import xframe_options_exempt
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response as DRFResponse
from rest_framework.views import APIView

from apps.surveys.enums import SurveyStatus
from apps.surveys.models import Survey

from . import public_services
from .models import Response
from .public_serializers import (
    PublicResponseCreateSerializer,
    PublicResponseHandleSerializer,
    PublicResponsePatchSerializer,
)
from .public_services import (
    AnswerValidationError,
    LinkGoneError,
    LinkRequiredError,
    RequiredAnswersMissing,
    SurveyClosedError,
)
from .throttling import (
    PublicDisplayThrottle,
    PublicResponseCreateThrottle,
    PublicResponseEditThrottle,
)

# --------------------------------------------------------------------------- #
#  Shared helpers                                                              #
# --------------------------------------------------------------------------- #
_REQUEST_META_IP_HEADERS = ("HTTP_X_FORWARDED_FOR", "REMOTE_ADDR")


def _client_ip(request) -> str | None:
    """Best-effort client IP for ``Response.meta.ip`` (PII; redacted on read — rbac.md §11).

    Prefers the first hop of ``X-Forwarded-For`` (proxied deploys) then ``REMOTE_ADDR``.
    Stored server-side only; the authenticated read serializer strips it.
    """
    for header in _REQUEST_META_IP_HEADERS:
        raw = request.META.get(header)
        if raw:
            return raw.split(",")[0].strip()
    return None


def _server_meta(request, client_meta: dict | None, *, capture_ip: bool = False) -> dict:
    """Merge client-supplied meta {url, source, userAgent} with server-derived ip/userAgent.

    The server is authoritative for ``ip`` (never client-trusted) and fills ``userAgent``
    from the request header if the client didn't send one (openapi: "Server augments with
    IP/deviceType server-side").

    ``ip`` is captured ONLY when the survey opts in via ``capture_ip`` (Settings tab; off
    by default — GDPR-friendly). A client-supplied ``ip`` in ``meta`` is always dropped when
    capture is off (the client can't force PII capture).
    """
    meta = dict(client_meta or {})
    meta.pop("ip", None)  # never trust a client-supplied ip; the server is authoritative
    if capture_ip:
        ip = _client_ip(request)
        if ip:
            meta["ip"] = ip
    if not meta.get("userAgent"):
        ua = request.META.get("HTTP_USER_AGENT")
        if ua:
            meta["userAgent"] = ua
    return meta


def _handle_payload(response: Response, *, reference=None) -> dict:
    """Build the ``PublicResponseHandle`` dict (id, finished, edit_token, expires_at).

    Mints a fresh edit_token each time so the runtime always holds a token whose expiry it
    knows (the token is stateless — minting again is free and keeps expires_at honest)."""
    return {
        "id": response.id,
        "finished": response.finished,
        "edit_token": public_services.make_edit_token(response.id),
        "expires_at": public_services.edit_token_expiry(reference),
    }


def finalize_handle_body(response: Response, *, reference=None) -> dict:
    """The finalize-path response body: the serialized handle PLUS the over-quota fields when, and
    ONLY when, this response was marked over-quota (an over-quota finalize).

    QUOTAS (additive): merges ``over_quota`` + ``quota_full_ending_id`` onto the serializer output
    so the runtime can override the ending. For a non-over-quota finalize (incl. EVERY survey with
    no quotas) the keys are OMITTED entirely, so the body is BYTE-IDENTICAL to the pre-quota
    {id, finished, edit_token, expires_at} shape."""
    body = dict(PublicResponseHandleSerializer(_handle_payload(response, reference=reference)).data)
    if getattr(response, "over_quota", False):
        body["over_quota"] = True
        body["quota_full_ending_id"] = getattr(response, "quota_full_ending_id", None)
    return body


def _get_published_survey_or_404(survey_id) -> Survey:
    survey = public_services.get_published_survey(survey_id)
    if survey is None:
        raise _PublishedSurveyNotFound()
    return survey


class _PublishedSurveyNotFound(Exception):
    """Internal sentinel → mapped to a uniform 404 body in the view."""


def _not_found_response() -> DRFResponse:
    return DRFResponse(
        {"detail": "Survey not found.", "code": "not_found"},
        status=status.HTTP_404_NOT_FOUND,
    )


def _get_response_for_edit_or_none(response_id) -> Response | None:
    """Load a Response (with its survey chain) by id, or None for a bad/unknown id."""
    try:
        return Response.objects.select_related(
            "survey", "survey__environment", "survey__environment__workspace"
        ).get(pk=response_id)
    except (Response.DoesNotExist, ValueError):
        return None


def _bad_edit_token() -> DRFResponse:
    return DRFResponse(
        {"detail": "Invalid or expired edit token.", "code": "bad_edit_token"},
        status=status.HTTP_403_FORBIDDEN,
    )


# --------------------------------------------------------------------------- #
#  POST create response                                                        #
# --------------------------------------------------------------------------- #
class PublicResponseCreateView(APIView):
    """POST create a (partial) response → returns id + edit_token (ADR-009).

    openapi: /public/surveys/{survey_id}/responses. Unauthenticated, strict per-IP/per-survey
    throttle. Contact derived from link_token only.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [PublicResponseCreateThrottle]

    def post(self, request, survey_id):
        try:
            survey = _get_published_survey_or_404(survey_id)
        except _PublishedSurveyNotFound:
            return _not_found_response()

        serializer = PublicResponseCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        # Auto-close gate (openapi POST-create 410 "survey closed (cap/date)"): refuse NEW
        # responses once close_on.by_date has passed or by_response_cap finished responses
        # exist. Checked before link resolution so a closed survey 410s uniformly.
        try:
            public_services.assert_accepting_responses(survey)
        except SurveyClosedError as exc:
            return DRFResponse(
                {"detail": str(exc), "code": "survey_closed"}, status=status.HTTP_410_GONE
            )

        # Resolve the link: a single_use survey needs a usable token (410 otherwise), and
        # the link is the sole trusted contact source.
        try:
            link = public_services.resolve_link(survey, payload.get("link_token"))
        except (LinkGoneError, LinkRequiredError) as exc:
            return DRFResponse(
                {"detail": str(exc), "code": "link_gone"}, status=status.HTTP_410_GONE
            )

        try:
            response = public_services.create_response(
                survey=survey,
                data=payload.get("data") or {},
                language=payload.get("language") or "default",
                link=link,
                meta=_server_meta(request, payload.get("meta"), capture_ip=survey.capture_ip),
            )
        except AnswerValidationError as exc:
            return DRFResponse(
                {
                    "detail": "Answer payload references unknown question ids.",
                    "errors": {"data": exc.unknown_keys},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        body = PublicResponseHandleSerializer(_handle_payload(response)).data
        return DRFResponse(body, status=status.HTTP_201_CREATED)


# --------------------------------------------------------------------------- #
#  PATCH response (save-as-you-go)                                             #
# --------------------------------------------------------------------------- #
class PublicResponseDetailView(APIView):
    """PATCH partial answers for an in-progress response (openapi PATCH /public/responses/{id}).

    Authorized by the ``edit_token`` query param (ADR-009) — single-response scope + expiry.
    Per-IP/per-response throttle.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [PublicResponseEditThrottle]

    def patch(self, request, response_id):
        edit_token = request.query_params.get("edit_token", "")
        # Verify the token BEFORE any DB existence is revealed: a bad token → 403 regardless
        # of whether the response exists (no existence oracle via the token surface).
        if not public_services.verify_edit_token(edit_token, response_id):
            return _bad_edit_token()

        response = _get_response_for_edit_or_none(response_id)
        if response is None:
            # Token verified for this id but no such response (deleted/never existed) → 404.
            raise Http404("Response not found.")

        serializer = PublicResponsePatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        try:
            response = public_services.patch_response(
                response=response,
                data=payload.get("data") or {},
                language=payload.get("language"),
            )
        except AnswerValidationError as exc:
            return DRFResponse(
                {
                    "detail": "Answer payload references unknown question ids.",
                    "errors": {"data": exc.unknown_keys},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return DRFResponse(PublicResponseHandleSerializer(_handle_payload(response)).data)


# --------------------------------------------------------------------------- #
#  POST finalize                                                               #
# --------------------------------------------------------------------------- #
class PublicResponseFinalizeView(APIView):
    """POST mark the response finished (openapi POST /public/responses/{id}/finalize).

    Validates required answers (static + logic-required) and consumes any single-use link,
    atomically (public_services.finalize_response). Authorized by ``edit_token``.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [PublicResponseEditThrottle]

    def post(self, request, response_id):
        edit_token = request.query_params.get("edit_token", "")
        if not public_services.verify_edit_token(edit_token, response_id):
            return _bad_edit_token()

        response = _get_response_for_edit_or_none(response_id)
        if response is None:
            raise Http404("Response not found.")

        try:
            response = public_services.finalize_response(response=response)
        except RequiredAnswersMissing as exc:
            return DRFResponse(
                {
                    "detail": "Required answers are missing.",
                    "errors": {"required": exc.missing},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # finalize_handle_body adds the over-quota fields ONLY for an over-quota response, so a
        # non-over-quota finalize (incl. every survey with no quotas) returns the byte-identical
        # pre-quota handle shape.
        return DRFResponse(finalize_handle_body(response))


# --------------------------------------------------------------------------- #
#  POST display                                                                #
# --------------------------------------------------------------------------- #
class PublicDisplayView(APIView):
    """POST record a display/impression (openapi POST /public/surveys/{id}/displays).

    Powers the view→start→finish funnel. Contact derived from an optional link_token (the
    only trusted source). Per-IP/per-survey throttle. The runtime island calls this on mount
    (NOT the HTML host view — keeps the static render side-effect-free and bot/preview-safe).
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [PublicDisplayThrottle]

    def post(self, request, survey_id):
        try:
            survey = _get_published_survey_or_404(survey_id)
        except _PublishedSurveyNotFound:
            return _not_found_response()

        # link_token is OPTIONAL here (anonymous displays are valid). If present and usable,
        # the display is tied to the link's contact; a consumed/expired link is tolerated for
        # a display (it's a low-stakes impression) → treat as anonymous rather than 410.
        link = None
        link_token = (request.data or {}).get("link_token") if hasattr(request, "data") else None
        if link_token:
            try:
                link = public_services.resolve_link(survey, link_token)
            except (LinkGoneError, LinkRequiredError):
                link = None

        display = public_services.record_display(survey=survey, link=link)
        return DRFResponse(
            {"display_id": display.id}, status=status.HTTP_201_CREATED
        )


# --------------------------------------------------------------------------- #
#  HTML runtime host page  (GET /{org_slug}/s/{survey_id})                      #
# --------------------------------------------------------------------------- #
@xframe_options_exempt
def public_runtime_host(request, survey_id):
    """Render the minimal HTML page that mounts the React runtime island (PLAN §8; ADR-001).

    Tenant HTML family: the org slug was stripped by TenantSlugMiddleware, so this view runs
    with the org's schema active. It looks up the PUBLISHED survey and renders
    ``templates/public/runtime.html``, injecting the survey id + link token + org slug via a
    ``{% json_script %}`` tag the island reads. A non-published/unknown survey → 404 (no leak).

    No data side effects here (no Display recorded) — the island records the display via
    POST /public/surveys/{id}/displays on mount, so previews/bots fetching the HTML don't
    inflate the funnel.

    EMBED SUPPORT (WS-7 Share page): ``@xframe_options_exempt`` removes the global
    ``X-Frame-Options: DENY`` (Django's default, set by ``XFrameOptionsMiddleware``) from THIS
    response only, so the public survey can be ``<iframe>``-embedded on a customer's site. The
    exemption is per-response (sets ``response.xframe_options_exempt = True`` for the middleware
    to honor) — the global setting and middleware are untouched, so every AUTHENTICATED admin
    page still ships ``DENY`` and keeps its clickjacking protection. This page is the public,
    unauthenticated runtime: it carries no admin session and is meant to be embedded, so framing
    it is the intended use, not an attack surface.
    """
    try:
        # select_related the workspace so the white-label flag is read without an extra query.
        survey = (
            Survey.objects.select_related("environment__workspace").get(
                pk=survey_id, status=SurveyStatus.PUBLISHED
            )
        )
    except (Survey.DoesNotExist, ValueError) as exc:
        raise Http404("Survey not found.") from exc

    tenant = getattr(connection, "tenant", None)
    org_slug = getattr(tenant, "slug", None)

    # White-label / remove-branding (enterprise) lives on the Workspace (apps.workspaces). When
    # True, the public runtime hides the "Powered by OpenSurvey" footer. We thread it via the
    # runtime host config (data-remove-branding + runtime_config) — NOT via PublicSurvey — so the
    # public survey JSON stays a pure survey definition. Defaults to False (branding shown).
    remove_branding = bool(survey.environment.workspace.remove_branding)

    vite_conf = settings.DJANGO_VITE.get("default", {})
    manifest_path = vite_conf.get("manifest_path")
    vite_built = bool(vite_conf.get("dev_mode")) or (
        manifest_path is not None and manifest_path.exists()
    )

    # link_token rides through from the URL so the island can pass it to the JSON endpoints.
    link_token = request.GET.get("link_token", "")

    context = {
        "survey_id": str(survey.id),
        "survey_name": survey.name,
        "org_slug": org_slug,
        "link_token": link_token,
        # Workspace white-label flag → data-remove-branding on the mount node (mirrors the other
        # data-* attrs). The island reads el.dataset.removeBranding === "true".
        "remove_branding": remove_branding,
        # The JSON the island reads via #opensurvey-runtime-config (json_script).
        "runtime_config": {
            "surveyId": str(survey.id),
            "orgSlug": org_slug,
            "linkToken": link_token,
            # ROOT base only — the runtime island (frontend/.../runtime/api.ts) builds
            # `${apiBase}/{orgSlug}/public/...` itself, so passing the org/public prefix here
            # double-prefixes the request. The island also defaults to "/api/v1".
            "apiBase": "/api/v1",
            # Mirrored here too (the json_script is the documented richer-wiring path); the island
            # currently reads the dataset attr, but keeping both in sync avoids a future drift.
            "removeBranding": remove_branding,
        },
        "vite_built": vite_built,
    }
    return render(request, "public/runtime.html", context)
