"""Lossless, append-only transport of original receipts through Release assets."""

import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

LIMIT = 32 * 1024 * 1024
MAX_BUNDLE = 256 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def sha(body):
    return hashlib.sha256(body).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def safe_path(name):
    p = PurePosixPath(name)
    if (not isinstance(name, str) or p.is_absolute() or '..' in p.parts
            or str(p) != name or '\\' in name or not p.parts
            or p.parts[0] not in ('ticks', 'observations', 'forecasts')):
        raise ValueError(f'unsafe evidence path: {name}')
    return name


def immutable(path, body):
    path = Path(path)
    # System-level aliases such as macOS /var are outside the evidence tree.
    for parent in path.absolute().parents:
        if parent.parent != Path(parent.anchor) and parent.is_symlink():
            raise ValueError(f'symlink destination parent: {parent}')
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f'symlink destination: {path}')
    try:
        with path.open('xb') as stream:
            stream.write(body)
    except FileExistsError:
        if path.read_bytes() != body:
            raise ValueError(f'immutable file conflict: {path}')


def inventory(root):
    result = {}
    for p in sorted(Path(root).rglob('*')):
        if p.is_symlink():
            raise ValueError(f'symlink source: {p}')
        if not p.is_file() or p.name == '.collection.lock':
            continue
        name = safe_path(p.relative_to(root).as_posix())
        if p.suffix not in ('.json', '.bin'):
            raise ValueError(f'unsupported evidence file: {name}')
        body = p.read_bytes()
        if len(body) > MAX_BUNDLE:
            raise ValueError('evidence file exceeds bundle bound')
        result[name] = {'sha256': sha(body), 'bytes': len(body)}
    return result


def catalog(data):
    result = {}
    for path in sorted((Path(data) / 'catalog').glob('*.json')):
        record = json.loads(path.read_text())
        for name, value in record['files'].items():
            safe_path(name)
            if name in result and result[name] != value:
                raise ValueError(f'catalog conflict: {name}')
            result[name] = value
    return result


def pack(files):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w') as archive:
        for name, body in sorted(files.items()):
            safe_path(name)
            info = tarfile.TarInfo(name)
            info.size = len(body)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(body))
    return gzip.compress(raw.getvalue(), mtime=0)


def unpack(body, max_bytes=MAX_BUNDLE):
    result = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(body), mode='r|gz') as archive:
        for member in archive:
            safe_path(member.name)
            if not member.isfile() or member.name in result or member.size < 0:
                raise ValueError('invalid or duplicate archive member')
            total += member.size
            if total > max_bytes or len(result) >= 100000:
                raise ValueError('archive exceeds decompression bound')
            data = archive.extractfile(member).read(member.size + 1)
            if len(data) != member.size:
                raise ValueError('archive member length differs')
            result[member.name] = data
    return result


def verified_download(backend, asset, digest=None, public=False):
    reader = backend.download_public if public and hasattr(backend, 'download_public') else backend.download
    body = reader(asset)
    if len(body) != asset['size'] or (digest and sha(body) != digest):
        raise ValueError('asset read-back differs')
    return body


def record_files(record, backend, public=True):
    files = {}
    def download(bundle):
        asset = bundle['asset']
        if public and hasattr(backend, 'public_asset'):
            asset = backend.public_asset(asset, record['tag'])
        return verified_download(backend, asset, bundle['sha256'], public=public)
    # Bound network fan-out while avoiding a winter archive's sequential GET latency.
    with ThreadPoolExecutor(max_workers=4) as pool:
        for blob in pool.map(download, record['bundles']):
            for name, body in unpack(blob).items():
                if name in files and files[name] != body:
                    raise ValueError('conflicting duplicate bundle member')
                files[name] = body
    expected = {name: {'sha256': sha(body), 'bytes': len(body)} for name, body in files.items()}
    if expected != record['files']:
        raise ValueError('record inventory differs from archived bytes')
    return files


