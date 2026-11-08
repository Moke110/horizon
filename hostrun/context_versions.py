"""Stage-B read-only context version interface; no production knowledge claimed."""

def query_versions():
    return {name: {'provider': 'development_empty', 'schema_version': 1, 'version': 1,
                   'available': False} for name in ('knowledge', 'preferences')}
