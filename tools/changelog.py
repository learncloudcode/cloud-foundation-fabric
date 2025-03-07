#!/usr/bin/env python3
# Copyright 2022 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import click
import collections

import ghapi.all
import iso8601

ORG = 'GoogleCloudPlatform'
REPO = 'cloud-foundation-fabric'
URL = f'https://github.com/{ORG}/{REPO}'

PullRequest = collections.namedtuple('PullRequest',
                                     'id author title merged_at labels')
Release = collections.namedtuple('Release', 'name published since pulls')


class Error(Exception):
  pass


def _paginate(method, **kw):
  'Paginate GitHub API call.'
  page = 1
  while True:
    result = method(page=page, per_page=100, **kw)
    for item in result:
      yield item
    if len(result) < 100:
      break
    page += 1


def changelog_load(path):
  'Parse changelog file and return structured data.'
  releases = []
  try:
    with open(path) as f:
      for l in f.readlines():
        l = l.strip()
        if l.startswith('## '):
          name, _, date = l[3:].partition(' - ')
          releases.append(Release(name[1:-1], date, None, []))
        elif l.startswith('- '):
          if not releases:
            raise Error(f'Pull found with no releases: {l}')
          releases[-1].pulls.append(l)
    return releases
  except (IOError, OSError) as e:
    raise Error(f'Cannot open {path}: {e.args[0]}')


def changelog_dumps(releases, overrides=None):
  'Return formatted changelog from structured data, overriding versions.'
  overrides = overrides or {}
  buffer = [
      ('# Changelog\n\n'
       'All notable changes to this project will be documented in this file.\n')
  ]
  ref_buffer = ['<!-- markdown-link-check-disable -->']
  for i, release in enumerate(releases):
    name, published, _, pulls = release
    prev_name = releases[i + 1].name if i + 1 < len(releases) else '0.1'
    if name != 'Unreleased':
      buffer.append(f'## [{name}] - {published}\n')
      ref_buffer.append(f'[{name}]: {URL}/compare/v{prev_name}...v{name}')
    else:
      buffer.append(f'## [{name}]\n')
      ref_buffer.append(f'[Unreleased]: {URL}/compare/v{prev_name}...HEAD')
    if name in overrides:
      buffer.append(
          f'<!-- {overrides[name].published} < {overrides[name].since} -->\n')
      pulls = group_pulls(overrides[name].pulls)
      for k in sorted(pulls.keys(), key=lambda s: s or ''):
        if k is not None:
          buffer.append(f'### {k}\n')
        for pull in pulls[k]:
          buffer.append(format_pull(pull))
        buffer.append('')
    else:
      for pull in pulls:
        buffer.append(pull)
    buffer.append('')
  return '\n'.join(buffer + [''] + ref_buffer)


def format_pull(pull):
  'Format pull request.'
  url = 'https://github.com'
  pull_url = f'{url}/{ORG}/{REPO}/pull'
  prefix = ''
  if 'incompatible change' in pull.labels:
    prefix = '**incompatible change:** '
  return (f'- [[#{pull.id}]({pull_url}/{pull.id})] '
          f'{prefix}'
          f'{pull.title} '
          f'([{pull.author}]({url}/{pull.author})) <!-- {pull.merged_at} -->')


def group_pulls(pulls):
  pulls.sort(key=lambda p: p.merged_at, reverse=True)
  groups = {None: []}
  for pull in pulls:
    labels = [l[3:] for l in pull.labels if l.startswith('on:')]
    if not labels:
      groups[None].append(pull)
      continue
    for label in labels:
      group = groups.setdefault(label.upper(), [])
      group.append(pull)
  return groups


def get_api(token, owner=ORG, name=REPO):
  'Get GitHub API object.'
  return ghapi.all.GhApi(owner=owner, repo=name, token=token)


def get_release_pulls(api, releases):
  'Get and add pull requests for releases.'
  i = 0
  for p in _paginate(api.pulls.list, base='master', state='closed',
                     sort='updated', direction='desc'):
    try:
      merged_at = iso8601.parse_date(p['merged_at'])
    except iso8601.ParseError:
      continue
    if releases[i].published and merged_at >= releases[i].published:
      continue
    if releases[i].since and merged_at <= releases[i].since:
      i += 1
      if i == len(releases):
        break
    releases[i].pulls.append(
        PullRequest(p['number'], p['user']['login'], p['title'], merged_at,
                    [l['name'] for l in p['labels']]))
  return releases


def get_releases(api, filter_names=None):
  'Get releases with optional filter on release names.'
  Buffer = collections.namedtuple('Buffer', 'name published')
  buffer = Buffer('Unreleased', None)
  for r in _paginate(api.repos.list_releases):
    published = iso8601.parse_date(r['published_at'])
    if not filter_names or buffer.name in filter_names:
      yield Release(buffer.name, buffer.published, published, [])
    buffer = Buffer(r['name'], published)
  if buffer and (not filter_names or buffer.name in filter_names):
    yield Release(buffer.name, buffer.published, None, [])


@click.command
@click.option(
    '--release', required=False, default=['Unreleased'], multiple=True,
    help='Release to replace, specify multiple times for more than one version.'
)
@click.option('--token', required=True, envvar='GH_TOKEN',
              help='GitHub API token.')
@click.option('--write/-w', is_flag=True, required=False, default=False,
              help='Write modified changelog file.')
@click.argument('changelog', required=False, default='CHANGELOG.md',
                type=click.Path(exists=True))
def main(token, changelog='CHANGELOG.md', release=None, write=False):
  api = get_api(token)
  releases = [r for r in get_releases(api, release)]
  releases = {r.name: r for r in get_release_pulls(api, releases)}
  try:
    changelog_releases = changelog_load(changelog)
    result = changelog_dumps(changelog_releases, releases)
  except Error as e:
    raise SystemExit(f'Cannot read or generate changelog: {e.args[0]}')
  if not write:
    print(result)
  else:
    try:
      open(changelog, 'w').write(result)
    except (IOError, OSError) as e:
      raise SystemExit('Cannot write to changelog file.')


if __name__ == '__main__':
  main()
