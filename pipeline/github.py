"""GitHub CLI adapter; authentication stays in gh, never in evidence records."""

import json
from pathlib import Path
import re
import subprocess
import tempfile
import uuid
import time
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class GitHub:
    def __init__(self, repo):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
            raise ValueError('invalid repository')
        self.repo = repo
        self.release_ids = {}

    def command(self, args, body=None):
        result = subprocess.run(['gh', *args], input=body, capture_output=True, timeout=600)
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors='replace')[-2000:])
        return result.stdout

    def api(self, path, method='GET', payload=None):
        args = ['api', f'repos/{self.repo}/{path}', '--method', method]
        body = None
        if payload is not None:
            args += ['--input', '-']; body = json.dumps(payload).encode()
        return json.loads(self.command(args, body))

    def all_releases(self):
        result = []
        for page in range(1, 10001):
            items = self.api(f'releases?per_page=100&page={page}')
            self.release_ids.update({r['tag_name']:r['id'] for r in items})
            result.extend(items)
            if len(items) < 100:
                return result
        raise ValueError('release pagination bound exceeded')

    def get_release(self, tag):
        if not re.fullmatch(r'[A-Za-z0-9-]+', tag):
            raise ValueError('invalid release tag')
        if tag not in self.release_ids:
            self.all_releases()
        if tag not in self.release_ids:
            raise ValueError(f'release not found: {tag}')
        # Draft releases do not yet have a resolvable published tag.
        release = self.api(f'releases/{self.release_ids[tag]}')
        # Release responses may only embed the first page of assets.
        assets = []
        for page in range(1, 1001):
            batch = self.api(f'releases/{release["id"]}/assets?per_page=100&page={page}')
            assets.extend(batch)
            if len(batch) < 100:
                release['assets'] = assets
                return release
        raise ValueError('asset pagination bound exceeded')

    def ensure_release(self, tag, target):
        if not re.fullmatch(r'[a-f0-9]{40}', target):
            raise ValueError('release requires exact code commit')
        # A failed read is never treated as permission to create a replacement.
        found = next((r for r in self.all_releases() if r['tag_name'] == tag), None)
        if found:
            return self.get_release(tag)
        release = self.api('releases', 'POST', {'tag_name': tag, 'target_commitish': target,
                        'name': tag, 'draft': True, 'make_latest': 'false',
                        'body': 'Original NOAA receipt evidence. Acquisition clocks are in the manifests; publication time is separate.'})
        self.release_ids[tag] = release['id']
        return release

    def upload(self, release, name, body):
        current = self.get_release(release['tag_name'])
        found = next((a for a in current['assets'] if a['name'] == name), None)
        if found and found.get('state', 'uploaded') != 'uploaded':
            # Keep the failed upload placeholder; retry under a distinct asset name.
            name += '.retry-' + uuid.uuid4().hex
            found = None
        if found:
            if self.download(found) != body:
                raise ValueError('existing asset conflict')
            return self.public_asset(self.asset_info(found), release['tag_name'])
        if not current['draft']:
            raise ValueError('cannot append to published evidence')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', name):
            raise ValueError('invalid asset name')
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / name; path.write_bytes(body)
            self.command(['release', 'upload', release['tag_name'], str(path), '--repo', self.repo])
        current = self.get_release(release['tag_name'])
        return self.public_asset(self.asset_info(next(a for a in current['assets'] if a['name'] == name)), release['tag_name'])

    @staticmethod
    def asset_info(asset):
        return {key: asset[key] for key in ('id', 'name', 'size', 'browser_download_url', 'state') if key in asset}

    def public_asset(self, asset, tag):
        if not re.fullmatch(r'[A-Za-z0-9-]+', tag) or not re.fullmatch(r'[A-Za-z0-9_.-]+', asset['name']):
            raise ValueError('invalid public asset identity')
        # Draft URLs contain an ephemeral untagged ID that disappears on publication.
        return {**asset, 'browser_download_url':f'https://github.com/{self.repo}/releases/download/{tag}/{asset["name"]}'}

    def download_public(self, asset):
        url = asset.get('browser_download_url', '')
        if not url.startswith(f'https://github.com/{self.repo}/releases/download/'):
            raise ValueError('invalid public asset URL')
        for attempt in range(3):
            try:
                with urlopen(Request(url, headers={'User-Agent':'noaa-water-level-research'}), timeout=120) as response:
                    return response.read(asset['size'] + 1)
            except (URLError, TimeoutError, ConnectionError, IncompleteRead) as error:
                if attempt == 2 or (isinstance(error, HTTPError) and error.code not in (429,500,502,503,504)):
                    raise
                time.sleep(2 ** attempt)

    def download(self, asset):
        if not isinstance(asset['id'], int) or asset['id'] <= 0:
            raise ValueError('invalid asset ID')
        return self.command(['api', f'repos/{self.repo}/releases/assets/{asset["id"]}',
                             '-H', 'Accept: application/octet-stream'])

    def publish(self, release):
        current = self.get_release(release['tag_name'])
        if current['draft']:
            self.api(f'releases/{current["id"]}', 'PATCH', {'draft': False, 'make_latest': 'false',
                     'tag_name':current['tag_name'], 'target_commitish':current['target_commitish']})
        return self.get_release(release['tag_name'])
