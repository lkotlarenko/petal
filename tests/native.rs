use anyhow::Result;
use petal::{
    engine, files, media,
    model::*,
    process::{Runner, args},
};
use std::{collections::BTreeMap, ffi::OsString, fs, path::Path, sync::mpsc, time::Duration};
fn fixture(path: &Path, w: u32, h: u32) -> Result<()> {
    let mut a = args(&[
        "-hide_banner",
        "-nostdin",
        "-v",
        "error",
        "-n",
        "-f",
        "lavfi",
        "-i",
        &format!("testsrc2=size={w}x{h}:rate=12"),
        "-t",
        "1",
        "-c:v",
        "ffv1",
    ]);
    a.push(path.into());
    Runner::default().run("ffmpeg", &a, Some(Duration::from_secs(20)))?;
    Ok(())
}
fn events() -> Events {
    let (tx, _) = mpsc::channel();
    Events { tx, ctx: None }
}
fn job(root: &Path) -> Result<Job> {
    let source = root.join("source/nested/猫.mp4");
    fs::create_dir_all(source.parent().unwrap())?;
    fs::write(&source, vec![17_u8; 100000])?;
    Ok(Job {
        source: source.clone(),
        relative: "nested/猫.mp4".into(),
        destination: root.join("output/nested/猫.mkv"),
        status: Status::Skipped,
        source_key: files::fingerprint(&source)?,
        ..Default::default()
    })
}
#[test]
fn same_filesystem_move_retains_inode_and_never_copies_bytes() -> Result<()> {
    use std::os::unix::fs::MetadataExt;
    let t = tempfile::tempdir()?;
    let mut j = job(t.path())?;
    let inode = fs::metadata(&j.source)?.ino();
    let root = t.path().join("output");
    let mut mover = files::Mover::new(&root, &t.path().join("state"))?;
    mover.relocate(&mut j, "skipped", &Control::default())?;
    assert!(!j.source.exists());
    let target = j.relocated_to.unwrap();
    assert_eq!(fs::metadata(&target)?.ino(), inode);
    assert_eq!(fs::read(target)?, vec![17_u8; 100000]);
    Ok(())
}
#[test]
fn collisions_keep_both_original_and_existing_destination() -> Result<()> {
    let t = tempfile::tempdir()?;
    let mut j = job(t.path())?;
    let root = t.path().join("output");
    let dst = root.join("skipped").join(&j.relative);
    fs::create_dir_all(dst.parent().unwrap())?;
    fs::write(&dst, b"existing")?;
    assert!(
        files::Mover::new(&root, &t.path().join("state"))?
            .relocate(&mut j, "skipped", &Control::default())
            .is_err()
    );
    assert!(j.source.exists());
    assert_eq!(fs::read(dst)?, b"existing");
    Ok(())
}
#[test]
fn changed_original_is_not_moved() -> Result<()> {
    let t = tempfile::tempdir()?;
    let mut j = job(t.path())?;
    fs::write(&j.source, b"edited")?;
    assert!(
        files::Mover::new(&t.path().join("output"), &t.path().join("state"))?
            .relocate(&mut j, "skipped", &Control::default())
            .is_err()
    );
    assert_eq!(fs::read(j.source)?, b"edited");
    Ok(())
}
#[test]
fn traversal_and_symlink_output_are_rejected() -> Result<()> {
    use std::os::unix::fs::symlink;
    let t = tempfile::tempdir()?;
    let mut j = job(t.path())?;
    let root = t.path().join("output");
    fs::create_dir_all(&root)?;
    let outside = t.path().join("outside");
    fs::create_dir(&outside)?;
    symlink(&outside, root.join("skipped"))?;
    assert!(
        files::Mover::new(&root, &t.path().join("state"))?
            .relocate(&mut j, "skipped", &Control::default())
            .is_err()
    );
    j.relative = "../escape".into();
    assert!(
        files::Mover::new(&root, &t.path().join("state"))?
            .relocate(&mut j, "skipped", &Control::default())
            .is_err()
    );
    assert!(j.source.exists());
    assert_eq!(fs::read_dir(outside)?.count(), 0);
    Ok(())
}
#[test]
fn completed_originals_cannot_use_skipped_move() -> Result<()> {
    let t = tempfile::tempdir()?;
    let mut j = job(t.path())?;
    j.status = Status::Completed;
    assert!(
        files::Mover::new(&t.path().join("output"), &t.path().join("state"))?
            .relocate(&mut j, "skipped", &Control::default())
            .is_err()
    );
    assert!(j.source.exists());
    Ok(())
}
#[test]
fn cancellation_keeps_source() -> Result<()> {
    let t = tempfile::tempdir()?;
    let mut j = job(t.path())?;
    let c = Control::default();
    c.stop();
    assert!(
        files::Mover::new(&t.path().join("output"), &t.path().join("state"))?
            .relocate(&mut j, "skipped", &c)
            .is_err()
    );
    assert!(j.source.exists());
    Ok(())
}
#[test]
fn cross_filesystem_copy_is_verified() -> Result<()> {
    use std::os::unix::fs::MetadataExt;
    let t = tempfile::tempdir()?;
    let mut j = job(t.path())?;
    let other = tempfile::Builder::new()
        .prefix(".petal-rust-test-")
        .tempdir_in(std::env::current_dir()?)?;
    let root = other.path().join("output");
    files::Mover::new(&root, &t.path().join("state"))?.relocate(
        &mut j,
        "skipped",
        &Control::default(),
    )?;
    assert!(!j.source.exists());
    assert_eq!(
        fs::read(j.relocated_to.as_ref().unwrap())?,
        vec![17_u8; 100000]
    );
    let _different = t.path().metadata()?.dev() != other.path().metadata()?.dev();
    Ok(())
}
#[test]
fn no_clobber_output_keeps_temporary() -> Result<()> {
    let t = tempfile::tempdir()?;
    let a = t.path().join("tmp");
    let b = t.path().join("final");
    fs::write(&a, b"new")?;
    fs::write(&b, b"existing")?;
    assert!(files::keep_output(&a, &b).is_err());
    assert_eq!(fs::read(&b)?, b"existing");
    assert_eq!(fs::read(&a)?, b"new");
    Ok(())
}
#[test]
fn nested_roots_and_symlink_sources_are_rejected() -> Result<()> {
    use std::os::unix::fs::symlink;
    let t = tempfile::tempdir()?;
    assert!(files::validate_roots(t.path(), &t.path().join("nested")).is_err());
    let file = t.path().join("original");
    fs::write(&file, b"data")?;
    let link = t.path().join("link");
    symlink(file, &link)?;
    assert!(files::fingerprint(&link).is_err());
    Ok(())
}
#[test]
fn legacy_fingerprint_format_compatible() -> Result<()> {
    use sha2::{Digest, Sha256};
    use std::os::unix::fs::MetadataExt;
    let t = tempfile::tempdir()?;
    let file = t.path().join("one");
    fs::write(&file, b"hello")?;
    let info = fs::metadata(&file)?;
    let nanos = info.mtime() as i128 * 1_000_000_000 + info.mtime_nsec() as i128;
    let mut legacy = Sha256::new();
    legacy.update(format!(
        "{}:{}:{}:{nanos}",
        info.dev(),
        info.ino(),
        info.len()
    ));
    legacy.update(b"hello");
    assert_eq!(
        files::fingerprint(&file)?,
        format!("{:x}", legacy.finalize())
    );
    Ok(())
}
#[test]
fn scan_cache_names_and_existing_proofs() -> Result<()> {
    let t = tempfile::tempdir()?;
    let root = t.path().join("source");
    fs::create_dir(&root)?;
    fixture(&root.join("one.mkv"), 160, 90)?;
    fs::copy(root.join("one.mkv"), root.join("one.mp4"))?;
    let out = t.path().join("out");
    let s = Settings::default();
    let a = engine::scan(
        std::slice::from_ref(&root),
        &out,
        &t.path().join("state"),
        &s,
        &[],
        &BTreeMap::new(),
        &Runner::default(),
        &events(),
    )?
    .0;
    assert_eq!(a.len(), 2);
    assert_ne!(a[0].destination, a[1].destination);
    let b = engine::scan(
        &[root],
        &out,
        &t.path().join("state"),
        &s,
        &a,
        &BTreeMap::new(),
        &Runner::default(),
        &events(),
    )?
    .0;
    assert_eq!(a[0].destination, b[0].destination);
    Ok(())
}
#[test]
fn safe_dimensions_profiles_and_portrait() -> Result<()> {
    for (w, h) in [
        (7680, 4320),
        (4320, 7680),
        (4096, 4096),
        (640, 360),
        (853, 479),
    ] {
        let m = Media {
            streams: vec![
                serde_json::json!({"codec_type":"video","width":w,"height":h,"pix_fmt":"yuv420p"}),
            ],
            ..Default::default()
        };
        for resolution in ["480p", "720p", "1080p", "1440p", "2160p", "original"] {
            let s = Settings {
                resolution: resolution.into(),
                ..Default::default()
            };
            let (a, b) = media::target(&m, &s)?;
            assert!(a <= w && b <= h && a % 2 == 0 && b % 2 == 0);
        }
    }
    assert_eq!(Settings::default().quality((1920, 1080)), 105);
    assert_eq!(
        Settings {
            codec: "hevc_vaapi".into(),
            ..Default::default()
        }
        .quality((1920, 1080)),
        24
    );
    Ok(())
}
#[test]
fn unsupported_hdr_and_track_policy() -> Result<()> {
    let mut m = Media {
        streams: vec![
            serde_json::json!({"index":0,"codec_type":"video","width":1920,"height":1080,"pix_fmt":"yuv420p","color_transfer":"smpte2084"}),
        ],
        ..Default::default()
    };
    assert!(media::plan(&m, &Settings::default()).is_err());
    m.streams[0]["color_transfer"] = "bt709".into();
    m.streams
        .push(serde_json::json!({"index":1,"codec_type":"subtitle","codec_name":"mov_text"}));
    let p = media::plan(&m, &Settings::default())?;
    assert_eq!(p.subtitles.get(&1).unwrap(), "ass");
    assert!(
        media::plan(
            &m,
            &Settings {
                incompatible: "skip".into(),
                ..Default::default()
            }
        )
        .is_err()
    );
    Ok(())
}
fn cpu_adapter(a: Vec<OsString>, codec: &str) -> Vec<OsString> {
    let mut out = Vec::new();
    let mut i = 0;
    while i < a.len() {
        let key = a[i].to_string_lossy();
        if key == "-init_hw_device" || key == "-filter_hw_device" {
            i += 2;
            continue;
        }
        if key == "-c:v:0" {
            if codec == "av1_vaapi" {
                out.extend(args(&[
                    "-c:v:0",
                    "libaom-av1",
                    "-cpu-used",
                    "8",
                    "-crf",
                    "32",
                    "-b:v",
                    "0",
                    "-threads",
                    "2",
                ]));
            } else {
                out.extend(args(&[
                    "-c:v:0",
                    "libx265",
                    "-preset",
                    "ultrafast",
                    "-crf",
                    "24",
                    "-x265-params",
                    "pools=1:frame-threads=1:log-level=error",
                ]));
            }
            i += 6;
            continue;
        }
        out.push(OsString::from(
            key.replace("format=nv12,hwupload", "format=yuv420p")
                .replace("format=p010le,hwupload", "format=yuv420p10le"),
        ));
        i += 1;
    }
    out
}
#[test]
fn real_encode_verify_both_formats_and_truncation() -> Result<()> {
    let t = tempfile::tempdir()?;
    let source = t.path().join("source.mkv");
    fixture(&source, 320, 180)?;
    let r = Runner::default();
    let m = media::probe(&source, &r)?;
    for codec in ["av1_vaapi", "hevc_vaapi"] {
        let s = Settings {
            codec: codec.into(),
            ..Default::default()
        };
        let out = t.path().join(format!("{codec}.mkv"));
        let a = cpu_adapter(
            media::encode(&m, &out, &s, false, &BTreeMap::new(), None)?,
            codec,
        );
        r.run("ffmpeg", &a, Some(Duration::from_secs(30)))?;
        media::verify(&m, &out, &s, &r, false, None)?;
        let bytes = fs::read(&out)?;
        fs::write(&out, &bytes[..bytes.len() / 2])?;
        assert!(media::verify(&m, &out, &s, &r, false, None).is_err());
    }
    Ok(())
}
#[test]
fn decoded_geometry_mismatch_is_rejected() -> Result<()> {
    let t = tempfile::tempdir()?;
    let source = t.path().join("source.mkv");
    fixture(&source, 320, 180)?;
    let m = media::probe(&source, &Runner::default())?;
    let s = Settings {
        resolution: "480p".into(),
        ..Default::default()
    };
    let out = t.path().join("small.mkv");
    let mut a = cpu_adapter(
        media::encode(&m, &out, &s, false, &BTreeMap::new(), None)?,
        "av1_vaapi",
    );
    for v in &mut a {
        *v = OsString::from(v.to_string_lossy().replace("scale=320:180", "scale=160:90"));
    }
    Runner::default().run("ffmpeg", &a, None)?;
    assert!(media::verify(&m, &out, &s, &Runner::default(), false, None).is_err());
    Ok(())
}
#[test]
fn real_vmaf_identity_when_available() -> Result<()> {
    let r = Runner::default();
    if !r
        .run(
            "ffmpeg",
            &args(&["-hide_banner", "-h", "filter=libvmaf"]),
            Some(Duration::from_secs(10)),
        )?
        .stdout
        .contains("libvmaf AVOptions")
    {
        return Ok(());
    }
    let t = tempfile::tempdir()?;
    let p = t.path().join("source.mkv");
    let mut a = args(&[
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=160x90:rate=12",
        "-t",
        "3",
        "-c:v",
        "ffv1",
    ]);
    a.push(p.clone().into());
    r.run("ffmpeg", &a, None)?;
    let q = media::quality(&media::probe(&p, &r)?, &p, &r)?;
    assert!(q["mean"].as_f64().unwrap() > 95.);
    Ok(())
}
#[test]
fn output_lock_excludes_second_writer() -> Result<()> {
    let t = tempfile::tempdir()?;
    let _first = files::OutputLock::acquire(t.path())?;
    assert!(files::OutputLock::acquire(t.path()).is_err());
    Ok(())
}
#[test]
fn trash_requires_proof_and_never_touches_compressed_copy() -> Result<()> {
    let t = tempfile::tempdir()?;
    let mut j = job(t.path())?;
    j.status = Status::Completed;
    assert!(files::trash_original(&mut j, &Runner::default()).is_err());
    assert!(j.source.exists());
    let data = t.path().join("data");
    fs::create_dir_all(&data)?;
    let out = std::process::Command::new(env!("CARGO_BIN_EXE_petal"))
        .arg("self-test")
        .env("PETAL_TEST_TRASH", "1")
        .env("HOME", t.path())
        .env("XDG_DATA_HOME", &data)
        .env("XDG_STATE_HOME", t.path().join("state"))
        .output()?;
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(j.source.exists());
    Ok(())
}
#[test]
fn library_roundtrip_550_rows_preserves_reports() -> Result<()> {
    let t = tempfile::tempdir()?;
    let mut s = Library::default();
    for i in 0..550 {
        s.jobs.push(Job {
            source: t.path().join(format!("{i}.mp4")),
            relative: format!("{i}.mp4").into(),
            destination: t.path().join(format!("{i}.mkv")),
            status: Status::Completed,
            size: 100,
            final_size: 40,
            ..Default::default()
        });
    }
    s.report = s.jobs.clone();
    s.save(t.path())?;
    let loaded = Library::load(t.path())?;
    assert_eq!(loaded.jobs.len(), 550);
    assert_eq!(summary(&loaded.report)["saved"], 33000);
    Ok(())
}
#[test]
fn hardware_requires_a_real_device() -> Result<()> {
    let t = tempfile::tempdir()?;
    let s = Settings {
        device: t.path().join("missing").display().to_string(),
        ..Default::default()
    };
    assert!(engine::doctor(&s, t.path(), &Runner::default(), &events(), true).is_err());
    Ok(())
}

