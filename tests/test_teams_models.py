"""
Focused model tests for teams (Team / TeamMembership / WorkspaceTeam, models.md §2.3–§2.5).

The most load-bearing tenant app for the cross-schema rule (ADR-002): TeamMembership.user_id
is a plain UUID column (NO FK to the public User), resolved via hydrate_users(). These tests
exercise that pattern end-to-end (write a user_id from a public User, read it back via the
manager's .hydrate_users() in ONE public-schema query) plus the key constraints.
"""
import uuid

import pytest
from django.db import IntegrityError, connection

from apps.common.hydration import hydrate_users
from apps.teams.enums import TeamRole, WorkspaceAccess
from apps.teams.models import Team, TeamMembership, WorkspaceTeam
from apps.workspaces.models import Workspace


@pytest.mark.django_db
def test_team_name_case_insensitive_unique(demo_tenant):
    connection.set_tenant(demo_tenant)
    Team.objects.create(name="Engineering")
    with pytest.raises(IntegrityError):
        Team.objects.create(name="engineering")  # uniq_team_name is Lower("name")


@pytest.mark.django_db
def test_team_membership_user_id_is_plain_uuid_no_fk(demo_tenant):
    """The cross-schema pattern: user_id stores a public User PK as a plain UUID column.

    There must be NO ForeignKey to accounts.User (would be a cross-schema FK, forbidden).
    """
    connection.set_tenant(demo_tenant)
    field = TeamMembership._meta.get_field("user_id")
    # It's a UUIDField, not a relation.
    assert field.get_internal_type() == "UUIDField"
    assert not field.is_relation


@pytest.mark.django_db
def test_team_membership_hydrate_users_one_query(demo_tenant, django_assert_num_queries):
    """Write user_id from a real public User; read it back via .hydrate_users() in ONE query
    (the N+1-killing contract, models.md PART A / ADR-002)."""
    from apps.accounts.models import User

    # Create the user in the PUBLIC schema.
    connection.set_schema_to_public()
    u1 = User.objects.create_user(email="eng1@example.com", password="x", name="Eng One")
    u2 = User.objects.create_user(email="eng2@example.com", password="x", name="Eng Two")

    # Write tenant rows carrying the public User PKs as user_id (no FK).
    connection.set_tenant(demo_tenant)
    team = Team.objects.create(name="Squad")
    TeamMembership.objects.create(team=team, user_id=u1.id, team_role=TeamRole.ADMIN)
    TeamMembership.objects.create(team=team, user_id=u2.id, team_role=TeamRole.MEMBER)

    # The frozen guarantee (models.md PART A): hydration adds at most ONE public-schema query
    # regardless of row count (the N+1 the helper exists to kill). Materialize the rows first
    # (their fetch is a separate tenant-schema query), THEN assert the resolution is 1 query.
    members = list(TeamMembership.objects.filter(team=team))
    with django_assert_num_queries(1):
        hydrate_users(members, fields=("user_id",))

    by_id = {m.user_id: m.user_obj for m in members}
    assert by_id[u1.id] == u1
    assert by_id[u2.id] == u2

    # Manager ergonomic from PART A also works (chained on the default queryset): returns a
    # list with .user_obj attached. (Not wrapped in the query-count assert because the chained
    # call also performs the row-fetch query; the 1-query *resolution* guarantee is asserted
    # above on the materialized list.)
    members2 = TeamMembership.objects.filter(team=team).hydrate_users("user_id")
    assert {m.user_id: m.user_obj for m in members2} == by_id


@pytest.mark.django_db
def test_team_membership_unique_per_user(demo_tenant):
    connection.set_tenant(demo_tenant)
    team = Team.objects.create(name="Squad2")
    uid = uuid.uuid4()
    TeamMembership.objects.create(team=team, user_id=uid)
    with pytest.raises(IntegrityError):
        TeamMembership.objects.create(team=team, user_id=uid)  # uniq_team_membership


@pytest.mark.django_db
def test_workspace_team_unique_and_access_choices(demo_tenant):
    connection.set_tenant(demo_tenant)
    team = Team.objects.create(name="Squad3")
    ws = Workspace.objects.create(name="WS", slug="ws-teams")
    WorkspaceTeam.objects.create(team=team, workspace=ws, access=WorkspaceAccess.WRITE)
    # One access row per (team, workspace) — re-granting violates uniq_workspace_team.
    with pytest.raises(IntegrityError):
        WorkspaceTeam.objects.create(team=team, workspace=ws, access=WorkspaceAccess.READ)


def test_workspace_access_ordering_values():
    """The contract fixes the ordered string values read < write < manage (models.md §2.5).
    Ordering itself is enforced in apps/common/permissions.py, not the DB — here we just lock
    the string values."""
    assert WorkspaceAccess.READ == "read"
    assert WorkspaceAccess.WRITE == "write"
    assert WorkspaceAccess.MANAGE == "manage"
