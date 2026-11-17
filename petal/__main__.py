from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import sys
import threading


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--petal-child":
        from ._child import main as child_main
        del sys.argv[1]
        return child_main()
    os.environ.setdefault("LIBVA_DRIVER_NAME", "radeonsi")
    parser = argparse.ArgumentParser(description="Petal · careful VA-API video compression")
    from . import __version__
    parser.add_argument("--version", action="version", version=f"Petal {__version__}")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command")
    check = commands.add_parser("doctor", help="Run real hardware encode/decode tests at the selected ceiling")
    check.add_argument("--device", default="/dev/dri/renderD128")
    check.add_argument("--report", type=Path)
    from .models import RESOLUTIONS
    check.add_argument("--resolution", choices=tuple(RESOLUTIONS), default="1080p")
    scan = commands.add_parser("scan", help="Probe a source tree without encoding")
    scan.add_argument("source", type=Path)
    scan.add_argument("output", type=Path)
    for name in ("compress", "sample"):
        cmd = commands.add_parser(name)
        cmd.add_argument("source", type=Path, help="Source folder (compress) or video file (sample)")
        cmd.add_argument("output", type=Path)
        cmd.add_argument("--codec", choices=("av1_vaapi", "hevc_vaapi"), default="av1_vaapi")
        cmd.add_argument("--profile", choices=("High Quality", "Balanced", "Maximum Savings"), default="Balanced")
        cmd.add_argument("--device", default="/dev/dri/renderD128")
        cmd.add_argument("--audio", choices=("copy", "opus"), default="copy")
        cmd.add_argument("--sampled-verification", action="store_true", help="Decode beginning/middle/end instead of the full output")
        cmd.add_argument("--min-savings", type=float, default=5)
        cmd.add_argument("--omit-incompatible-streams", action="store_true")
        cmd.add_argument("--stream-policy", choices=("compatible", "skip", "omit"), default="compatible")
        cmd.add_argument("--no-auto-quality", action="store_true", help="Disable first-three VMAF spot checks")
        cmd.add_argument("--resolution", choices=tuple(RESOLUTIONS), default="1080p", help="Output ceiling, with orientation preserved and no upscaling")
    args = parser.parse_args()
    if args.smoke_test:
        from .gui import smoke
        return smoke()
    if not args.command:
        try:
            from .gui import run
        except ImportError as error:
            print(f"Petal needs PySide6: {error}\nInstall pyside6 on CachyOS or use requirements.txt.", file=sys.stderr)
            return 2
        return run()
    from .models import Settings, xdg_path
    from .process import Cancelled, Runner
    cancel = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: cancel.set())
    signal.signal(signal.SIGTERM, lambda *_: cancel.set())
    def log(line):
        print(line, file=sys.stderr, flush=True)
    runner = Runner(cancel, log)
    try:
        from .capabilities import detect
        from . import hardware_cache
        if args.command == "doctor":
            config = Settings(device=args.device, resolution=args.resolution)
            report = detect(config, runner)
            hardware_cache.save(xdg_path("state"), config, report)
            text = json.dumps(report.to_dict(), indent=2)
            print(text)
            if args.report:
                args.report.write_text(text + "\n")
            return 0 if any(report.tested.values()) else 1
        from .discovery import discover
        if args.command == "scan":
            jobs = discover(args.source, args.output, runner)
            print(json.dumps([{"source": str(j.source), "output": str(j.destination), "status": j.status.value,
                               "duration": j.media.duration if j.media else None, "message": j.message} for j in jobs], indent=2, ensure_ascii=False))
            return 0
        settings = Settings(codec=args.codec, profile=args.profile, device=args.device, audio=args.audio,
                            strict=not args.sampled_verification, min_savings=args.min_savings,
                            incompatible="omit" if args.omit_incompatible_streams else args.stream_policy,
                            auto_quality=not args.no_auto_quality, resolution=args.resolution)
        # Reject unsafe roots before running any encodes, even synthetic tests.
        if args.command == "compress":
            from .discovery import validate_roots
            validate_roots(args.source, args.output)
        elif args.output.resolve() == args.source.resolve() or args.source.resolve().parent == args.output.resolve():
            raise ValueError("Put comparison samples in a separate directory from the source video")
        capabilities = hardware_cache.load(xdg_path("state"), settings)
        if capabilities is None:
            capabilities = detect(settings, runner)
            hardware_cache.save(xdg_path("state"), settings, capabilities)
        if not any(capabilities.supports(settings, bits) for bits in (8, 10)):
            print(json.dumps(capabilities.to_dict(), indent=2))
            return 1
        from .engine import Batch, benchmark
        if args.command == "sample":
            from .media import probe
            print(json.dumps(benchmark(probe(args.source.resolve(), runner), args.output.resolve(), settings, capabilities, runner), indent=2))
            return 0
        jobs = discover(args.source, args.output, runner)
        def event(kind, payload):
            if kind == "job":
                log(f"{payload.status.value}: {payload.relative}: {payload.message}")
            elif kind == "log":
                log(str(payload))
        batch = Batch(jobs, args.source, args.output, settings, capabilities, xdg_path("state"), event)
        batch.cancel = cancel
        batch.runner.cancel = cancel
        batch.run()
        from .models import Status
        return 130 if cancel.is_set() else 1 if any(j.status == Status.FAILED for j in jobs) else 0
    except Cancelled:
        return 130
    except Exception as error:
        print(f"Petal: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