#[test]
fn real_audio_subtitle_timecode_and_color_survive() -> Result<()> {
    let t = tempfile::tempdir()?;
    let source = t.path().join("tracks.mp4");
    let subtitle = t.path().join("captions.srt");
    fs::write(&subtitle, "1\n00:00:00,200 --> 00:00:01,600\nHello world\n")?;
    let mut a = args(&[
        "-hide_banner",
        "-nostdin",
        "-v",
        "error",
        "-n",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x180:rate=12",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000",
        "-i",
    ]);
    a.push(subtitle.into());
    a.extend(args(&[
        "-t",
        "2",
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-map",
        "2:s",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-c:a",
        "aac",
        "-ac",
        "2",
        "-c:s",
        "mov_text",
        "-vf",
        "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-colorspace",
        "bt709",
        "-color_range",
        "tv",
        "-metadata",
        "title=Example",
        "-metadata",
        "artist=Example Artist",
        "-metadata:s:a:0",
        "language=eng",
        "-timecode",
        "01:02:03:04",
    ]));
    a.push(source.clone().into());
    let r = Runner::default();
    r.run("ffmpeg", &a, Some(Duration::from_secs(30)))?;
    let m = media::probe(&source, &r)?;
    for codec in ["av1_vaapi", "hevc_vaapi"] {
        let s = Settings {
            codec: codec.into(),
            ..Default::default()
        };
        let out = t.path().join(format!("tracks-{codec}.mkv"));
        r.run(
            "ffmpeg",
            &cpu_adapter(
                media::encode(&m, &out, &s, false, &BTreeMap::new(), None)?,
                codec,
            ),
            Some(Duration::from_secs(30)),
        )?;
        media::verify(&m, &out, &s, &r, false, None)?;
    }
    Ok(())
}
#[test]
fn trash_rejects_changed_middle_bytes_even_with_matching_fast_fingerprint() -> Result<()> {
    use sha2::{Digest, Sha256};
    use std::io::{Seek, SeekFrom, Write};
    let t = tempfile::tempdir()?;
    let original = t.path().join("original.mp4");
    let compressed = t.path().join("compressed.mkv");
    fs::write(&original, vec![3_u8; 3 * 1024 * 1024])?;
    fs::write(&compressed, vec![4_u8; 3 * 1024 * 1024])?;
    let metadata = compressed.metadata()?;
    let mut job = Job {
        source: original.clone(),
        destination: compressed.clone(),
        status: Status::Completed,
        proof: Some(Proof {
            source_key: files::fingerprint(&original)?,
            output_key: files::fingerprint(&compressed)?,
            source_sha: format!("{:x}", Sha256::digest(fs::read(&original)?)),
            output_sha: format!("{:x}", Sha256::digest(fs::read(&compressed)?)),
            full: true,
            ..Default::default()
        }),
        ..Default::default()
    };
    let mut f = fs::OpenOptions::new().write(true).open(&compressed)?;
    f.seek(SeekFrom::Start(1536 * 1024))?;
    f.write_all(b"changed middle")?;
    f.set_times(fs::FileTimes::new().set_modified(metadata.modified()?))?;
    drop(f);
    assert_eq!(
        files::fingerprint(&compressed)?,
        job.proof.as_ref().unwrap().output_key
    );
    assert!(files::trash_original(&mut job, &Runner::default()).is_err());
    assert!(original.exists());
    assert!(compressed.exists());
    Ok(())
}
#[test]
fn interrupted_atomic_move_is_reconciled_without_touching_bytes() -> Result<()> {
    use sha2::{Digest, Sha256};
    let t = tempfile::tempdir()?;
    let mut j = job(t.path())?;
    let root = t.path().join("output");
    let dst = root.join("skipped").join(&j.relative);
    let state = t.path().join("state");
    let identity = files::identity(&j.source.metadata()?);
    let hold = j.source.parent().unwrap().join(format!(
        ".petal-{}.move-source",
        uuid::Uuid::new_v4().simple()
    ));
    let temporary = root.join(".petal-00000000000000000000000000000000.move-copy");
    let key = format!("{:x}", Sha256::digest(root.to_string_lossy().as_bytes()));
    let journal = state.join(format!("native-moves-{}.json", &key[..24]));
    files::atomic_json(
        &journal,
        &serde_json::json!({j.source.to_string_lossy().to_string():{"source":j.source,"destination":dst,"hold":hold,"temporary":temporary,"original":identity,"sha":"","phase":"prepared","same_device":true}}),
    )?;
    files::rename_new(&j.source, &dst)?;
    let mut mover = files::Mover::new(&root, &state)?;
    mover.recover(&Control::default())?;
    mover.relocate(&mut j, "skipped", &Control::default())?;
    assert_eq!(j.relocated_to, Some(dst.clone()));
    assert_eq!(fs::read(dst)?, vec![17_u8; 100000]);
    Ok(())
}
#[test]
fn cancellation_kills_the_whole_media_process_group() -> Result<()> {
    let runner = Runner::default();
    let control = runner.control.clone();
    let began = std::time::Instant::now();
    let child = std::thread::spawn(move || runner.run("sh", &args(&["-c", "sleep 30"]), None));
    std::thread::sleep(Duration::from_millis(100));
    control.stop();
    assert!(child.join().unwrap().is_err());
    assert!(began.elapsed() < Duration::from_secs(3));
    Ok(())
}

