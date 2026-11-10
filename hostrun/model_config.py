"""Private pipe endpoint for the bundled Node agent's configuration reader."""
import json
import sys
from pathlib import Path

from hostrun.embedded_agent import load_model_config
from hostrun.cleanup import InstanceBusy, InstanceInvalid, load_managed_model_config


def main():
    try:
        home = Path(sys.argv[1])
        managed, data, _ = load_managed_model_config(home)
        if not managed:
            data = load_model_config(home)
    except (OSError, ValueError, RuntimeError) as error:
        message = ('Model configuration identity validation failed' if isinstance(error, InstanceInvalid)
                   else 'Model configuration instance busy' if isinstance(error, InstanceBusy)
                   else 'Model configuration unavailable')
        print(message, file=sys.stderr)
        return 2
    # The parent consumes this pipe in memory; credentials never enter argv/env.
    sys.stdout.write(json.dumps({'managed': managed, 'config': data or {}}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
