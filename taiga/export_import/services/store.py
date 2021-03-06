# Copyright (C) 2014-2016 Andrey Antukh <niwi@niwi.nz>
# Copyright (C) 2014-2016 Jesús Espino <jespinog@gmail.com>
# Copyright (C) 2014-2016 David Barragán <bameda@dbarragan.com>
# Copyright (C) 2014-2016 Alejandro Alonso <alejandro.alonso@kaleidos.net>
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

# This makes all code that import services works and
# is not the baddest practice ;)

import os
import uuid

from unidecode import unidecode

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist
from django.template.defaultfilters import slugify
from django.utils.translation import ugettext as _

from taiga.projects.history.services import make_key_from_model_object, take_snapshot
from taiga.projects.models import Membership
from taiga.projects.references import sequences as seq
from taiga.projects.references import models as refs
from taiga.projects.userstories.models import RolePoints
from taiga.projects.services import find_invited_user
from taiga.timeline.service import build_project_namespace
from taiga.users import services as users_service

from .. import exceptions as err
from .. import serializers


########################################################################
## Manage errors
########################################################################

_errors_log = {}


def get_errors(clear=True):
    _errors = _errors_log.copy()
    if clear:
        _errors_log.clear()
    return _errors


def add_errors(section, errors):
    if section in _errors_log:
        _errors_log[section].append(errors)
    else:
        _errors_log[section] = [errors]


def reset_errors():
    _errors_log.clear()


########################################################################
## Store functions
########################################################################


## PROJECT

def store_project(data):
    project_data = {}
    for key, value in data.items():
        excluded_fields = [
            "default_points", "default_us_status", "default_task_status",
            "default_priority", "default_severity", "default_issue_status",
            "default_issue_type", "memberships", "points", "us_statuses",
            "task_statuses", "issue_statuses", "priorities", "severities",
            "issue_types", "userstorycustomattributes", "taskcustomattributes",
            "issuecustomattributes", "roles", "milestones", "wiki_pages",
            "wiki_links", "notify_policies", "user_stories", "issues", "tasks",
            "is_featured"
        ]
        if key not in excluded_fields:
            project_data[key] = value

    serialized = serializers.ProjectExportSerializer(data=project_data)
    if serialized.is_valid():
        serialized.object._importing = True
        serialized.object.save()
        serialized.save_watchers()
        return serialized
    add_errors("project", serialized.errors)
    return None


## MISC

def _use_id_instead_name_as_key_in_custom_attributes_values(custom_attributes, values):
    ret = {}
    for attr in custom_attributes:
        value = values.get(attr["name"], None)
        if value is not None:
            ret[str(attr["id"])] = value

    return ret


def _store_custom_attributes_values(obj, data_values, obj_field, serializer_class):
    data = {
        obj_field: obj.id,
        "attributes_values": data_values,
    }

    try:
        custom_attributes_values = obj.custom_attributes_values
        serializer = serializer_class(custom_attributes_values, data=data)
    except ObjectDoesNotExist:
        serializer = serializer_class(data=data)

    if serializer.is_valid():
        serializer.save()
        return serializer

    add_errors("custom_attributes_values", serializer.errors)
    return None


def _store_attachment(project, obj, attachment):
    serialized = serializers.AttachmentExportSerializer(data=attachment)
    if serialized.is_valid():
        serialized.object.content_type = ContentType.objects.get_for_model(obj.__class__)
        serialized.object.object_id = obj.id
        serialized.object.project = project
        if serialized.object.owner is None:
            serialized.object.owner = serialized.object.project.owner
        serialized.object._importing = True
        serialized.object.size = serialized.object.attached_file.size
        serialized.object.name = os.path.basename(serialized.object.attached_file.name)
        serialized.save()
        return serialized
    add_errors("attachments", serialized.errors)
    return serialized


def _store_history(project, obj, history):
    serialized = serializers.HistoryExportSerializer(data=history, context={"project": project})
    if serialized.is_valid():
        serialized.object.key = make_key_from_model_object(obj)
        if serialized.object.diff is None:
            serialized.object.diff = []
        serialized.object._importing = True
        serialized.save()
        return serialized
    add_errors("history", serialized.errors)
    return serialized


