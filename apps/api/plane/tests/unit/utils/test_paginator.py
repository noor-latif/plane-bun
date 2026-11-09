# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from django.db.models import Q
from django.test import RequestFactory
from rest_framework.exceptions import ParseError
from rest_framework.request import Request

from plane.app.views.issue.base import IssueViewSet
from plane.db.models import Issue, IssueLabel, Label, Project, State
from plane.utils.grouper import issue_on_results, issue_queryset_grouper
from plane.utils.paginator import (
    BasePaginator,
    Cursor,
    CursorResult,
    GroupedOffsetPaginator,
    SubGroupedOffsetPaginator,
)


class _StubGroupedPaginator:
    """Stand-in for GroupedOffsetPaginator/SubGroupedOffsetPaginator that
    records its constructor kwargs without touching the DB/ORM. The security
    property under test lives entirely in BasePaginator.paginate() — by the
    time a real paginator class would run a query, the field name has
    already been validated (or rejected)."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def get_result(self, limit, cursor):
        return CursorResult(
            results=[],
            next=Cursor(limit, 1, False, False),
            prev=Cursor(limit, -1, True, False),
            hits=0,
            max_hits=0,
        )

    def process_results(self, results):
        return results


def _make_request(**params):
    django_request = RequestFactory().get("/fake-url/", data=params)
    return Request(django_request)


@pytest.mark.unit
class TestPaginateGroupByValidation:
    """Regression tests for GHSA-wwgj-929g-42cm.

    BasePaginator.paginate() is the single chokepoint all group_by/sub_group_by
    call sites (public Spaces endpoint + 5 authenticated issue/cycle/module/
    workspace endpoints) funnel through. It must reject any field name outside
    ISSUE_GROUP_BY_ALLOWLIST with a 400 (ParseError), before the value ever
    reaches a real paginator's F()/.values()/.order_by()/Window partition_by.
    """

    def test_invalid_group_by_raises_parse_error(self):
        request = _make_request(group_by="created_by__password")
        with pytest.raises(ParseError):
            BasePaginator().paginate(
                request=request,
                queryset=None,
                paginator_cls=_StubGroupedPaginator,
                group_by_field_name="created_by__password",
                group_by_fields=[],
                count_filter=None,
            )

    def test_invalid_sub_group_by_raises_parse_error(self):
        # A valid group_by paired with an invalid sub_group_by must still be
        # rejected — the PoC in the advisory used exactly this combination
        # (group_by=state_id&sub_group_by=created_by__password).
        request = _make_request(group_by="priority", sub_group_by="workspace__secret_key")
        with pytest.raises(ParseError):
            BasePaginator().paginate(
                request=request,
                queryset=None,
                paginator_cls=_StubGroupedPaginator,
                group_by_field_name="priority",
                group_by_fields=[],
                sub_group_by_field_name="workspace__secret_key",
                sub_group_by_fields=[],
                count_filter=None,
            )

    def test_unrecognised_field_never_reaches_paginator_constructor(self):
        # Belt-and-braces: assert the stub paginator is never even
        # instantiated for a rejected field name.
        request = _make_request(group_by="not_a_field")

        class _ExplodingPaginator:
            def __init__(self, **kwargs):
                raise AssertionError("paginator_cls must not be constructed for an invalid group_by field")

        with pytest.raises(ParseError):
            BasePaginator().paginate(
                request=request,
                queryset=None,
                paginator_cls=_ExplodingPaginator,
                group_by_field_name="not_a_field",
                group_by_fields=[],
                count_filter=None,
            )

    def test_valid_group_by_and_sub_group_by_pass_through(self):
        request = _make_request(group_by="priority", sub_group_by="state_id")
        response = BasePaginator().paginate(
            request=request,
            queryset=None,
            paginator_cls=_StubGroupedPaginator,
            group_by_field_name="priority",
            group_by_fields=[],
            sub_group_by_field_name="state_id",
            sub_group_by_fields=[],
            count_filter=None,
        )
        assert response.data["grouped_by"] == "priority"
        assert response.data["sub_grouped_by"] == "state_id"

    def test_no_group_by_is_unaffected(self):
        # Plain (non-grouped) pagination must not be touched by this fix.
        request = _make_request()
        response = BasePaginator().paginate(
            request=request,
            queryset=None,
            paginator_cls=_StubGroupedPaginator,
        )
        assert response.data["grouped_by"] is None


class _ExplodingPaginator:
    """Fails if constructed: the cursor check runs before any paginator is built."""

    def __init__(self, **kwargs):
        raise AssertionError("paginator_cls must not be constructed for an invalid cursor")


@pytest.mark.unit
class TestCursorBounds:
    """paginate() accepts a cursor value in [0, max_per_page] and a non-negative offset."""

    @pytest.mark.parametrize("cursor", ["-1:0:0", "1001:0:0", "20:-1:0"])
    def test_out_of_bounds_cursor_rejected_before_paginator(self, cursor):
        request = _make_request(cursor=cursor)
        with pytest.raises(ParseError):
            BasePaginator().paginate(
                request=request,
                queryset=None,
                paginator_cls=_ExplodingPaginator,
                default_per_page=20,
                max_per_page=1000,
            )

    def test_valid_cursor_passes_the_guard(self):
        request = _make_request(cursor="20:0:0")
        response = BasePaginator().paginate(
            request=request,
            queryset=None,
            paginator_cls=_StubGroupedPaginator,
            default_per_page=20,
            max_per_page=1000,
        )
        assert response.data["results"] == []

    def test_cursor_value_at_max_is_allowed(self):
        request = _make_request(cursor="1000:0:0")
        response = BasePaginator().paginate(
            request=request,
            queryset=None,
            paginator_cls=_StubGroupedPaginator,
            default_per_page=20,
            max_per_page=1000,
        )
        assert response.data["results"] == []


@pytest.mark.unit
class TestGetPerPageNonPositiveRejected:
    """per_page must be a positive integer."""

    @pytest.mark.parametrize("per_page", [-1, -50, -1000, 0])
    def test_non_positive_per_page_rejected_by_get_per_page(self, per_page):
        request = _make_request(per_page=str(per_page))
        with pytest.raises(ParseError):
            BasePaginator().get_per_page(request, default_per_page=20, max_per_page=1000)

    @pytest.mark.parametrize("per_page", ["-1", "0"])
    def test_non_positive_per_page_rejected_before_paginator_runs(self, per_page):
        request = _make_request(per_page=per_page)
        with pytest.raises(ParseError):
            BasePaginator().paginate(
                request=request,
                queryset=None,
                paginator_cls=_ExplodingPaginator,
                default_per_page=20,
                max_per_page=1000,
            )


@pytest.mark.unit
@pytest.mark.django_db
class TestGroupedPageSize:
    """Grouped paginators size each group's page by per_page, whatever the cursor value."""

    @pytest.fixture
    def issues(self, workspace):
        project = Project.objects.create(name="Paginate", identifier="PAGE", workspace=workspace)
        state = State.objects.create(name="Todo", project=project, workspace=workspace)
        for i in range(3):
            for priority in ("high", "low"):
                Issue.objects.create(
                    name=f"{priority}-{i}", priority=priority, state=state, project=project, workspace=workspace
                )
        return Issue.issue_objects.filter(project=project)

    @pytest.mark.parametrize(
        "paginator_cls,extra",
        [
            (GroupedOffsetPaginator, {}),
            (SubGroupedOffsetPaginator, {"sub_group_by_field_name": "state_id", "sub_group_by_fields": []}),
        ],
    )
    def test_cursor_value_does_not_change_page_size(self, issues, paginator_cls, extra):
        paginator = paginator_cls(
            queryset=issues,
            order_by="-created_at",
            group_by_field_name="priority",
            group_by_fields=["high", "low"],
            count_filter=None,
            **extra,
        )
        seen = []
        for page in range(3):
            result = paginator.get_result(limit=1, cursor=Cursor(1000, page, False))
            rows = list(result.results.values("id", "priority"))
            assert sorted(r["priority"] for r in rows) == ["high", "low"]
            assert str(result.next) == f"1:{page + 1}:0"
            seen += [r["id"] for r in rows]
        assert len(set(seen)) == 6
        assert not result.next.has_results