#[test]
fn interrupted_cross_copy_restores_source_then_retries_verified_copy() -> Result<()> {
    use sha2::{Digest, Sha256};
    let t = tempfile::tempdir()?;
    let mut j = job(t.path())?;
    let root = t.path().join("output");
    let dst = root.join("skipped").join(&j.relative);
    fs::create_dir_all(dst.parent().unwrap())?;
    fs::copy(&j.source, &dst)?;
    let state = t.path().join("state");
    let original = files::identity(&j.source.metadata()?);
    let sha = files::hash_file(&j.source, &Control::default())?;
    let hold = j.source.parent().unwrap().join(format!(
        ".petal-{}.move-source",
        uuid::Uuid::new_v4().simple()
    ));
    let key = format!("{:x}", Sha256::digest(root.to_string_lossy().as_bytes()));
    let journal = state.join(format!("native-moves-{}.json", &key[..24]));
    files::atomic_json(
        &journal,
        &serde_json::json!({j.source.to_string_lossy().to_string(): {
            "source":j.source,"destination":dst,"hold":hold,
            "temporary":root.join(".petal-recovery.move-copy"),"original":original,
            "sha":sha,"phase":"held","same_device":false
        }}),
    )?;
    files::rename_new(&j.source, &hold)?;
    let mut mover = files::Mover::new(&root, &state)?;
    mover.recover(&Control::default())?;
    assert!(j.source.exists());
    assert!(!hold.exists());
    assert_eq!(files::hash_file(&j.source, &Control::default())?, sha);
    mover.relocate(&mut j, "skipped", &Control::default())?;
    assert!(!j.source.exists());
    assert_eq!(files::hash_file(&dst, &Control::default())?, sha);
    Ok(())
}