## ROLES

def _store_role(project, role):
    serialized = serializers.RoleExportSerializer(data=role)
    if serialized.is_valid():
        serialized.object.project = project
        serialized.object._importing = True
        serialized.save()
        return serialized
    add_errors("roles", serialized.errors)
    return None


def store_roles(project, data):
    results = []
    for role in data.get("roles", []):
        serialized = _store_role(project, role)
        if serialized:
            results.append(serialized)

    return results


## MEMGERSHIPS

def _store_membership(project, membership):
    serialized = serializers.MembershipExportSerializer(data=membership, context={"project": project})
    if serialized.is_valid():
        serialized.object.project = project
        serialized.object._importing = True
        serialized.object.token = str(uuid.uuid1())
        serialized.object.user = find_invited_user(serialized.object.email,
                                                   default=serialized.object.user)
        serialized.save()
        return serialized

    add_errors("memberships", serialized.errors)
    return None


def store_memberships(project, data):
    results = []
    for membership in data.get("memberships", []):
        results.append(_store_membership(project, membership))
    return results


## PROJECT ATTRIBUTES

def _store_project_attribute_value(project, data, field, serializer):
    serialized = serializer(data=data)
    if serialized.is_valid():
        serialized.object.project = project
        serialized.object._importing = True
        serialized.save()
        return serialized.object
    add_errors(field, serialized.errors)
    return None


def store_project_attributes_values(project, data, field, serializer):
    result = []
    for choice_data in data.get(field, []):
        result.append(_store_project_attribute_value(project, choice_data, field, serializer))
    return result


## DEFAULT PROJECT ATTRIBUTES VALUES

def store_default_project_attributes_values(project, data):
    def helper(project, field, related, data):
        if field in data:
            value = related.all().get(name=data[field])
        else:
            value = related.all().first()
        setattr(project, field, value)

    helper(project, "default_points", project.points, data)
    helper(project, "default_issue_type", project.issue_types, data)
    helper(project, "default_issue_status", project.issue_statuses, data)
    helper(project, "default_us_status", project.us_statuses, data)
    helper(project, "default_task_status", project.task_statuses, data)
    helper(project, "default_priority", project.priorities, data)
    helper(project, "default_severity", project.severities, data)
    project._importing = True
    project.save()


## CUSTOM ATTRIBUTES

def _store_custom_attribute(project, data, field, serializer):
    serialized = serializer(data=data)
    if serialized.is_valid():
        serialized.object.project = project
        serialized.object._importing = True
        serialized.save()
        return serialized.object
    add_errors(field, serialized.errors)
    return None


def store_custom_attributes(project, data, field, serializer):
    result = []
    for custom_attribute_data in data.get(field, []):
        result.append(_store_custom_attribute(project, custom_attribute_data, field, serializer))
    return result


## MILESTONE

def store_milestone(project, milestone):
    serialized = serializers.MilestoneExportSerializer(data=milestone, project=project)
    if serialized.is_valid():
        serialized.object.project = project
        serialized.object._importing = True
        serialized.save()
        serialized.save_watchers()

        for task_without_us in milestone.get("tasks_without_us", []):
            task_without_us["user_story"] = None
            store_task(project, task_without_us)
        return serialized

    add_errors("milestones", serialized.errors)
    return None


def store_milestones(project, data):
    results = []
    for milestone_data in data.get("milestones", []):
        milestone = store_milestone(project, milestone_data)
        results.append(milestone)
    return results


## USER STORIES

def _store_role_point(project, us, role_point):
    serialized = serializers.RolePointsExportSerializer(data=role_point, context={"project": project})
    if serialized.is_valid():
        try:
            existing_role_point = us.role_points.get(role=serialized.object.role)
            existing_role_point.points = serialized.object.points
            existing_role_point.save()
            return existing_role_point

        except RolePoints.DoesNotExist:
            serialized.object.user_story = us
            serialized.save()
            return serialized.object

    add_errors("role_points", serialized.errors)
    return None

