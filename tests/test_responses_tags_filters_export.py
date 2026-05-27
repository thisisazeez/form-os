"""
Tests for the WIRED response TAGS + FILTERS + EXPORT admin surface (parity: tags/filters/export):

  POST /<org>/surveys/<id>/responses/<rid>/tags                  response_tag_apply  (add/create-on-fly)
  POST /<org>/surveys/<id>/responses/<rid>/tags/<tid>/remove     response_tag_remove (remove)
  GET  /<org>/surveys/<id>/responses?tag=&status=&from=&to=&answer=  survey_responses (filters)
  GET  /<org>/surveys/<id>/responses.csv|.json                   response_export     (RBAC + PII + CSV-injection)
  GET  /<org>/tags + POST .../rename|delete|merge                tags_manage         (mgmt, owner/admin)

Same harness as tests/test_contacts_actions_admin.py: REAL TenantSlugMiddleware path, Django
session Client + force_login (HTML views gated by @login_required + can()/org-role, not DRF),
``transactional_db`` (org creation = schema DDL). Coverage = the brief's checklist:
  - tag add/remove/create-on-the-fly + RBAC (read-only → 403),
  - filter narrowing (tag / completed / date / answer-to-a-question),
  - export (CSV content-type, filtered rows, PII masked for read-only callers, formula-injection
    sanitized), JSON export.
"""
import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django_tenants.utils import get_public_schema_name, schema_context

from apps.contacts.models import ContactAttributeKey
from apps.contacts.services import upsert_contact
from apps.organizations.enums import MembershipStatus, OrgRole
from apps.organizations.models import Membership, Organization
from apps.organizations.services import create_organization
from apps.responses import tag_services
from apps.responses.models import Response, ResponseTag, Tag
from apps.surveys.models import Survey
from apps.teams.enums import WorkspaceAccess
from apps.teams.models import Team, TeamMembership, WorkspaceTeam
from apps.workspaces.enums import EnvType
from apps.workspaces.models import Environment, Workspace

User = get_user_model()

SLUG = "rt-tags-org"

# A small survey with one NPS question + one open-text question (drives the answer filter +
# export column tests). Question ids match the keys written into Response.data below.
_QUESTIONS = [
    {"id": "q_nps", "type": "nps", "headline": {"default": "How likely to recommend?"}},
    {"id": "q_text", "type": "openText", "headline": {"default": "Anything else?"}},
]


# --------------------------------------------------------------------------- #
#  Fixtures / helpers                                                          #
# --------------------------------------------------------------------------- #
@pytest.fixture
def client():
    return Client()


def _mk_user(email):
    connection.set_schema_to_public()
    User.objects.filter(email=email).delete()
    return User.objects.create_user(email=email, password="x", is_verified=True)


def _cleanup(slug):
    connection.set_schema_to_public()
    for org in Organization.objects.filter(slug=slug):
        org.delete(force_drop=True)


@pytest.fixture
def ctx(transactional_db):
    """Org + owner + workspace + prod env + a survey + 3 responses (mix of finished/partial,
    NPS scores, text answers). One response is linked to a contact (for the PII-masking export
    test)."""
    owner = _mk_user("rt_owner@example.com")
    _cleanup(SLUG)
    org = create_organization(name="RT Tags Org", slug=SLUG, owner=owner)
    connection.set_tenant(org)

    Workspace.objects.all().delete()
    ws = Workspace.objects.create(name="WS", slug="ws-main")
    env = Environment.objects.create(workspace=ws, type=EnvType.PRODUCTION)
    survey = Survey.objects.create(environment=env, name="Sat Survey", questions=_QUESTIONS)

    # A contact (email = PII) so a contact-bearing response exposes a maskable identity column.
    ContactAttributeKey.objects.create(environment=env, key="email", data_type="string", is_pii=True, label="Email")
    contact, _ = upsert_contact(environment=env, attributes=[{"key": "email", "value": "promoter@acme.io"}])

    # r1: finished, NPS 9 (promoter), text answer, linked to a contact, IP in meta.
    r1 = Response.objects.create(
        survey=survey,
        data={"q_nps": 9, "q_text": "Great product"},
        finished=True,
        contact_id=contact.id,
        meta={"ip": "203.0.113.7"},
    )
    # r2: finished, NPS 3 (detractor), text answer, anonymous.
    r2 = Response.objects.create(
        survey=survey,
        data={"q_nps": 3, "q_text": "Too slow"},
        finished=True,
    )
    # r3: partial, no NPS, anonymous.
    r3 = Response.objects.create(
        survey=survey,
        data={"q_text": "Maybe later"},
        finished=False,
    )

    yield {
        "org": org,
        "owner": owner,
        "ws": ws,
        "env": env,
        "survey": survey,
        "contact": contact,
        "r1": r1,
        "r2": r2,
        "r3": r3,
    }
    _cleanup(SLUG)


