"""Verify four live public source types through the Materials service."""
import hashlib
import json
import tempfile
from pathlib import Path

from hostrun.voyager_materials import MaterialsService


SAMPLES = {
    'news': ('https://science.nasa.gov/missions/hubble/nasa-celebrates-hubbles-35th-year-in-orbit/', 'Hubble'),
    'blog': ('https://blog.python.org/2024/10/python-3130-final-released/', 'Python'),
    'institution': ('https://www.iana.org/help/example-domains', 'domains'),
    'technical': ('https://docs.python.org/3/library/bisect.html', 'bisect'),
}


def check(home, url, expected):
    service = MaterialsService(home, token='local-probe')
    command = dict(schema_version=1, operation_id='acquire', url=url)
    result = service.register_web(command, token='local-probe')
    if result['web']['status'] != 'available':
        raise ValueError(result['web']['reason'])
    reopened = MaterialsService(home, token='local-probe')
    assert reopened.register_web(command, token='local-probe') == result
    package = reopened.get_package(result['material_id'], token='local-probe')
    content = package['original_text']
    snapshot = package['snapshot']
    assert expected.casefold() in content.casefold() and len(content) >= 200
    assert result['web']['mode'] == 'static' and result['web']['metadata']['title']
    assert hashlib.sha256(content.encode()).hexdigest() == snapshot['content_hash']
    anchors = result['web']['anchors']
    assert anchors and all(content[a['start']:a['end']].strip() for a in anchors)
    return dict(title=result['web']['metadata']['title'], extractor=snapshot['extraction_version'],
                paragraphs=len(anchors), bytes=snapshot['byte_length'], sha256=snapshot['content_hash'],
                original_sha256=snapshot['original_hash'])


if __name__ == '__main__':
    errors = 0
    with tempfile.TemporaryDirectory() as tmp:
        for kind, (address, keyword) in SAMPLES.items():
            try:
                facts = check(Path(tmp) / kind, address, keyword)
            except Exception as error:
                facts = dict(error=type(error).__name__, reason=str(error)[:180])
                errors += 1
            print(json.dumps(dict(kind=kind, url=address, **facts), ensure_ascii=True), flush=True)
    raise SystemExit(bool(errors))
