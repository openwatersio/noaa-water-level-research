"""External publication proof for prospectively frozen calibration widths."""

import json
from pipeline.archive import sha, verified_download
from pipeline.health import utc


def validate_widths_publication(backend, tag, body, calibration_body=None):
    release = backend.get_release(tag)
    published = release.get('published_at')
    if release.get('draft') or not release.get('immutable') or not published:
        raise ValueError('widths require an immutable published release')
    frozen = json.loads(body)['frozenAt']
    if not utc('2026-12-03T13:00:00Z') <= utc(frozen) <= utc(published) < utc('2026-12-04T02:00:00Z'):
        raise ValueError('widths were not published within the prospective freeze window')
    assets = {a['name']:a for a in release['assets']}
    if verified_download(backend, assets['widths.json'], sha(body), public=True) != body:
        raise ValueError('widths publication differs')
    if calibration_body is not None:
        provenance = json.loads(verified_download(backend, assets['provenance.json'], public=True))
        if provenance['widthsSha256'] != sha(body) or provenance['reportSha256'] != sha(calibration_body):
            raise ValueError('freeze provenance differs from calibration and widths')
    return {'tag':tag, 'releaseId':release.get('id'), 'publishedAt':published, 'widthsSha256':sha(body)}
