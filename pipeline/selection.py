"""Build a disposable primary-source view; never edit original evidence."""

import json
import os
from pathlib import Path

from pipeline.health import utc
from pipeline.archive import verified_download


def validate_transition_publication(transition, body, backend):
    if transition.get('primaryBeforeBoundary') != 'legacy' or transition.get('primaryAtAndAfterBoundary') != 'github':
        raise ValueError('unsupported primary source rule')
    boundary = transition.get('boundaryAt')
    if boundary is None:
        return
    tag = transition.get('publicationTag')
    if not tag:
        raise ValueError('source boundary requires immutable preregistration')
    release = backend.get_release(tag)
    if release.get('draft') or not release.get('immutable') or not release.get('published_at') or utc(release['published_at']) >= utc(boundary):
        raise ValueError('source boundary was not published before it took effect')
    asset = next((a for a in release['assets'] if a['name'] == 'collector-transition.json'), None)
    if asset is None or verified_download(backend, asset, public=True) != body:
        raise ValueError('source boundary differs from immutable preregistration')


def eligible_source(origin, received_at, boundary):
    if origin not in ('legacy', 'github'):
        raise ValueError('unknown collector origin')
    if boundary is None:
        return origin == 'legacy'
    return (utc(received_at) < utc(boundary)) == (origin == 'legacy')


def primary_view(root, data, output, transition):
    root, data, output = Path(root), Path(data), Path(output)
    if output.exists():
        raise ValueError('primary view must be a new directory')
    origins = {}
    for p in (data / 'catalog').glob('*.json'):
        record = json.loads(p.read_text())
        for name in record['files']:
            if len(Path(name).parts) != 2 or not name.endswith('.json'): continue
            previous = origins.get(name)
            origin = record['context']['origin']
            if previous is not None and previous != origin:
                raise ValueError('ambiguous acquisition source')
            origins[name] = origin
    boundary = transition.get('boundaryAt')
    excluded = []
    output.mkdir(parents=True)
    for p in root.rglob('*'):
        if not p.is_file(): continue
        name = p.relative_to(root).as_posix()
        # Only these top-level manifests enter the original scientific selectors.
        if p.suffix == '.json' and len(p.relative_to(root).parts) == 2 and p.parent.name in ('observations','forecasts','ticks'):
            document = json.loads(p.read_text())
            records = [document['record']] if 'record' in document else list(document.get('products', {}).values())
            origin = origins.get(name)
            if origin not in ('legacy', 'github'):
                raise ValueError('manifest lacks a known source')
            # Failed receipts remain diagnostic; they cannot become valid input in the reader.
            times = [r.get('receivedAt') or r.get('finishedAt') or r.get('requestStartedAt') for r in records]
            keep = eligible_source(origin, document.get('startedAt') or document.get('finishedAt'), boundary) if not times and (boundary is None or document.get('startedAt') or document.get('finishedAt')) else all(
                at is not None and eligible_source(origin, at, boundary) for at in times)
            if not times and boundary is not None and not (document.get('startedAt') or document.get('finishedAt')):
                keep = False
            if not keep:
                excluded.append(name)
                continue
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        os.link(p, target)
    return {'boundaryAt':boundary, 'excludedManifests':excluded,
            'rule':'legacy before boundary, GitHub at/after; GitHub shadow excluded until boundary is published'}
