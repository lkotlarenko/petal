#!/usr/bin/env python3
"""Choose a monotonic release version from the source floor and fetched Git tags."""
import argparse
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent.parent
PATTERN = re.compile(r'v(\d+)\.(\d+)\.(\d+)$')


def choose(base, tags, current_tags=()):
    versions = [tuple(map(int, match.groups())) for tag in tags if (match := PATTERN.fullmatch(tag))]
    current = [tuple(map(int, match.groups())) for tag in current_tags if (match := PATTERN.fullmatch(tag))]
    if current:
        return max(current)  # Re-running the same commit keeps its version.
    latest = max(versions, default=(0, 0, -1))
    return max(base, (latest[0], latest[1], latest[2] + 1))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='Stamp the build checkout only')
    args = parser.parse_args()
    path = ROOT / 'petal/__init__.py'
    content = path.read_text()
    match = re.search(r'__version__ = "(\d+)\.(\d+)\.(\d+)"', content)
    base = tuple(map(int, match.groups()))
    def tags(*extra):
        return subprocess.check_output(['git', 'tag', '--list', *extra], cwd=ROOT, text=True).splitlines()
    version = '.'.join(map(str, choose(base, tags(), tags('--points-at', 'HEAD'))))
    if args.write:
        path.write_text(re.sub(r'__version__ = "[^"]+"', f'__version__ = "{version}"', content))
    print(version)


if __name__ == '__main__':
    main()
