"""Private pipe endpoint for the bundled Node agent's configuration reader."""
import json
import sys
from pathlib import Path

from hostrun.embedded_agent import load_model_config
from hostrun.cleanup import InstanceBusy, InstanceInvalid


def main():
    try:
        data = load_model_config(Path(sys.argv[1]))
    except (OSError, ValueError, RuntimeError) as error:
        message = ('Model configuration identity validation failed' if isinstance(error, InstanceInvalid)
                   else 'Model configuration instance busy' if isinstance(error, InstanceBusy)
                   else 'Model configuration unavailable')
        print(message, file=sys.stderr)
        return 2
    # The parent consumes this pipe in memory; credentials never enter argv/env.
    sys.stdout.write(json.dumps(data, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
