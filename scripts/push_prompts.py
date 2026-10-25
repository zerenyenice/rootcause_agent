"""Upload the packaged prompts to Langfuse as new prompt versions.

Dry-run by default: prints the prompt names and makes no API call (needs no
credentials). ``--apply`` creates one new version per prompt; pass ``--label``
to also move a label (for example ``production``) onto the new version,
otherwise the version is only served once promoted in the Langfuse UI.

    uv run python scripts/push_prompts.py               # dry run
    uv run python scripts/push_prompts.py --apply       # upload, no label
    uv run python scripts/push_prompts.py --apply --label staging

Credentials come from ROOTCAUSE_LANGFUSE_HOST / _PUBLIC_KEY / _SECRET_KEY.
"""

import argparse
import sys
from collections.abc import Sequence

from rootcause.agents.prompts import PROMPT_NAMES, load_packaged_prompt
from rootcause.clients.langfuse import push_prompt
from rootcause.errors import ConfigurationError
from rootcause.settings import get_settings

EXIT_OK = 0
EXIT_UPLOAD_FAILED = 1
EXIT_CONFIGURATION = 2


def _parse(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", dest="apply", action="store_false", help="list prompts only (default)"
    )
    mode.add_argument("--apply", dest="apply", action="store_true", help="upload to Langfuse")
    parser.add_argument("--label", help="label to put on each new version (e.g. production)")
    parser.set_defaults(apply=False)
    return parser.parse_args(list(argv))


def _apply(label: str | None) -> int:
    try:
        settings = get_settings()
        for name in PROMPT_NAMES:
            labels = (label,) if label else ()
            push_prompt(name, load_packaged_prompt(name), settings, labels=labels)
            print(f"uploaded {name}")
    except ConfigurationError as error:
        # The message names the missing ROOTCAUSE_LANGFUSE_* variables, never values.
        print(f"push_prompts: {error}", file=sys.stderr)
        return EXIT_CONFIGURATION
    except Exception as error:  # noqa: BLE001 - report the failure type, not SDK internals
        print(f"push_prompts: upload failed ({type(error).__name__})", file=sys.stderr)
        return EXIT_UPLOAD_FAILED
    return EXIT_OK


def main(argv: Sequence[str]) -> int:
    """Run the CLI; returns the process exit code."""
    args = _parse(argv)
    if not args.apply:
        for name in PROMPT_NAMES:
            print(f"would upload {name}")
        return EXIT_OK
    return _apply(args.label)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