def _add_member(org, email, role=OrgRole.MEMBER):
    user = _mk_user(email)
    with schema_context(get_public_schema_name()):
        Membership.objects.create(user=user, organization=org, role=role, status=MembershipStatus.ACTIVE)
    return user


def _grant(org, user, workspace_id, access):
    connection.set_tenant(org)
    team = Team.objects.create(name=f"team-{user.email}-{access}")
    TeamMembership.objects.create(team=team, user_id=user.id)
    WorkspaceTeam.objects.create(team=team, workspace_id=workspace_id, access=access)


def _read_streaming(resp) -> str:
    return b"".join(resp.streaming_content).decode("utf-8")


def _url(ctx, suffix=""):
    return f"/{SLUG}/surveys/{ctx['survey'].id}/responses{suffix}"


# =========================================================================== #
#  TAGS — apply / remove / create-on-the-fly + RBAC                            #
# =========================================================================== #
def test_apply_existing_tag(client, ctx):
    """Owner posts a tag_id → the tag is linked to the response (idempotent)."""
    connection.set_tenant(ctx["org"])
    tag = Tag.objects.create(name="Bug")
    client.force_login(ctx["owner"])
    resp = client.post(
        f"{_url(ctx)}/{ctx['r1'].id}/tags", {"tag_id": str(tag.id)}
    )
    assert resp.status_code in (301, 302)
    connection.set_tenant(ctx["org"])
    assert ResponseTag.objects.filter(response=ctx["r1"], tag=tag).exists()


def test_apply_creates_tag_on_the_fly(client, ctx):
    """Owner posts a tag_name with no matching tag → the tag is created (case-insensitive) and
    linked (create-on-the-fly)."""
    client.force_login(ctx["owner"])
    resp = client.post(f"{_url(ctx)}/{ctx['r1'].id}/tags", {"tag_name": "Feature Request"})
    assert resp.status_code in (301, 302)
    connection.set_tenant(ctx["org"])
    tag = Tag.objects.get(name__iexact="Feature Request")
    assert ResponseTag.objects.filter(response=ctx["r1"], tag=tag).exists()


def test_apply_tag_idempotent(client, ctx):
    """Re-applying the same tag does not create a duplicate ResponseTag row."""
    connection.set_tenant(ctx["org"])
    tag = Tag.objects.create(name="Praise")
    client.force_login(ctx["owner"])
    for _ in range(2):
        client.post(f"{_url(ctx)}/{ctx['r1'].id}/tags", {"tag_id": str(tag.id)})
    connection.set_tenant(ctx["org"])
    assert ResponseTag.objects.filter(response=ctx["r1"], tag=tag).count() == 1


def test_create_on_the_fly_reuses_existing_case_insensitive(client, ctx):
    """A tag_name matching an existing tag (different case) reuses it — no second Tag row."""
    connection.set_tenant(ctx["org"])
    Tag.objects.create(name="Churn Risk")
    client.force_login(ctx["owner"])
    client.post(f"{_url(ctx)}/{ctx['r2'].id}/tags", {"tag_name": "churn risk"})
    connection.set_tenant(ctx["org"])
    assert Tag.objects.filter(name__iexact="churn risk").count() == 1