def store_user_story(project, data):
    if "status" not in data and project.default_us_status:
        data["status"] = project.default_us_status.name

    us_data = {key: value for key, value in data.items() if key not in
                                                            ["role_points", "custom_attributes_values"]}
    serialized = serializers.UserStoryExportSerializer(data=us_data, context={"project": project})

    if serialized.is_valid():
        serialized.object.project = project
        if serialized.object.owner is None:
            serialized.object.owner = serialized.object.project.owner
        serialized.object._importing = True
        serialized.object._not_notify = True

        serialized.save()
        serialized.save_watchers()

        if serialized.object.ref:
            sequence_name = refs.make_sequence_name(project)
            if not seq.exists(sequence_name):
                seq.create(sequence_name)
            seq.set_max(sequence_name, serialized.object.ref)
        else:
            serialized.object.ref, _ = refs.make_reference(serialized.object, project)
            serialized.object.save()

        for us_attachment in data.get("attachments", []):
            _store_attachment(project, serialized.object, us_attachment)

        for role_point in data.get("role_points", []):
            _store_role_point(project, serialized.object, role_point)

        history_entries = data.get("history", [])
        for history in history_entries:
            _store_history(project, serialized.object, history)

        if not history_entries:
            take_snapshot(serialized.object, user=serialized.object.owner)

        custom_attributes_values = data.get("custom_attributes_values", None)
        if custom_attributes_values:
            custom_attributes = serialized.object.project.userstorycustomattributes.all().values('id', 'name')
            custom_attributes_values = _use_id_instead_name_as_key_in_custom_attributes_values(
                                                    custom_attributes, custom_attributes_values)
            _store_custom_attributes_values(serialized.object, custom_attributes_values,
                                      "user_story", serializers.UserStoryCustomAttributesValuesExportSerializer)

        return serialized

    add_errors("user_stories", serialized.errors)
    return None


def store_user_stories(project, data):
    results = []
    for userstory in data.get("user_stories", []):
        us = store_user_story(project, userstory)
        results.append(us)
    return results


## TASKS

def store_task(project, data):
    if "status" not in data and project.default_task_status:
        data["status"] = project.default_task_status.name

    serialized = serializers.TaskExportSerializer(data=data, context={"project": project})
    if serialized.is_valid():
        serialized.object.project = project
        if serialized.object.owner is None:
            serialized.object.owner = serialized.object.project.owner
        serialized.object._importing = True
        serialized.object._not_notify = True

        serialized.save()
        serialized.save_watchers()

        if serialized.object.ref:
            sequence_name = refs.make_sequence_name(project)
            if not seq.exists(sequence_name):
                seq.create(sequence_name)
            seq.set_max(sequence_name, serialized.object.ref)
        else:
            serialized.object.ref, _ = refs.make_reference(serialized.object, project)
            serialized.object.save()

        for task_attachment in data.get("attachments", []):
            _store_attachment(project, serialized.object, task_attachment)

        history_entries = data.get("history", [])
        for history in history_entries:
            _store_history(project, serialized.object, history)

        if not history_entries:
            take_snapshot(serialized.object, user=serialized.object.owner)

        custom_attributes_values = data.get("custom_attributes_values", None)
        if custom_attributes_values:
            custom_attributes = serialized.object.project.taskcustomattributes.all().values('id', 'name')
            custom_attributes_values = _use_id_instead_name_as_key_in_custom_attributes_values(
                                                    custom_attributes, custom_attributes_values)
            _store_custom_attributes_values(serialized.object, custom_attributes_values,
                                           "task", serializers.TaskCustomAttributesValuesExportSerializer)

        return serialized

    add_errors("tasks", serialized.errors)
    return None


def store_tasks(project, data):
    results = []
    for task in data.get("tasks", []):
        task = store_task(project, task)
        results.append(task)
    return results


## ISSUES