def save_catalog(data, record, files):
    batch = record['context']['batchId']
    if not re.fullmatch(r'[a-zA-Z0-9-]+', batch):
        raise ValueError('invalid batch ID')
    known = catalog(data)
    for name, item in record['files'].items():
        if name in known and known[name] != item:
            raise ValueError(f'catalog conflict: {name}')
    for name, body in files.items():
        if name.endswith('.json'):
            immutable(Path(data) / 'evidence' / safe_path(name), body)
    immutable(Path(data) / 'catalog' / (batch + '.json'), encoded(record))


def validate_import(context, files, expected, known, complete):
    if 'importInventorySha256' not in context:
        return
    if expected is None or sha(encoded(expected)) != context['importInventorySha256'] or len(expected) != context['importFileCount']:
        raise ValueError('import inventory identity differs')
    if any(expected.get(name) != item for name, item in files.items()):
        raise ValueError('import inventory contains unexpected bytes')
    available = {**known, **files}
    if complete and any(available.get(name) != item for name, item in expected.items()):
        raise ValueError('import inventory is incomplete')


class Archive:
    def __init__(self, root, data, backend, context, expected_inventory=None):
        self.root, self.data, self.backend = Path(root), Path(data), backend
        self.context = context
        self.expected_inventory = expected_inventory
        validate_import(context, {}, expected_inventory, {}, False)
        if not re.fullmatch(r'[a-zA-Z0-9-]+', context['batchId']):
            raise ValueError('invalid batch ID')
        self.tag = 'receipts-' + context['batchId']
        self.known = catalog(data)
        self.files = {}
        self.bundles = []
        self.release = None
        self.context_ready = False
        self.limit = LIMIT

    def start(self):
        if self.release is None:
            self.release = self.backend.ensure_release(self.tag, self.context['codeCommit'])
        if not self.context_ready:
            context = encoded(self.context)
            asset = self.backend.upload(self.release, 'context.json', context)
            verified_download(self.backend, asset, sha(context))
            if self.expected_inventory is not None:
                body = encoded(self.expected_inventory)
                asset = self.backend.upload(self.release, 'import-inventory.json', body)
                verified_download(self.backend, asset, sha(body))
            self.context_ready = True

    def checkpoint(self):
        self.start()
        pending = {}
        total = 0
        for name, item in inventory(self.root).items():
            previous = self.files.get(name, self.known.get(name))
            if previous is not None:
                if previous != item:
                    raise ValueError(f'evidence conflict: {name}')
                continue
            if item['bytes'] > self.limit:
                raise ValueError('file exceeds configured bundle limit')
            if pending and total + item['bytes'] > self.limit:
                self._upload(pending)
                pending, total = {}, 0
            body = (self.root / name).read_bytes()
            if sha(body) != item['sha256'] or len(body) != item['bytes']:
                raise ValueError('source changed during snapshot')
            pending[name] = body
            total += len(body)
        if pending:
            self._upload(pending)

    def _upload(self, files):
        body = pack(files)
        digest = sha(body)
        asset = self.backend.upload(self.release, f'bundle-{digest}.tar.gz', body)
        verified_download(self.backend, asset, digest)
        self.bundles.append({'sha256': digest, 'asset': asset, 'encoding': 'tar+gzip'})
        for name, original in files.items():
            self.files[name] = {'sha256': sha(original), 'bytes': len(original)}

    def finish(self, completion='complete'):
        self.checkpoint()
        if self.expected_inventory is not None and inventory(self.root) != self.expected_inventory:
            raise ValueError('import inventory source changed before publication')
        validate_import(self.context, self.files, self.expected_inventory, self.known, True)
        record = {'schemaVersion': 1, 'context': self.context, 'tag': self.tag,
                  'releaseId': self.release['id'], 'archivedAt': now(),
                  'completion': completion, 'files': self.files, 'bundles': self.bundles}
        body = encoded(record)
        asset = self.backend.upload(self.release, 'archive.json', body)
        verified_download(self.backend, asset, sha(body))
        published = self.backend.publish(self.release)
        if published.get('draft') or not published.get('immutable'):
            raise ValueError('published release is not immutable')
        save_catalog(self.data, record, record_files(record, self.backend))
        return record


