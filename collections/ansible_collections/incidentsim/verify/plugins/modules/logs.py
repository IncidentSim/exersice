#!/usr/bin/python
# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
module: logs
short_description: Look for a pattern in log entries inside an explicit time scope
description:
  - Scans entries with a timestamp in C([since, until)). The scope is mandatory.
  - I(expect=absent) passes only when the source was readable and the scope held at least
    I(min_entries) entries. An unreadable or empty source is C(available=false) and fails the task
    (I(fail_on_unavailable=true), default); it is never an empty success.
  - I(cursor) resumes after a previous scan; the module returns the new cursor.
options:
  text:
    description: Log lines starting with an ISO 8601 or epoch timestamp (for example C(journalctl -o short-iso)).
    type: str
  entries:
    description: Alternative to I(text), a list of C({ts, message, cursor}).
    type: list
    elements: dict
  since: {type: raw, required: true, description: Inclusive start (epoch seconds or ISO 8601; naive means UTC).}
  until: {type: raw, required: true, description: Exclusive end.}
  pattern: {type: str, required: true, description: Regular expression searched in each message.}
  expect: {type: str, default: present, choices: [present, absent], description: Whether matches are required or forbidden.}
  min_entries: {type: int, default: 1, description: Minimum entries that must be in scope.}
  source_available: {type: bool, default: true, description: Set false when reading the source failed.}
  cursor: {type: str, description: Cursor returned by a previous scan.}
  allow_unparsed:
    type: bool
    default: false
    description:
      - Non-blank lines without a parsable timestamp can hide errors. By default they make C(expect=absent)
        (and an unmatched C(expect=present)) unavailable. Set true only for sources with multi-line records.
  fail_on_unavailable: {type: bool, default: true, description: Fail the task when the scan cannot be trusted.}
author: IncidentSim
"""

EXAMPLES = r"""
- incidentsim.verify.logs:
    text: "{{ journal.stdout }}"
    source_available: "{{ journal.rc == 0 }}"
    since: "{{ window_start }}"
    until: "{{ window_end }}"
    pattern: "(?i)connection refused"
    expect: absent
    min_entries: 5
  register: app_log
"""

RETURN = r"""
passed: {description: Whether the expectation held., type: bool, returned: always}
available: {description: Whether the scan could be trusted., type: bool, returned: always}
reason: {description: Why the scan could not be trusted., type: str, returned: always}
matches: {description: Up to 20 matching messages., type: list, returned: always}
cursor: {description: Newest cursor seen., type: str, returned: always}
"""

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.incidentsim.verify.plugins.module_utils import observe, report


def main():
    module = AnsibleModule(
        argument_spec=dict(
            text=dict(type="str"),
            entries=dict(type="list", elements="dict"),
            since=dict(type="raw", required=True),
            until=dict(type="raw", required=True),
            pattern=dict(type="str", required=True),
            expect=dict(type="str", default="present", choices=["present", "absent"]),
            min_entries=dict(type="int", default=1),
            source_available=dict(type="bool", default=True),
            cursor=dict(type="str"),
            allow_unparsed=dict(type="bool", default=False),
            fail_on_unavailable=dict(type="bool", default=True),
        ),
        mutually_exclusive=[("text", "entries")],
        supports_check_mode=True,
    )
    p = module.params
    unparsed = 0
    entries = p["entries"]
    if entries is None:
        entries, unparsed = observe.parse_log_text(p["text"] or "")
    try:
        result = observe.scan_logs(
            entries, p["since"], p["until"], p["pattern"], expect=p["expect"], min_entries=p["min_entries"],
            available=p["source_available"], cursor=p["cursor"], unparsed=unparsed,
            allow_unparsed=p["allow_unparsed"])
    except (ValueError, KeyError, TypeError) as exc:
        module.fail_json(msg="invalid log scan: %s" % exc)
    except Exception as exc:  # invalid regular expression
        module.fail_json(msg="invalid log scan: %s" % exc)
    result["matches"] = [report.bound(m, 300) for m in result.get("matches", [])]
    result["changed"] = False
    if not result["available"] and p["fail_on_unavailable"]:
        module.fail_json(msg="logs unavailable: " + result["reason"], **result)
    module.exit_json(**result)


if __name__ == "__main__":
    main()
