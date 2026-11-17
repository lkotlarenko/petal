use anyhow::{Context, Result, bail};
use petal::{engine, files, media, model::*, process::Runner};
use std::{collections::BTreeMap, path::PathBuf, sync::mpsc};
fn main() -> Result<()> {
    unsafe {
        libc::signal(
            libc::SIGINT,
            petal::process::interrupt as *const () as libc::sighandler_t,
        );
        libc::signal(
            libc::SIGTERM,
            petal::process::interrupt as *const () as libc::sighandler_t,
        );
    }
    let mut argv = std::env::args().skip(1).collect::<Vec<_>>();
    if argv.is_empty() {
        return petal::ui::run(false, false);
    }
    match argv[0].as_str() {
        "--version" => {
            println!("Petal {} · Rust", env!("CARGO_PKG_VERSION"));
            return Ok(());
        }
        "--help" | "-h" => {
            println!(
                "Petal — smaller videos, kept on your computer.\n\nRun without arguments for the app.\n--version\n--install-desktop\nscan SOURCE OUTPUT\ncompress SOURCE OUTPUT [--codec av1_vaapi|hevc_vaapi] [--profile Balanced] [--resolution 1080p]\nsample VIDEO SAMPLE_FOLDER\nquality ORIGINAL COMPRESSED\nverify ORIGINAL COMPRESSED\ndoctor [--device /dev/dri/renderD128]\n\nOptions: --sampled-verification, --no-auto-quality, --audio copy|opus, --min-savings 5, --stream-policy compatible|skip|omit\nNothing is uploaded. FFmpeg and FFprobe must be installed."
            );
            return Ok(());
        }
        "--write-icons" => {
            let root = argv.get(1).context("Choose an icon output folder")?;
            petal::ui::write_icons(std::path::Path::new(root))?;
            return Ok(());
        }
        "--install-desktop" => {
            println!("{}", petal::ui::install_desktop()?.display());
            return Ok(());
        }
        "--demo" => return petal::ui::run(true, false),
        "--smoke-test" => return petal::ui::run(false, true),
        "self-test" => return self_test(),
        _ => {}
    }
    let command = argv.remove(0);
    let mut settings = Settings::default();
    let mut positional = Vec::new();
    let mut i = 0;
    while i < argv.len() {
        match argv[i].as_str() {
            "--sampled-verification" => settings.strict = false,
            "--no-auto-quality" => settings.auto_quality = false,
            "--omit-incompatible-streams" => settings.incompatible = "omit".into(),
            flag if flag.starts_with("--") => {
                i += 1;
                let value = argv.get(i).context("Missing option value")?.clone();
                match flag {
                    "--codec" => settings.codec = value,
                    "--profile" => settings.profile = value,
                    "--resolution" => settings.resolution = value,
                    "--device" => settings.device = value,
                    "--audio" => settings.audio = value,
                    "--stream-policy" => settings.incompatible = value,
                    "--min-savings" => settings.min_savings = value.parse()?,
                    _ => bail!("Unknown option {flag}"),
                }
            }
            _ => positional.push(PathBuf::from(&argv[i])),
        }
        i += 1;
    }
    settings.validate()?;
    let r = Runner::default();
    let (tx, rx) = mpsc::channel();
    let ev = Events { tx, ctx: None };
    let logger = std::thread::spawn(move || {
        let mut incomplete = false;
        for event in rx {
            match event {
                Event::Log(s) | Event::Done(s) | Event::Hardware(s) => eprintln!("{s}"),
                Event::Job(j) => {
                    incomplete |= matches!(j.status, Status::Failed | Status::Cancelled);
                    eprintln!("{:?}: {}: {}", j.status, j.relative.display(), j.message)
                }
                _ => {}
            }
        }
        incomplete
    });
    let state = files::xdg("state");
    std::fs::create_dir_all(&state)?;
    let result = (|| -> Result<()> {
        if command == "doctor" {
            println!("{:?}", engine::doctor(&settings, &state, &r, &ev, true)?);
            return Ok(());
        }
        anyhow::ensure!(
            positional.len() == 2,
            "Choose a source and output. Run petal --help for usage."
        );
        let source = &positional[0];
        let output = &positional[1];
        match command.as_str() {
            "move-skipped" | "move-failed" => {
                let source = std::fs::canonicalize(source)?;
                let category = if command == "move-skipped" {
                    "skipped"
                } else {
                    "failed"
                };
                let mut job = Job {
                    relative: source.file_name().context("Missing filename")?.into(),
                    source_key: files::fingerprint(&source)?,
                    source,
                    explicit_file: true,
                    status: if category == "skipped" {
                        Status::Skipped
                    } else {
                        Status::Failed
                    },
                    ..Default::default()
                };
                let output = files::absolute(output)?;
                let _lock = files::OutputLock::acquire(&output)?;
                let mut mover = files::Mover::new(&output, &state)?;
                mover.recover(&r.control)?;
                mover.relocate(&mut job, category, &r.control)?;
                println!("{}", job.relocated_to.unwrap().display());
            }
            "scan" | "compress" => {
                let (jobs, _) = engine::scan(
                    std::slice::from_ref(source),
                    output,
                    &state,
                    &settings,
                    &[],
                    &BTreeMap::new(),
                    &r,
                    &ev,
                )?;
                if command == "scan" {
                    println!("{}", serde_json::to_string_pretty(&jobs)?);
                } else {
                    engine::batch(jobs, &files::absolute(output)?, &state, &settings, &r, &ev)?;
                }
            }
            "sample" => {
                let job = Job {
                    source: std::fs::canonicalize(source)?,
                    input_root: Some(
                        source
                            .parent()
                            .context("Missing source folder")?
                            .to_path_buf(),
                    ),
                    ..Default::default()
                };
                engine::samples(&job, &files::absolute(output)?, &state, &settings, &r, &ev)?;
            }
            "quality" => println!(
                "{}",
                serde_json::to_string_pretty(&media::quality(
                    &media::probe(source, &r)?,
                    output,
                    &r
                )?)?
            ),
            "verify" => {
                media::verify(
                    &media::probe(source, &r)?,
                    output,
                    &settings,
                    &r,
                    false,
                    None,
                )?;
                println!("Compressed copy verified");
            }
            "encode-command" => {
                let m = media::probe(source, &r)?;
                let plan = media::plan(&m, &settings)?;
                anyhow::ensure!(
                    plan.artwork.is_empty(),
                    "Command preview with cover art needs prepared artwork"
                );
                let a = media::encode(&m, output, &settings, false, &BTreeMap::new(), None)?;
                println!(
                    "{}",
                    serde_json::to_string(
                        &a.iter().map(|s| s.to_string_lossy()).collect::<Vec<_>>()
                    )?
                );
            }
            _ => bail!("Unknown command. Run petal --help."),
        }
        Ok(())
    })();
    drop(ev);
    let incomplete = logger.join().unwrap_or(true);
    result?;
    anyhow::ensure!(
        command != "compress" || !incomplete,
        "Some videos did not complete; originals were kept. Inspect the log."
    );
    Ok(())
}
fn self_test() -> Result<()> {
    use sha2::{Digest, Sha256};
    let dir =
        std::env::temp_dir().join(format!("petal-self-test-{}", uuid::Uuid::new_v4().simple()));
    std::fs::create_dir(&dir)?;
    let result = (|| -> Result<()> {
        let src = dir.join("source");
        let out = dir.join("output");
        let state = dir.join("state");
        std::fs::create_dir_all(src.join("nested"))?;
        let input = src.join("nested/video.mp4");
        std::fs::write(&input, b"synthetic fixture - no user media")?;
        let bytes = std::fs::read(&input)?;
        let mut job = Job {
            source: input.clone(),
            relative: "nested/video.mp4".into(),
            destination: out.join("nested/video.mkv"),
            status: Status::Skipped,
            source_key: files::fingerprint(&input)?,
            ..Default::default()
        };
        let _lock = files::OutputLock::acquire(&out)?;
        let mut mover = files::Mover::new(&out, &state)?;
        mover.relocate(&mut job, "skipped", &Control::default())?;
        anyhow::ensure!(
            !input.exists() && std::fs::read(job.relocated_to.as_ref().unwrap())? == bytes,
            "Move test failed"
        );
        let original = src.join("trash-fixture.mp4");
        let compressed = out.join("trash-fixture.mkv");
        std::fs::write(&original, b"synthetic trash original")?;
        std::fs::write(&compressed, b"synthetic checked copy")?;
        let mut job = Job {
            source: original.clone(),
            destination: compressed.clone(),
            status: Status::Completed,
            proof: Some(Proof {
                source_key: files::fingerprint(&original)?,
                output_key: files::fingerprint(&compressed)?,
                source_sha: format!("{:x}", Sha256::digest(std::fs::read(&original)?)),
                output_sha: format!("{:x}", Sha256::digest(std::fs::read(&compressed)?)),
                full: true,
                ..Default::default()
            }),
            ..Default::default()
        };
        anyhow::ensure!(
            std::env::var_os("PETAL_TEST_TRASH").is_some(),
            "Self-test must run with PETAL_TEST_TRASH=1 and isolated XDG_DATA_HOME"
        );
        let data = std::env::var_os("XDG_DATA_HOME")
            .map(PathBuf::from)
            .context("An isolated XDG_DATA_HOME is required")?;
        anyhow::ensure!(
            data.starts_with(std::env::temp_dir()),
            "Self-test Trash must be in a temporary folder"
        );
        files::trash_original(&mut job, &Runner::default())?;
        anyhow::ensure!(
            job.trashed && !original.exists() && compressed.exists(),
            "Trash test failed"
        );
        println!("Native move, journal, checksum and desktop Trash tests passed");
        Ok(())
    })();
    let _ = std::fs::remove_dir_all(dir);
    result
}