def reconcile(tag, data, backend):
    release = backend.get_release(tag)
    assets = {a['name']: a for a in release['assets'] if a.get('state', 'uploaded') == 'uploaded'}
    def metadata(name):
        candidates = [a for n,a in assets.items() if n == name or n.startswith(name + '.retry-')]
        if len(candidates) > 1:
            bodies = [verified_download(backend, a) for a in candidates]
            if any(b != bodies[0] for b in bodies): raise ValueError('conflicting metadata retries')
        return candidates[0] if candidates else None
    context_asset = metadata('context.json')
    if context_asset:
        context = json.loads(verified_download(backend, context_asset))
    else:
        batch = tag.removeprefix('receipts-')
        start = Path(data) / 'runs' / (batch + '-start.json')
        imported = Path(data) / 'imports' / (batch + '-start.json')
        if start.exists():
            context = {k:v for k,v in json.loads(start.read_text()).items() if k not in ('startedAt','state')}
        elif imported.exists(): context = json.loads(imported.read_text())
        else: raise ValueError('draft context unavailable; no original receipts reconstructed')
        if context['batchId'] != batch: raise ValueError('recovery context identity differs')
        body = encoded(context)
        asset = backend.upload(release, 'context.json', body)
        verified_download(backend, asset, sha(body))
    archive_asset = metadata('archive.json')
    expected = None
    if 'importInventorySha256' in context:
        expectation = metadata('import-inventory.json')
        if expectation:
            expected = json.loads(verified_download(backend, expectation, context['importInventorySha256']))
        else:
            body = (Path(data) / 'imports' / (context['batchId'] + '-inventory.json')).read_bytes()
            expected = json.loads(body)
            validate_import(context, {}, expected, {}, False)
            asset = backend.upload(release, 'import-inventory.json', body)
            verified_download(backend, asset, context['importInventorySha256'])
    if archive_asset:
        record = json.loads(verified_download(backend, archive_asset))
        if record['context'] != context or record['tag'] != tag or record['releaseId'] != release['id']:
            raise ValueError('archive identity differs')
    else:
        bundles, files = [], {}
        for name, asset in sorted(assets.items()):
            match = re.fullmatch(r'bundle-([0-9a-f]{64})\.tar\.gz(?:\.retry-[0-9a-f]+)?', name)
            if not match:
                continue
            blob = verified_download(backend, asset, match[1])
            for path, body in unpack(blob).items():
                item = {'sha256': sha(body), 'bytes': len(body)}
                if path in files and files[path] != item:
                    raise ValueError('conflicting recovered path')
                files[path] = item
            if hasattr(backend, 'public_asset'):
                asset = backend.public_asset(backend.asset_info(asset), tag)
            bundles.append({'sha256': match[1], 'asset': asset, 'encoding': 'tar+gzip'})
        record = {'schemaVersion': 1, 'context': context, 'tag': tag, 'releaseId': release['id'],
                  'archivedAt': now(), 'completion': 'recovered_interrupted', 'files': files, 'bundles': bundles,
                  'incompleteUploads':[a for a in release['assets'] if a.get('state', 'uploaded') != 'uploaded']}
        validate_import(context, files, expected, catalog(data), False)
        body = encoded(record)
        asset = backend.upload(release, 'archive.json', body)
        verified_download(backend, asset, sha(body))
    validate_import(context, record['files'], expected, catalog(data), record['completion'] == 'complete')
    originals = record_files(record, backend, public=not release['draft'])
    published = backend.publish(release) if release['draft'] else release
    if not published.get('immutable'):
        raise ValueError('recovery release is not immutable')
    save_catalog(data, record, originals)
    return record


def materialize(data, output, backend):
    count = 0
    for path in sorted((Path(data) / 'catalog').glob('*.json')):
        record = json.loads(path.read_text())
        for name, body in record_files(record, backend).items():
            immutable(Path(output) / safe_path(name), body)
            count += 1
    return {'files': count, 'output': str(output)}