#[test]
fn recovery_collision_preserves_new_file_and_held_original() -> Result<()> {
    use sha2::{Digest, Sha256};
    let t = tempfile::tempdir()?;
    let j = job(t.path())?;
    let root = t.path().join("output");
    let state = t.path().join("state");
    let dst = root.join("skipped").join(&j.relative);
    let original = files::identity(&j.source.metadata()?);
    let hold = j.source.parent().unwrap().join(".petal-test.move-source");
    let key = format!("{:x}", Sha256::digest(root.to_string_lossy().as_bytes()));
    files::atomic_json(
        &state.join(format!("native-moves-{}.json", &key[..24])),
        &serde_json::json!({j.source.to_string_lossy().to_string(): {
            "source":j.source,"destination":dst,"hold":hold,
            "temporary":root.join(".petal-test.move-copy"),"original":original,
            "sha":"","phase":"held","same_device":false
        }}),
    )?;
    files::rename_new(&j.source, &hold)?;
    fs::write(&j.source, b"new file")?;
    assert!(
        files::Mover::new(&root, &state)?
            .recover(&Control::default())
            .is_err()
    );
    assert_eq!(fs::read(&j.source)?, b"new file");
    assert_eq!(fs::read(&hold)?, vec![17_u8; 100000]);
    Ok(())
}

