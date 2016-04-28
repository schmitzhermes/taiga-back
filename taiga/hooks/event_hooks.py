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

import re


class BaseEventHook:
    messages = {
        # We have a real User to use
        'native': {
            'push': "@{username} changed the status to \"{status}\" "
                    "from {service_type} commit [{commit_id}]({commit_url}):\n\n"
                    "> {commit_message}",

            'issue': "@{username} created issue via {service_type}.\n"
                    "Original {service_type} issue: [{remote_ref} - {subject}]({remote_url}\n\n"
                    "> {description}",

            'comment': "@{username} referenced this on {service_type}: "
                    "[{remote_comment_ref} - {issue_subject}]({issue_url}\n\n"
                    "{message}"
        },

        # We have to use the generic system user
        'system': {
            'push': "[{name}]({user_url}) changed the status to \"{status}\" "
                    "from {service_type} commit [{commit_id}]({commit_url}):\n\n"
                    "> {commit_message}",

            'issue': "[{name}]({user_url}) created issue via {service_type}.\n"
                    "Original {service_type} issue: [{remote_ref} - {subject}]({remote_url}\n\n"
                    "> {description}",

            'comment': ''
        }
    }

    def __init__(self, project, payload):
        self.project = project
        self.payload = payload
        self.user_info = self.get_user_info_from_payload()

    def process_event(self):
        raise NotImplementedError("process_event must be overwritten")

    def get_user_info_from_payload(self):
        """
        Return a dict with id, name, email, url, and user.
        user is a User, either the system user for the service or a real user
        """
        raise NotImplementedError("get_user_info_from_payload must be overwritten")

    def process_message(self, message):
        """
        The message we look for is like
            TG-XX #yyyyyy

        Where:
            XX: is the ref for us, issue or task
            yyyyyy: is the status slug we are setting
        """

        p = re.compile("tg-(\d+) +#([-\w]+)")

        for match in p.finditer(message.lower()):
            yield {
                'ref': match.group(1),
                'status_slug': match.group(2)
            }
