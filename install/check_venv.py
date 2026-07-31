"""Exit 0 if the interpreter running this file is a usable project venv.

Kept as a file rather than a `python -c "..."` one-liner on purpose. The inline
form embedded parentheses and comparison operators in a quoted cmd.exe argument
inside a parenthesised block, which cmd mis-parsed: the probe reported failure
while the identical command exited 0 at the prompt. The installer then
discarded a healthy .venv on every run and rebuilt it, costing ~3 minutes and
leaving a backup directory behind each time.

Usage:
    .venv\\Scripts\\python.exe install\\check_venv.py
"""

import sys

MIN_VERSION = (3, 10)
MAX_VERSION_EXCLUSIVE = (3, 15)


def main() -> int:
    in_venv = sys.prefix != sys.base_prefix
    version = sys.version_info[:2]
    supported = MIN_VERSION <= version < MAX_VERSION_EXCLUSIVE

    if not in_venv:
        print("not a virtual environment")
        return 1
    if not supported:
        print(
            "unsupported Python {}.{} (need >={}.{}, <{}.{})".format(
                version[0], version[1],
                MIN_VERSION[0], MIN_VERSION[1],
                MAX_VERSION_EXCLUSIVE[0], MAX_VERSION_EXCLUSIVE[1],
            )
        )
        return 1

    print("ok {}.{}".format(version[0], version[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
