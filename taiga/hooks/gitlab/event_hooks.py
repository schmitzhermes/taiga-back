# Copyright (C) 2014 Andrey Antukh <niwi@niwi.be>
# Copyright (C) 2014 Jesús Espino <jespinog@gmail.com>
# Copyright (C) 2014 David Barragán <bameda@dbarragan.com>
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

import re
import os

from django.utils.translation import ugettext as _

from taiga.projects.issues.models import Issue
from taiga.projects.tasks.models import Task
from taiga.projects.userstories.models import UserStory
from taiga.projects.history.services import take_snapshot
from taiga.projects.notifications.services import send_notifications
from taiga.hooks.event_hooks import BaseEventHook
from taiga.hooks.exceptions import ActionSyntaxException

from .services import get_user_info_from_payload, get_gitlab_user
from ..services import get_element_from_ref, get_status_from_slug


class BaseGitlabEventHook(BaseEventHook):
    service_type = 'GitLab'

    # pusher
    def get_user_info_from_payload(self, payload=None):
        if not payload:
            return get_user_info_from_payload(self.payload)
        else:
            return get_user_info_from_payload(payload)


class PushEventHook(BaseGitlabEventHook):
    def process_event(self):
        if self.payload is None:
            return

        commits = self.payload.get("commits", [])
        for commit in commits:
            for match in self.process_message(commit.get("message", None)):
                self._process_commit(commit=commit, **match)

    def _process_commit(self, ref, status_slug, commit):
        element = get_element_from_ref(self.project, ref)
        status = get_status_from_slug(self.project, element, status_slug)
        element.status = status
        element.save()

        commit_id = commit.get("id", None)
        commit_url = commit.get("url", None)
        # @todo replace_gitlab_references()?
        commit_message = commit.get("message").strip()
        author_info = self.get_user_info_from_payload(commit)
        author_user = author_info.get('user')

        """ Do we care about pushed by vs authored by?
        if author_info.get('email') != self.pusher.get('email'):
            if self.pusher.get('user') is not None:
                user = self.pusher.get('user')
                pushed_by = _(" (Pushed by @{pusher_username})").format(
                    pusher_username=user.get_username()
                )
            else:
                pushed_by = _(" (Pushed by [{pusher_username}](mailto:{pusher_url}))").format(
                    pusher_username=self.pusher.get('name'),
                    pusher_url=self.pusher.get('email')
                )
        else:
            pushed_by = ""
        """

        if not all([commit_id, commit_url]):
            raise ActionSyntaxException(_("Invalid commit information"))

        # we can use a real user
        if author_user is not None and not author_user.is_system:
            comment = _(self.messages.get('native').get('push')).format(
                username=author_user.username,
                service_type=self.service_type,
                status=status.name,
                commit_id=commit_id[:7],
                commit_url=commit_url,
                commit_message=commit_message)

        # use what info we have
        elif "name" in author_info and "email" in author_info:
            comment = _(self.messages.get('system').get('push')).format(
                name=author_info.get("name'"),
                service_type=self.service_type,
                status=status.name,
                user_url=author_info.get("url"),
                commit_id=str(commit_id)[:7],
                commit_url=commit_url,
                commit_message=commit_message)

        snapshot = take_snapshot(element,
                                 comment=comment,
                                 user=author_user)
        send_notifications(element, history=snapshot)


def replace_gitlab_references(project_url, wiki_text):
    if wiki_text is None:
        wiki_text = ""

    template = "\g<1>[GitLab#\g<2>]({}/issues/\g<2>)\g<3>".format(project_url)
    return re.sub(r"(\s|^)#(\d+)(\s|$)", template, wiki_text, 0, re.M)