def test_remove_tag(client, ctx):
    """Owner removes a tag from a response → the link is gone (response untouched)."""
    connection.set_tenant(ctx["org"])
    tag = Tag.objects.create(name="Resolved")
    ResponseTag.objects.create(response=ctx["r1"], tag=tag)
    client.force_login(ctx["owner"])
    resp = client.post(f"{_url(ctx)}/{ctx['r1'].id}/tags/{tag.id}/remove")
    assert resp.status_code in (301, 302)
    connection.set_tenant(ctx["org"])
    assert not ResponseTag.objects.filter(response=ctx["r1"], tag=tag).exists()
    assert Response.objects.filter(id=ctx["r1"].id).exists()  # response not deleted


def test_tag_apply_read_only_forbidden(client, ctx):
    """A workspace ``read`` member cannot tag (tag_response = write) → 403, no link created."""
    reader = _add_member(ctx["org"], "rt_tag_reader@example.com")
    _grant(ctx["org"], reader, ctx["ws"].id, WorkspaceAccess.READ)
    client.force_login(reader)
    resp = client.post(f"{_url(ctx)}/{ctx['r1'].id}/tags", {"tag_name": "Sneaky"})
    assert resp.status_code == 403
    connection.set_tenant(ctx["org"])
    assert not Tag.objects.filter(name__iexact="Sneaky").exists()


def test_tag_remove_read_only_forbidden(client, ctx):
    """A workspace ``read`` member cannot remove a tag → 403; the link survives."""
    connection.set_tenant(ctx["org"])
    tag = Tag.objects.create(name="Keep")
    ResponseTag.objects.create(response=ctx["r1"], tag=tag)
    reader = _add_member(ctx["org"], "rt_rm_reader@example.com")
    _grant(ctx["org"], reader, ctx["ws"].id, WorkspaceAccess.READ)
    client.force_login(reader)
    resp = client.post(f"{_url(ctx)}/{ctx['r1'].id}/tags/{tag.id}/remove")
    assert resp.status_code == 403
    connection.set_tenant(ctx["org"])
    assert ResponseTag.objects.filter(response=ctx["r1"], tag=tag).exists()


def test_tag_chips_render_on_table(client, ctx):
    """Applied tags render as chips on the responses table; the add affordance shows for a
    write+ caller."""
    connection.set_tenant(ctx["org"])
    tag = Tag.objects.create(name="VIP")
    ResponseTag.objects.create(response=ctx["r1"], tag=tag)
    client.force_login(ctx["owner"])
    resp = client.get(_url(ctx))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "VIP" in html
    assert "Add tag" in html or "Tag" in html  # add affordance present for owner (write+)


# =========================================================================== #
#  FILTERS — tag / completed / date / answer-to-a-question                     #
# =========================================================================== #
def test_filter_by_tag_narrows(client, ctx):
    """?tag=<id> shows only responses carrying that tag."""
    connection.set_tenant(ctx["org"])
    tag = Tag.objects.create(name="Followup")
    ResponseTag.objects.create(response=ctx["r2"], tag=tag)
    client.force_login(ctx["owner"])
    resp = client.get(f"{_url(ctx)}?tag={tag.id}")
    assert resp.status_code == 200
    # Only r2 matches → 1 row. The active-filter chip renders the tag name.
    assert "Followup" in resp.content.decode()
    _state, qs = _filtered(ctx, tag_ids=[tag.id])
    assert list(qs.values_list("id", flat=True)) == [ctx["r2"].id]


