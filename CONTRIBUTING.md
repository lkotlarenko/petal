# Contributing

Petal is a native Rust app for local video compression. Keep common actions easy to understand and advanced choices in Settings. User-facing wording should say what happens to files; reserve release/build terminology for development documentation.

Read [Architecture](docs/DESIGN.md), [file safety](docs/ORGANIZING.md) and [build instructions](docs/RELEASING.md). Use `cargo build --locked` and `./scripts/test.sh`. Keep Cargo.lock committed and update notices when dependency licensing changes. Code is GPL-3.0-only.

Use synthetic media and temporary folders for tests. Never require private collections or trash real user files. Preserve no-overwrite behavior, source identity checks, durable recovery evidence, cancellation and the distinction between a saved UI result and verification proof. Do not turn a failed Trash operation into permanent deletion.

A change should explain the problem, resulting behavior, validation and material limitations. UI changes need a real desktop review at typical and smaller window sizes. New filesystem or media behavior needs meaningful failure/recovery tests. CI lacks actual VA-API hardware; record host checks honestly. Keep documentation and screenshots aligned with the shipping native app.

Report bugs with app version, distro/session, GPU/driver/FFmpeg details, steps and a small redacted log excerpt. Prefer a synthetic reproduction; do not upload private media.