#[test]
fn interrupted_incomplete_copy_is_cleaned_without_touching_original_or_unknown_files() -> Result<()>
{
    use sha2::{Digest, Sha256};
    let t = tempfile::tempdir()?;
    let j = job(t.path())?;
    let root = t.path().join("output");
    let state = t.path().join("state");
    let dst = root.join("skipped").join(&j.relative);
    fs::create_dir_all(dst.parent().unwrap())?;
    let temporary = dst.parent().unwrap().join(".petal-registered.move-copy");
    let unknown = dst.parent().unwrap().join(".petal-unknown.move-copy");
    fs::write(&temporary, b"incomplete copy")?;
    fs::write(&unknown, b"not ours")?;
    let key = format!("{:x}", Sha256::digest(root.to_string_lossy().as_bytes()));
    let source_key = files::fingerprint(&j.source)?;
    files::atomic_json(
        &state.join(format!("native-moves-{}.json", &key[..24])),
        &serde_json::json!({j.source.to_string_lossy().to_string(): {
            "source":j.source,"destination":dst,
            "hold":j.source.parent().unwrap().join(".petal-test.move-source"),
            "temporary":temporary,"original":files::identity(&j.source.metadata()?),
            "sha":"","phase":"prepared","same_device":false
        }}),
    )?;
    files::Mover::new(&root, &state)?.recover(&Control::default())?;
    assert!(!temporary.exists());
    assert_eq!(fs::read(unknown)?, b"not ours");
    assert_eq!(files::fingerprint(&j.source)?, source_key);
    assert_eq!(fs::read(&j.source)?, vec![17_u8; 100000]);
    assert!(!dst.exists());
    Ok(())
}