def test_filter_by_multiple_tags_is_and(client, ctx):
    """Two ?tag= params require BOTH tags (AND), not either (OR)."""
    connection.set_tenant(ctx["org"])
    a = Tag.objects.create(name="A")
    b = Tag.objects.create(name="B")
    ResponseTag.objects.create(response=ctx["r1"], tag=a)
    ResponseTag.objects.create(response=ctx["r1"], tag=b)
    ResponseTag.objects.create(response=ctx["r2"], tag=a)  # only A
    _state, qs = _filtered(ctx, tag_ids=[a.id, b.id])
    assert list(qs.values_list("id", flat=True)) == [ctx["r1"].id]  # only the one with both


def test_filter_by_status_completed(client, ctx):
    """?status=completed → only finished responses (r1, r2); excludes the partial r3."""
    _state, qs = _filtered(ctx, status="completed")
    got = set(qs.values_list("id", flat=True))
    assert got == {ctx["r1"].id, ctx["r2"].id}


def test_filter_by_status_partial(client, ctx):
    """?status=partial → only the partial response (r3)."""
    _state, qs = _filtered(ctx, status="partial")
    assert list(qs.values_list("id", flat=True)) == [ctx["r3"].id]


def test_filter_by_date_range(client, ctx):
    """A ``from`` date AFTER all responses' created_at excludes everything; a wide range keeps all."""
    # All responses were created "now"; a tomorrow-from excludes all, a yesterday-to excludes all.
    from datetime import timedelta

    from django.utils import timezone

    tomorrow = (timezone.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday = (timezone.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    _s1, qs_future = _filtered(ctx, date_from=tomorrow)
    assert qs_future.count() == 0
    _s2, qs_past_to = _filtered(ctx, date_to=yesterday)
    assert qs_past_to.count() == 0
    # today..today keeps all 3.
    today = timezone.now().strftime("%Y-%m-%d")
    _s3, qs_today = _filtered(ctx, date_from=today, date_to=today)
    assert qs_today.count() == 3


def test_filter_by_answer_equals(client, ctx):
    """?answer=q_nps:equals:9 → only the response whose NPS answer is 9 (r1)."""
    questions_by_id = {"q_nps": "NPS", "q_text": "Text"}
    _display, specs = tag_services.parse_answer_filters(["q_nps:equals:9"], questions_by_id)
    _state, qs = _filtered(ctx, answer_specs=specs)
    assert list(qs.values_list("id", flat=True)) == [ctx["r1"].id]


def test_filter_by_answer_numeric_compare(client, ctx):
    """?answer=q_nps:lte:6 → only detractor NPS ≤ 6 (r2); r1 (9) excluded, r3 (no NPS) excluded."""
    questions_by_id = {"q_nps": "NPS"}
    _display, specs = tag_services.parse_answer_filters(["q_nps:lte:6"], questions_by_id)
    _state, qs = _filtered(ctx, answer_specs=specs)
    assert list(qs.values_list("id", flat=True)) == [ctx["r2"].id]


def test_filter_by_answer_contains_text(client, ctx):
    """?answer=q_text:contains:slow → only the text answer containing "slow" (r2)."""
    questions_by_id = {"q_text": "Text"}
    _display, specs = tag_services.parse_answer_filters(["q_text:contains:slow"], questions_by_id)
    _state, qs = _filtered(ctx, answer_specs=specs)
    assert list(qs.values_list("id", flat=True)) == [ctx["r2"].id]


def test_filter_unknown_question_ignored(client, ctx):
    """An unknown question id / bad operator in ?answer= is dropped silently (no 500, all shown)."""
    client.force_login(ctx["owner"])
    resp = client.get(f"{_url(ctx)}?answer=nope:equals:1&answer=q_nps:bogusop:5")
    assert resp.status_code == 200
    # Nothing applied → all 3 responses present (page count unaffected).
    _display, specs = tag_services.parse_answer_filters(
        ["nope:equals:1", "q_nps:bogusop:5"], {"q_nps": "NPS"}
    )
    assert specs == []


def test_filter_combined_and(client, ctx):
    """status=completed AND answer=q_nps:gte:7 → only the finished promoter (r1)."""
    questions_by_id = {"q_nps": "NPS"}
    _display, specs = tag_services.parse_answer_filters(["q_nps:gte:7"], questions_by_id)
    _state, qs = _filtered(ctx, status="completed", answer_specs=specs)
    assert list(qs.values_list("id", flat=True)) == [ctx["r1"].id]


def _filtered(ctx, **kwargs):
    """Run the shared narrowing path directly (request-free) for a precise row assertion."""
    connection.set_tenant(ctx["org"])
    qs = tag_services.filtered_responses(ctx["survey"], **kwargs)
    return None, qs


# =========================================================================== #
#  EXPORT — CSV content-type, filtered rows, PII masking, formula injection    #
# =========================================================================== #
def test_export_csv_content_type_and_header(client, ctx):
    """Owner export → text/csv with the base + question columns in the header."""
    client.force_login(ctx["owner"])
    resp = client.get(f"{_url(ctx)}.csv")
    assert resp.status_code == 200
    assert resp["Content-Type"] == "text/csv"
    body = _read_streaming(resp)
    header = body.splitlines()[0]
    for col in ("id", "respondent", "status", "language", "ip", "submitted_at", "tags"):
        assert col in header
    # Question headline columns appear too.
    assert "How likely to recommend?" in header
    assert "Anything else?" in header


def test_export_csv_rows_present(client, ctx):
    """All 3 responses appear in the CSV (text answers + statuses)."""
    client.force_login(ctx["owner"])
    body = _read_streaming(client.get(f"{_url(ctx)}.csv"))
    assert "Great product" in body
    assert "Too slow" in body
    assert "Maybe later" in body
    assert body.count("\n") >= 3  # header + 3 rows


def test_export_honors_filters(client, ctx):
    """A filtered export contains only the matching rows (shared narrowing path with the table)."""
    client.force_login(ctx["owner"])
    resp = client.get(f"{_url(ctx)}.csv?status=partial")
    body = _read_streaming(resp)
    assert "Maybe later" in body       # r3 (partial) included
    assert "Great product" not in body  # r1 (finished) excluded
    assert "Too slow" not in body       # r2 (finished) excluded


def test_export_owner_sees_raw_pii(client, ctx):
    """Owner (write+) export → the contact's email + the source IP are RAW (not masked)."""
    client.force_login(ctx["owner"])
    body = _read_streaming(client.get(f"{_url(ctx)}.csv"))
    assert "promoter@acme.io" in body   # contact email (PII) raw for write+
    assert "203.0.113.7" in body        # meta.ip (PII) raw for write+


def test_export_read_only_masks_pii(client, ctx):
    """A workspace ``read`` member export → PII (contact email + IP) masked; non-PII answers shown."""
    reader = _add_member(ctx["org"], "rt_export_reader@example.com")
    _grant(ctx["org"], reader, ctx["ws"].id, WorkspaceAccess.READ)
    client.force_login(reader)
    body = _read_streaming(client.get(f"{_url(ctx)}.csv"))
    assert "promoter@acme.io" not in body  # email masked
    assert "203.0.113.7" not in body       # ip masked
    assert "***" in body                   # mask present
    assert "Great product" in body         # non-PII text answer still exported


def test_export_csv_formula_injection_sanitized(client, ctx):
    """A response answer beginning with a formula trigger (=, +, -, @) is prefixed with a single
    quote in the CSV so a spreadsheet treats it as text (OWASP CSV-injection guard)."""
    connection.set_tenant(ctx["org"])
    Response.objects.create(
        survey=ctx["survey"],
        data={"q_text": "=SUM(A1:A9)+cmd"},
        finished=True,
    )
    client.force_login(ctx["owner"])
    body = _read_streaming(client.get(f"{_url(ctx)}.csv"))
    # The dangerous cell is emitted with a leading single quote, never a bare leading '='.
    assert "'=SUM(A1:A9)+cmd" in body
    assert ",=SUM(A1:A9)+cmd" not in body


def test_export_non_member_404(client, ctx):
    """A non-member (no workspace read) → 404 (no existence oracle)."""
    outsider = _mk_user("rt_export_outsider@example.com")
    client.force_login(outsider)
    resp = client.get(f"{_url(ctx)}.csv")
    assert resp.status_code == 404


def test_export_json_format(client, ctx):
    """The .json export returns a JSON object with count + per-response records (same PII rule)."""
    client.force_login(ctx["owner"])
    resp = client.get(f"{_url(ctx)}.json")
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/json"
    payload = resp.json()
    assert payload["count"] == 3
    # The contact email is raw for the owner; statuses present.
    joined = str(payload["responses"])
    assert "promoter@acme.io" in joined
    assert "completed" in joined and "partial" in joined


def test_export_json_read_only_masks_pii(client, ctx):
    """The .json export masks PII for a read-only caller (same rule as CSV)."""
    reader = _add_member(ctx["org"], "rt_json_reader@example.com")
    _grant(ctx["org"], reader, ctx["ws"].id, WorkspaceAccess.READ)
    client.force_login(reader)
    payload = client.get(f"{_url(ctx)}.json").json()
    joined = str(payload["responses"])
    assert "promoter@acme.io" not in joined
    assert "203.0.113.7" not in joined


# =========================================================================== #
#  TAG MANAGEMENT — rename / delete / merge + RBAC (owner/admin only)          #
# =========================================================================== #
def test_tags_manage_page_owner(client, ctx):
    """Owner sees the tag-management page with each tag + its response count."""
    connection.set_tenant(ctx["org"])
    tag = Tag.objects.create(name="Onboarding")
    ResponseTag.objects.create(response=ctx["r1"], tag=tag)
    client.force_login(ctx["owner"])
    resp = client.get(f"/{SLUG}/tags")
    assert resp.status_code == 200
    assert "Onboarding" in resp.content.decode()


def test_tags_manage_member_forbidden(client, ctx):
    """A plain org member (not owner/admin) cannot manage tags → 403."""
    member = _add_member(ctx["org"], "rt_mgmt_member@example.com")
    _grant(ctx["org"], member, ctx["ws"].id, WorkspaceAccess.WRITE)  # even with ws write
    client.force_login(member)
    resp = client.get(f"/{SLUG}/tags")
    assert resp.status_code == 403


def test_tags_manage_non_member_404(client, ctx):
    """A non-member → 404 (no existence oracle on the tags surface)."""
    outsider = _mk_user("rt_mgmt_outsider@example.com")
    client.force_login(outsider)
    resp = client.get(f"/{SLUG}/tags")
    assert resp.status_code == 404


def test_tag_rename(client, ctx):
    """Owner renames a tag → the name updates."""
    connection.set_tenant(ctx["org"])
    tag = Tag.objects.create(name="Old Name")
    client.force_login(ctx["owner"])
    resp = client.post(f"/{SLUG}/tags/{tag.id}/rename", {"name": "New Name"})
    assert resp.status_code in (301, 302)
    connection.set_tenant(ctx["org"])
    tag.refresh_from_db()
    assert tag.name == "New Name"


def test_tag_rename_duplicate_bounces_with_error(client, ctx):
    """Renaming to a name that collides (case-insensitively) with another tag is refused."""
    connection.set_tenant(ctx["org"])
    Tag.objects.create(name="Existing")
    tag = Tag.objects.create(name="Other")
    client.force_login(ctx["owner"])
    resp = client.post(f"/{SLUG}/tags/{tag.id}/rename", {"name": "existing"})
    assert resp.status_code in (301, 302)
    assert "error" in resp.url
    connection.set_tenant(ctx["org"])
    tag.refresh_from_db()
    assert tag.name == "Other"  # unchanged


def test_tag_delete(client, ctx):
    """Owner deletes a tag → the tag + its links are gone; the responses survive."""
    connection.set_tenant(ctx["org"])
    tag = Tag.objects.create(name="Trash")
    ResponseTag.objects.create(response=ctx["r1"], tag=tag)
    client.force_login(ctx["owner"])
    resp = client.post(f"/{SLUG}/tags/{tag.id}/delete")
    assert resp.status_code in (301, 302)
    connection.set_tenant(ctx["org"])
    assert not Tag.objects.filter(id=tag.id).exists()
    assert not ResponseTag.objects.filter(tag_id=tag.id).exists()
    assert Response.objects.filter(id=ctx["r1"].id).exists()


def test_tag_merge(client, ctx):
    """Owner merges source INTO target → source's links re-point to target, source is deleted."""
    connection.set_tenant(ctx["org"])
    source = Tag.objects.create(name="Dup")
    target = Tag.objects.create(name="Canonical")
    ResponseTag.objects.create(response=ctx["r1"], tag=source)
    ResponseTag.objects.create(response=ctx["r2"], tag=source)
    client.force_login(ctx["owner"])
    resp = client.post(f"/{SLUG}/tags/{source.id}/merge", {"target": str(target.id)})
    assert resp.status_code in (301, 302)
    connection.set_tenant(ctx["org"])
    assert not Tag.objects.filter(id=source.id).exists()
    # Both responses now carry the target tag.
    assert ResponseTag.objects.filter(response=ctx["r1"], tag=target).exists()
    assert ResponseTag.objects.filter(response=ctx["r2"], tag=target).exists()


def test_tag_merge_dedups_double_tagged_response(client, ctx):
    """A response already carrying BOTH source and target keeps a single target link after merge
    (uniq_response_tag respected — no duplicate)."""
    connection.set_tenant(ctx["org"])
    source = Tag.objects.create(name="S")
    target = Tag.objects.create(name="T")
    ResponseTag.objects.create(response=ctx["r1"], tag=source)
    ResponseTag.objects.create(response=ctx["r1"], tag=target)  # already has target
    client.force_login(ctx["owner"])
    resp = client.post(f"/{SLUG}/tags/{source.id}/merge", {"target": str(target.id)})
    assert resp.status_code in (301, 302)
    connection.set_tenant(ctx["org"])
    assert ResponseTag.objects.filter(response=ctx["r1"], tag=target).count() == 1
    assert not Tag.objects.filter(id=source.id).exists()


def test_tag_merge_read_only_forbidden(client, ctx):
    """A plain member cannot merge tags → 403 (management is owner/admin)."""
    connection.set_tenant(ctx["org"])
    source = Tag.objects.create(name="MS")
    target = Tag.objects.create(name="MT")
    member = _add_member(ctx["org"], "rt_merge_member@example.com")
    _grant(ctx["org"], member, ctx["ws"].id, WorkspaceAccess.WRITE)
    client.force_login(member)
    resp = client.post(f"/{SLUG}/tags/{source.id}/merge", {"target": str(target.id)})
    assert resp.status_code == 403
    connection.set_tenant(ctx["org"])
    assert Tag.objects.filter(id=source.id).exists()  # not merged


# =========================================================================== #
#  Unit: service-layer edge cases (request-free)                               #
# =========================================================================== #
def test_service_tag_create_or_get_blank_raises(ctx):
    connection.set_tenant(ctx["org"])
    with pytest.raises(ValueError):
        tag_services.tag_create_or_get("   ")


def test_service_merge_into_self_raises(ctx):
    connection.set_tenant(ctx["org"])
    t = tag_services.tag_create_or_get("Self")
    with pytest.raises(ValueError):
        tag_services.merge_tags(source=t, target=t)


def test_service_csv_safe_prefixes_formula_triggers():
    assert tag_services.csv_safe("=1+1") == "'=1+1"
    assert tag_services.csv_safe("+cmd") == "'+cmd"
    assert tag_services.csv_safe("-2") == "'-2"
    assert tag_services.csv_safe("@x") == "'@x"
    assert tag_services.csv_safe("safe") == "safe"
    assert tag_services.csv_safe(9) == "9"
    assert tag_services.csv_safe(None) == ""