class IssuesEventHook(BaseGitlabEventHook):
    def process_event(self):
        attrs = self.payload.get('object_attributes', {})

        if attrs.get("action", "") != "open":
            return

        subject = attrs.get('title', None)
        gitlab_reference = attrs.get('iid', None)
        gitlab_issue_url = attrs.get('url', None)
        gitlab_project_url = None
        user = self.user_info.get('user')

        if gitlab_issue_url:
            # the last two sections are always the "/issues/:id"
            gitlab_project_url = '/'.join(gitlab_issue_url.split('/')[:-2])

        description = replace_gitlab_references(gitlab_project_url, attrs.get('description', None))

        if not all([subject, gitlab_reference, gitlab_issue_url, gitlab_project_url]):
            raise ActionSyntaxException(_("Invalid issue information"))

        issue = Issue.objects.create(
            project=self.project,
            subject=subject,
            description=replace_gitlab_references(gitlab_project_url, description),
            status=self.project.default_issue_status,
            type=self.project.default_issue_type,
            severity=self.project.default_severity,
            priority=self.project.default_priority,
            external_reference=['gitlab', gitlab_reference],
            owner=user
        )

        take_snapshot(issue, user=user)

        comment = _("Issue created from GitLab.")

        if user is not None and not user.is_system:
            comment = _(self.messages.get('native').get('issue')).format(
                username=user.username,
                service_type=self.service_type,
                remote_ref=gitlab_reference,
                subject=subject,
                remote_url=gitlab_issue_url,
                description=description
            )

        # use what info we have
        elif "name" in self.user_info and "email" in self.user_info:
            comment = _(self.messages.get('system').get('issue')).format(
                name=self.user_info.get("name'"),
                user_url=self.user_info.get('url'),
                service_type=self.service_type,
                remote_ref=gitlab_reference,
                subject=subject,
                remote_url=gitlab_issue_url,
                description=description
            )

        snapshot = take_snapshot(issue, comment=comment, user=user)
        send_notifications(issue, history=snapshot)


class IssueCommentEventHook(BaseGitlabEventHook):
    def process_event(self):
        attrs = self.payload.get('object_attributes', {})

        if attrs.get("noteable_type", None) != "Issue":
            return

        number = self.payload.get('issue', {}).get('iid', None)
        subject = self.payload.get('issue', {}).get('title', None)

        project_url = self.payload.get('repository', {}).get('homepage', None)

        gitlab_url = os.path.join(project_url, "issues", str(number))
        gitlab_user_name = self.payload.get('user', {}).get('username', None)
        gitlab_user_url = os.path.join(os.path.dirname(os.path.dirname(project_url)), "u", gitlab_user_name)

        comment_message = attrs.get('note', None)
        comment_message = replace_gitlab_references(project_url, comment_message)

        user = get_gitlab_user(None)

        if not all([comment_message, gitlab_url, project_url]):
            raise ActionSyntaxException(_("Invalid issue comment information"))

        issues = Issue.objects.filter(external_reference=["gitlab", gitlab_url])
        tasks = Task.objects.filter(external_reference=["gitlab", gitlab_url])
        uss = UserStory.objects.filter(external_reference=["gitlab", gitlab_url])

        for item in list(issues) + list(tasks) + list(uss):
            if number and subject and gitlab_user_name and gitlab_user_url:
                comment = _("Comment by [@{gitlab_user_name}]({gitlab_user_url} "
                            "\"See @{gitlab_user_name}'s GitLab profile\") "
                            "from GitLab.\nOrigin GitLab issue: [gl#{number} - {subject}]({gitlab_url} "
                            "\"Go to 'gl#{number} - {subject}'\")\n\n"
                            "{message}").format(gitlab_user_name=gitlab_user_name,
                                                gitlab_user_url=gitlab_user_url,
                                                number=number,
                                                subject=subject,
                                                gitlab_url=gitlab_url,
                                                message=comment_message)
            else:
                comment = _("Comment From GitLab:\n\n{message}").format(message=comment_message)

            snapshot = take_snapshot(item, comment=comment, user=user)
            send_notifications(item, history=snapshot)