@pytest.mark.unit
@pytest.mark.django_db
class TestGroupedCountUsesDistinctIssues:
    """Grouped pagination must count DISTINCT issues, not m2m membership rows.

    Driven through BasePaginator().paginate() — the seam every grouped endpoint
    uses — against the real database, so the counted querysets are real. Two
    issues, one carrying two labels, give three membership rows; the pre-patch
    grouped count is therefore 3 while the correct distinct-issue count is 2.
    """

    @pytest.fixture
    def data(self, workspace):
        project = Project.objects.create(name="Counts", identifier="CNTS", workspace=workspace)
        state = State.objects.create(name="Todo", project=project, workspace=workspace)
        label_a = Label.objects.create(name="A", project=project, workspace=workspace)
        label_b = Label.objects.create(name="B", project=project, workspace=workspace)
        issue_one = Issue.objects.create(name="one", state=state, project=project, workspace=workspace)
        issue_two = Issue.objects.create(name="two", state=state, project=project, workspace=workspace)
        IssueLabel.objects.create(issue=issue_one, label=label_a, project=project, workspace=workspace)
        IssueLabel.objects.create(issue=issue_one, label=label_b, project=project, workspace=workspace)
        IssueLabel.objects.create(issue=issue_two, label=label_a, project=project, workspace=workspace)
        return {"project": project, "state": state, "labels": [label_a, label_b],
                "issues": [issue_one, issue_two]}

    @staticmethod
    def _annotated(queryset):
        # The real view annotates before the paginator (cycle/module/label
        # subqueries); issue_on_results requires those annotations.
        return IssueViewSet().apply_annotations(queryset)

    def _paginate(self, queryset, total_count_queryset, group_by_fields,
                  sub_group_by=None, sub_group_by_fields=None):
        request = _make_request(per_page="50")
        return BasePaginator().paginate(
            request=request,
            queryset=queryset,
            paginator_cls=GroupedOffsetPaginator if sub_group_by is None else SubGroupedOffsetPaginator,
            on_results=lambda issues: issue_on_results(
                issues=issues, group_by="labels__id", sub_group_by=sub_group_by
            ),
            group_by_field_name="labels__id",
            group_by_fields=group_by_fields,
            sub_group_by_field_name=sub_group_by,
            sub_group_by_fields=sub_group_by_fields,
            count_filter=Q(),
            total_count_queryset=total_count_queryset,
            order_by="-created_at",
            default_per_page=50,
            max_per_page=1000,
        )

    def test_grouped_total_count_is_distinct_issue_count(self, data):
        base = Issue.issue_objects.filter(project=data["project"]).distinct()
        grouped = issue_queryset_grouper(queryset=self._annotated(base), group_by="labels__id", sub_group_by=None)
        group_fields = [str(label.id) for label in data["labels"]] + ["None"]

        # The distinct-issue count. The pre-patch paginator counted the windowed
        # joined rows (3 memberships for 2 issues); see the captured pre-patch run.
        assert base.count() == 2

        response = self._paginate(grouped, base, group_fields)
        assert response.data["total_count"] == 2
        assert response.data["total_results"] == 2
        # The m2m grouper lists an issue under each of its labels, so count
        # distinct issue ids rather than group memberships.
        visible_ids = {
            issue["id"] for group in response.data["results"].values() for issue in group["results"]
        }
        assert len(visible_ids) == 2

    def test_sub_grouped_total_count_is_distinct_issue_count(self, data):
        base = Issue.issue_objects.filter(project=data["project"]).distinct()
        grouped = issue_queryset_grouper(
            queryset=self._annotated(base), group_by="labels__id", sub_group_by="priority"
        )
        response = self._paginate(
            grouped, base, [str(label.id) for label in data["labels"]] + ["None"],
            sub_group_by="priority", sub_group_by_fields=["low", "medium", "high", "urgent", "none"],
        )
        assert response.data["total_count"] == 2
        assert response.data["total_results"] == 2

    def test_none_fallback_keeps_annotated_count_for_public_surfaces(self, data):
        base = Issue.issue_objects.filter(project=data["project"]).distinct()
        grouped = issue_queryset_grouper(queryset=self._annotated(base), group_by="labels__id", sub_group_by=None)
        group_fields = [str(label.id) for label in data["labels"]] + ["None"]
        response = self._paginate(grouped, None, group_fields)
        # Public space endpoints pass no counting queryset; their count stays the
        # windowed annotated count (3 membership rows for 2 issues), which is the
        # pre-patch behaviour. The un-windowed distinct count is 2.
        assert grouped.count() == 2
        assert response.data["total_count"] == 3
        assert response.data["total_results"] == 3

    def test_zero_label_issue_survives_only_in_the_counting_queryset(self, data):
        """V1 observation: grouper filters are inner joins, the counting queryset is not."""
        unlabeled = Issue.objects.create(
            name="unlabeled", state=data["state"], project=data["project"], workspace=data["project"].workspace
        )
        base = Issue.issue_objects.filter(project=data["project"]).distinct()
        grouped = issue_queryset_grouper(queryset=self._annotated(base), group_by="labels__id", sub_group_by=None)
        assert base.count() == 3  # includes the unlabeled issue
        response = self._paginate(grouped, base, [str(label.id) for label in data["labels"]] + ["None"])
        visible_ids = {
            issue["id"]
            for group in response.data["results"].values()
            for issue in group["results"]
        }
        # V1 observed: the grouper's label filter is a LEFT OUTER join, so the
        # unlabeled issue survives under the "None" group and the distinct count
        # agrees with the visible rows (3 == 3), not 3 vs 2.
        assert "None" in response.data["results"]
        assert unlabeled.id in visible_ids
        assert response.data["total_count"] == 3
        assert len(visible_ids) == 3

    def test_non_distinct_counting_queryset_keeps_membership_rows(self, data):
        """V3 observation: surfaces without .distinct() stay unchanged-wrong."""
        base = Issue.issue_objects.filter(project=data["project"]).distinct()
        grouped = issue_queryset_grouper(queryset=self._annotated(base), group_by="labels__id", sub_group_by=None)
        labels = data["labels"]
        membership = Issue.issue_objects.filter(project=data["project"], labels__id__in=[l.id for l in labels])
        assert membership.count() == 3
        assert membership.distinct().count() == 2
        response = self._paginate(grouped, membership, [str(label.id) for label in labels] + ["None"])
        assert response.data["total_count"] == 3