def store_issue(project, data):
    serialized = serializers.IssueExportSerializer(data=data, context={"project": project})

    if "type" not in data and project.default_issue_type:
        data["type"] = project.default_issue_type.name

    if "status" not in data and project.default_issue_status:
        data["status"] = project.default_issue_status.name

    if "priority" not in data and project.default_priority:
        data["priority"] = project.default_priority.name

    if "severity" not in data and project.default_severity:
        data["severity"] = project.default_severity.name

    if serialized.is_valid():
        serialized.object.project = project
        if serialized.object.owner is None:
            serialized.object.owner = serialized.object.project.owner
        serialized.object._importing = True
        serialized.object._not_notify = True

        serialized.save()
        serialized.save_watchers()

        if serialized.object.ref:
            sequence_name = refs.make_sequence_name(project)
            if not seq.exists(sequence_name):
                seq.create(sequence_name)
            seq.set_max(sequence_name, serialized.object.ref)
        else:
            serialized.object.ref, _ = refs.make_reference(serialized.object, project)
            serialized.object.save()

        for attachment in data.get("attachments", []):
            _store_attachment(project, serialized.object, attachment)

        history_entries = data.get("history", [])
        for history in history_entries:
            _store_history(project, serialized.object, history)

        if not history_entries:
            take_snapshot(serialized.object, user=serialized.object.owner)

        custom_attributes_values = data.get("custom_attributes_values", None)
        if custom_attributes_values:
            custom_attributes = serialized.object.project.issuecustomattributes.all().values('id', 'name')
            custom_attributes_values = _use_id_instead_name_as_key_in_custom_attributes_values(
                                                    custom_attributes, custom_attributes_values)
            _store_custom_attributes_values(serialized.object, custom_attributes_values,
                                           "issue", serializers.IssueCustomAttributesValuesExportSerializer)

        return serialized

    add_errors("issues", serialized.errors)
    return None


def store_issues(project, data):
    issues = []
    for issue in data.get("issues", []):
        issues.append(store_issue(project, issue))
    return issues


## WIKI PAGES

def store_wiki_page(project, wiki_page):
    wiki_page["slug"] = slugify(unidecode(wiki_page.get("slug", "")))
    serialized = serializers.WikiPageExportSerializer(data=wiki_page)
    if serialized.is_valid():
        serialized.object.project = project
        if serialized.object.owner is None:
            serialized.object.owner = serialized.object.project.owner
        serialized.object._importing = True
        serialized.object._not_notify = True
        serialized.save()
        serialized.save_watchers()

        for attachment in wiki_page.get("attachments", []):
            _store_attachment(project, serialized.object, attachment)

        history_entries = wiki_page.get("history", [])
        for history in history_entries:
            _store_history(project, serialized.object, history)

        if not history_entries:
            take_snapshot(serialized.object, user=serialized.object.owner)

        return serialized

    add_errors("wiki_pages", serialized.errors)
    return None


def store_wiki_pages(project, data):
    results = []
    for wiki_page in data.get("wiki_pages", []):
        results.append(store_wiki_page(project, wiki_page))
    return results


## WIKI LINKS

def store_wiki_link(project, wiki_link):
    serialized = serializers.WikiLinkExportSerializer(data=wiki_link)
    if serialized.is_valid():
        serialized.object.project = project
        serialized.object._importing = True
        serialized.save()
        return serialized

    add_errors("wiki_links", serialized.errors)
    return None


def store_wiki_links(project, data):
    results = []
    for wiki_link in data.get("wiki_links", []):
        results.append(store_wiki_link(project, wiki_link))
    return results


## TAGS COLORS

def store_tags_colors(project, data):
    project.tags_colors = data.get("tags_colors", [])
    project.save()
    return None


## TIMELINE

def _store_timeline_entry(project, timeline):
    serialized = serializers.TimelineExportSerializer(data=timeline, context={"project": project})
    if serialized.is_valid():
        serialized.object.project = project
        serialized.object.namespace = build_project_namespace(project)
        serialized.object.object_id = project.id
        serialized.object._importing = True
        serialized.save()
        return serialized
    add_errors("timeline", serialized.errors)
    return serialized


def store_timeline_entries(project, data):
    results = []
    for timeline in data.get("timeline", []):
        tl = _store_timeline_entry(project, timeline)
        results.append(tl)
    return results


#############################################
## Store project dict
#############################################


def _validate_if_owner_have_enought_space_to_this_project(owner, data):
    # Validate if the owner can have this project
    data["owner"] = owner.email

    is_private = data.get("is_private", False)
    total_memberships = len([m for m in data.get("memberships", [])
                                        if m.get("email", None) != data["owner"]])
    total_memberships = total_memberships + 1 # 1 is the owner
    (enough_slots, error_message) = users_service.has_available_slot_for_import_new_project(
        owner,
        is_private,
        total_memberships
    )
    if not enough_slots:
        raise err.TaigaImportError(error_message, None)


