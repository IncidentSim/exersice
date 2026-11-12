# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
name: dig
short_description: Safe nested lookup
description:
  - C(value | incidentsim.verify.dig('json.items.0.id', default)) returns the default instead of raising
    when any part of the path is missing. Also provided in this collection are C(technical_error),
    which returns a message when a registered result shows a verifier fault (missing binary,
    crashed module, unusable URL) and an empty string otherwise, and C(prometheus_samples), which
    parses Prometheus text keeping labels.
options:
  _input: {description: Value to inspect., type: raw, required: true}
  path: {description: Dotted path., type: str, required: true}
  default: {description: Returned when the path is missing., type: raw}
"""

from ansible_collections.incidentsim.verify.plugins.module_utils import observe, report


def dig(value, path, default=None):
    return report.dig(value, path, default)


def technical_error(result):
    return report.technical_error(result)


def prometheus_samples(text, ts=None):
    return observe.parse_prometheus(text, ts)


class FilterModule(object):
    def filters(self):
        return {"dig": dig, "technical_error": technical_error, "prometheus_samples": prometheus_samples}
