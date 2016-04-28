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

from django.utils.translation import ugettext as _

from taiga.projects.models import IssueStatus, TaskStatus, UserStoryStatus
from taiga.projects.issues.models import Issue
from taiga.projects.tasks.models import Task
from taiga.projects.userstories.models import UserStory
from taiga.hooks.exceptions import ActionSyntaxException


def get_element_from_ref(project, ref):
    if Issue.objects.filter(project=project, ref=ref).exists():
        model_class = Issue
    elif Task.objects.filter(project=project, ref=ref).exists():
        model_class = Task
    elif UserStory.objects.filter(project=project, ref=ref).exists():
        model_class = UserStory
    else:
        raise ActionSyntaxException(_("The referenced element doesn't exist"))

    return model_class.objects.get(project=project, ref=ref)


def get_status_from_slug(project, element, status_slug):
    if isinstance(element, Issue):
        status_class = IssueStatus
    elif isinstance(element, Task):
        status_class = TaskStatus
    elif isinstance(element, UserStory):
        status_class = UserStoryStatus
    else:
        raise ActionSyntaxException(_("The referenced element doesn't exist"))

    try:
        return status_class.objects.get(project=project, slug=status_slug)
    except status_class.DoesNotExist:
        raise ActionSyntaxException(_("The status doesn't exist"))