def _create_project_object(data):
    # Create the project
    project_serialized = store_project(data)

    if not project_serialized:
        raise err.TaigaImportError(_("error importing project data"), None)

    return project_serialized.object if project_serialized else None


def _create_membership_for_project_owner(project):
    if project.memberships.filter(user=project.owner).count() == 0:
        if project.roles.all().count() > 0:
            Membership.objects.create(
                project=project,
                email=project.owner.email,
                user=project.owner,
                role=project.roles.all().first(),
                is_admin=True
            )


def _populate_project_object(project, data):
    def check_if_there_is_some_error(message=_("error importing project data"), project=None):
        errors = get_errors(clear=False)
        if errors:
            raise err.TaigaImportError(message, project, errors=errors)

    # Create roles
    store_roles(project, data)
    check_if_there_is_some_error(_("error importing roles"), None)

    # Create memberships
    store_memberships(project, data)
    _create_membership_for_project_owner(project)
    check_if_there_is_some_error(_("error importing memberships"),  project)

    # Create project attributes values
    store_project_attributes_values(project, data, "us_statuses", serializers.UserStoryStatusExportSerializer)
    store_project_attributes_values(project, data, "points", serializers.PointsExportSerializer)
    store_project_attributes_values(project, data, "task_statuses", serializers.TaskStatusExportSerializer)
    store_project_attributes_values(project, data, "issue_types", serializers.IssueTypeExportSerializer)
    store_project_attributes_values(project, data, "issue_statuses", serializers.IssueStatusExportSerializer)
    store_project_attributes_values(project, data, "priorities", serializers.PriorityExportSerializer)
    store_project_attributes_values(project, data, "severities", serializers.SeverityExportSerializer)
    check_if_there_is_some_error(_("error importing lists of project attributes"), project)

    # Create default values for project attributes
    store_default_project_attributes_values(project, data)
    check_if_there_is_some_error(_("error importing default project attributes values"), project)

    # Create custom attributes
    store_custom_attributes(project, data, "userstorycustomattributes",
                            serializers.UserStoryCustomAttributeExportSerializer)
    store_custom_attributes(project, data, "taskcustomattributes",
                            serializers.TaskCustomAttributeExportSerializer)
    store_custom_attributes(project, data, "issuecustomattributes",
                            serializers.IssueCustomAttributeExportSerializer)
    check_if_there_is_some_error(_("error importing custom attributes"), project)


    # Create milestones
    store_milestones(project, data)
    check_if_there_is_some_error(_("error importing sprints"), project)

    # Create user stories
    store_user_stories(project, data)
    check_if_there_is_some_error(_("error importing user stories"), project)

    # Createer tasks
    store_tasks(project, data)
    check_if_there_is_some_error(_("error importing tasks"), project)

    # Create issues
    store_issues(project, data)
    check_if_there_is_some_error(_("error importing issues"), project)

    # Create wiki pages
    store_wiki_pages(project, data)
    check_if_there_is_some_error(_("error importing wiki pages"), project)

    # Create wiki links
    store_wiki_links(project, data)
    check_if_there_is_some_error(_("error importing wiki links"), project)

    # Create tags
    store_tags_colors(project, data)
    check_if_there_is_some_error(_("error importing tags"), project)

    # Create timeline
    store_timeline_entries(project, data)
    check_if_there_is_some_error(_("error importing timelines"), project)

    # Regenerate stats
    project.refresh_totals()


def store_project_from_dict(data, owner=None):
    reset_errors()

    # Validate
    if owner:
        _validate_if_owner_have_enought_space_to_this_project(owner, data)

    # Create project
    project = _create_project_object(data)

    # Populate project
    try:
        _populate_project_object(project, data)
    except err.TaigaImportError:
        # reraise known inport errors
        raise
    except:
        # reise unknown errors as import error
        raise err.TaigaImportError(_("unexpected error importing project"), project)

    return project
