//! Bounded background work. One hardware encoder overlaps one verifier; originals stay untouched.
use crate::{
    files, media,
    model::*,
    process::{Runner, args},
};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
    sync::mpsc,
    thread,
    time::{Duration, Instant},
};
fn proof_path(state: &Path, source: &Path, output: &Path) -> PathBuf {
    let key = format!(
        "{:x}",
        Sha256::digest(format!("{}\0{}", source.display(), output.display()).as_bytes())
    );
    state.join("native-proofs").join(format!("{key}.json"))
}
fn save_job(job: &Job, state: &Path) -> Result<()> {
    files::atomic_json(&proof_path(state, &job.source, &job.destination), job)
}
pub fn restore(
    job: &mut Job,
    state: &Path,
    s: &Settings,
    r: &Runner,
    upgrade: bool,
) -> Result<bool> {
    let path = proof_path(state, &job.source, &job.destination);
    if path.exists() {
        let saved: Job = serde_json::from_slice(&fs::read(path)?)?;
        if let Some(p) = &saved.proof
            && files::fingerprint(&job.source)? == p.source_key
            && files::fingerprint(&job.destination)? == p.output_key
        {
            let source = job.source.clone();
            let root = job.output_root.clone();
            *job = saved;
            job.source = source;
            job.output_root = root;
            job.message = "Previously checked compressed copy".into();
            return Ok(true);
        }
        return Ok(false);
    }
    // Python-era verification records remain readable. UI history alone is never a proof.
    for file in fs::read_dir(state).into_iter().flatten().flatten() {
        let name = file.file_name().to_string_lossy().into_owned();
        if !name.starts_with("queue-") || !name.ends_with(".json") {
            continue;
        }
        let data: Value = serde_json::from_slice(&fs::read(file.path())?)?;
        let Some(record) = data["jobs"].get(job.relative.to_string_lossy().as_ref()) else {
            continue;
        };
        if record["status"] != "Completed"
            || record["destination"].as_str() != job.destination.to_str()
        {
            continue;
        }
        if files::fingerprint(&job.source)? != record["source"].as_str().unwrap_or("")
            || files::fingerprint(&job.destination)? != record["output"].as_str().unwrap_or("")
        {
            continue;
        }
        job.status = Status::Completed;
        job.source_key = record["source"].as_str().unwrap_or("").into();
        job.final_size = fs::metadata(&job.destination)?.len();
        job.quality = record["quality"].clone();
        job.message = "Previously checked compressed copy".into();
        job.output_dimensions = serde_json::from_value(record["dimensions"].clone()).ok();
        let mut settings = s.clone();
        if let Some(codec) = record["codec"].as_str() {
            settings.codec = codec.into()
        }
        if let Some(profile) = record["profile"].as_str() {
            settings.profile = profile.into()
        }
        if let Some(res) = record["resolution"].as_str() {
            settings.resolution = res.into()
        }
        if upgrade {
            let m = media::probe(&job.source, r)?;
            settings.strict = true;
            media::verify(&m, &job.destination, &settings, r, false, None)?;
            job.proof = Some(Proof {
                source_key: files::fingerprint(&job.source)?,
                output_key: files::fingerprint(&job.destination)?,
                source_sha: files::hash_file(&job.source, &r.control)?,
                output_sha: files::hash_file(&job.destination, &r.control)?,
                settings,
                full: true,
            });
            job.media = Some(m);
            save_job(job, state)?;
        }
        return Ok(true);
    }
    Ok(false)
}
#[derive(serde::Serialize, serde::Deserialize)]
struct ProbeCache {
    tool: String,
    entries: BTreeMap<String, (String, Media)>,
}
fn scan_identity(path: &Path) -> Result<String> {
    use std::os::unix::fs::MetadataExt;
    let m = fs::symlink_metadata(path)?;
    Ok(format!(
        "{}:{}:{}:{}:{}:{}:{}",
        m.dev(),
        m.ino(),
        m.len(),
        m.mtime(),
        m.mtime_nsec(),
        m.ctime(),
        m.ctime_nsec()
    ))
}
#[allow(clippy::too_many_arguments)] // Explicit roots, proof state and UI event channel are separate safety boundaries.
pub fn scan(
    inputs: &[PathBuf],
    output: &Path,
    state: &Path,
    s: &Settings,
    old: &[Job],
    namespaces: &BTreeMap<String, String>,
    r: &Runner,
    ev: &Events,
) -> Result<(Vec<Job>, BTreeMap<String, String>)> {
    ensure!(!inputs.is_empty(), "Choose a source folder or add videos");
    let output = files::absolute(output)?;
    let _lock = files::OutputLock::acquire(&output)?;
    files::Mover::new(&output, state)?.recover(&r.control)?;
    files::recover_legacy_moves(&output, state, &r.control)?;
    let mut paths: BTreeMap<PathBuf, (PathBuf, PathBuf, bool)> = BTreeMap::new();
    let mut names = namespaces.clone();
    for input in inputs {
        r.control.check()?;
        ensure!(
            !fs::symlink_metadata(input)?.file_type().is_symlink(),
            "Symbolic-link inputs are left alone"
        );
        let input = fs::canonicalize(input)?;
        if input.is_dir() {
            files::validate_roots(&input, &output)?;
        } else {
            ensure!(
                !input.starts_with(&output),
                "Source file is inside the output folder"
            )
        };
        let root = if input.is_dir() {
            input.clone()
        } else {
            input.parent().context("Missing parent")?.into()
        };
        let key = root.to_string_lossy().to_string();
        let prefix = if inputs.len() == 1 && input.is_dir() {
            names.entry(key).or_default().clone()
        } else {
            let used = names
                .values()
                .map(|s| s.to_lowercase())
                .collect::<BTreeSet<_>>();
            let mut label = root
                .file_name()
                .unwrap_or_default()
                .to_string_lossy()
                .into_owned();
            if used.contains(&label.to_lowercase()) {
                label = format!("{label}-{:x}", Sha256::digest(key.as_bytes()))[..label.len() + 9]
                    .into()
            }
            names.entry(key).or_insert(label).clone()
        };
        ensure!(
            !Path::new(&prefix).is_absolute()
                && !Path::new(&prefix)
                    .components()
                    .any(|c| matches!(c, std::path::Component::ParentDir)),
            "Unsafe source-folder name"
        );
        let mut add = |path: PathBuf| {
            if path.to_str().is_none() {
                ev.log("A filename could not be represented safely; it was left untouched");
                return;
            }
            if path
                .file_name()
                .unwrap_or_default()
                .to_string_lossy()
                .starts_with(".petal-")
            {
                return;
            }
            let relative = PathBuf::from(&prefix).join(path.strip_prefix(&root).unwrap());
            paths
                .entry(path)
                .or_insert((relative, root.clone(), input.is_file()));
        };
        if input.is_file() {
            add(input.clone());
        } else {
            for entry in walkdir::WalkDir::new(&input)
                .follow_links(false)
                .sort_by_file_name()
            {
                r.control.check()?;
                let entry = entry?;
                if entry.file_type().is_file() {
                    add(entry.path().into());
                }
            }
        }
    }
    let executable = std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default())
        .map(|p| p.join("ffprobe"))
        .find(|p| p.is_file())
        .context("FFprobe is not installed")?;
    let tool = format!("{}:{}", executable.display(), scan_identity(&executable)?);
    let cache_path = if state == files::xdg("state") {
        files::xdg("cache").join("native-probes.json")
    } else {
        state.join("native-probes.json")
    };
    let mut cache: ProbeCache = fs::read(&cache_path)
        .ok()
        .and_then(|v| serde_json::from_slice(&v).ok())
        .filter(|p: &ProbeCache| p.tool == tool)
        .unwrap_or(ProbeCache {
            tool,
            entries: BTreeMap::new(),
        });
    let candidates = paths.into_iter().collect::<Vec<_>>();
    let mut infos = Vec::new();
    let mut misses = Vec::new();
    for (path, (relative, root, explicit)) in &candidates {
        let id = scan_identity(path)?;
        if let Some((key, m)) = cache.entries.get(&path.to_string_lossy().to_string())
            && *key == id
        {
            infos.push((
                path.clone(),
                relative.clone(),
                root.clone(),
                *explicit,
                Ok(m.clone()),
            ));
            continue;
        }
        misses.push((path.clone(), relative.clone(), root.clone(), *explicit, id));
    }
    let (tx, rx) = mpsc::channel();
    thread::scope(|scope| {
        for slice in misses.chunks(misses.len().div_ceil(2).max(1)) {
            let tx = tx.clone();
            let r = r.clone();
            scope.spawn(move || {
                for (path, rel, root, explicit, id) in slice {
                    if r.control.check().is_err() {
                        break;
                    }
                    let value = media::probe(path, &r).map_err(|e| e.to_string());
                    let _ = tx.send((
                        path.clone(),
                        rel.clone(),
                        root.clone(),
                        *explicit,
                        id.clone(),
                        value,
                    ));
                }
            });
        }
        drop(tx);
        for (path, rel, root, explicit, id, result) in rx {
            if let Ok(m) = &result
                && scan_identity(&path).ok().as_ref() == Some(&id)
            {
                cache
                    .entries
                    .insert(path.to_string_lossy().into(), (id, m.clone()));
            }
            infos.push((
                path,
                rel,
                root,
                explicit,
                result.map_err(anyhow::Error::msg),
            ));
        }
    });
    r.control.check()?;
    if !misses.is_empty()
        && let Err(e) = files::atomic_json(&cache_path, &cache)
    {
        ev.log(format!("Scan cache unavailable: {e}"));
    }
    infos.sort_by(|a, b| a.0.cmp(&b.0));
    let mut jobs = Vec::new();
    for (path, relative, root, explicit, result) in infos {
        let mut j = Job {
            source: path.clone(),
            relative: relative.clone(),
            destination: output.join(relative.with_extension("mkv")),
            input_root: Some(root),
            explicit_file: explicit,
            size: fs::metadata(&path)?.len(),
            output_root: Some(output.clone()),
            ..Job::default()
        };
        match result {
            Ok(m) => {
                let format = m.format["format_name"].as_str().unwrap_or("");
                if format
                    .split(',')
                    .any(|s| s == "image2" || s.ends_with("_pipe"))
                {
                    continue;
                }
                j.media = Some(m);
            }
            Err(e) => {
                let extension = path
                    .extension()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .to_lowercase();
                if ![
                    "mp4", "mkv", "mov", "avi", "webm", "m4v", "ts", "mts", "m2ts", "mpg", "mpeg",
                    "vob", "wmv", "flv", "ogv", "3gp", "mxf", "nut", "m2v", "y4m", "divx", "asf",
                ]
                .contains(&extension.as_str())
                {
                    continue;
                }
                j.status = Status::Failed;
                j.message = format!("Could not read video: {e}");
            }
        }
        jobs.push(j);
    }
    let mut counts = BTreeMap::<String, usize>::new();
    for j in &jobs {
        *counts
            .entry(j.destination.to_string_lossy().to_lowercase())
            .or_default() += 1
    }
    let mut reserved = BTreeSet::new();
    for j in &mut jobs {
        if let Some(previous) = old.iter().find(|p| {
            p.source == j.source
                && p.output_root.as_ref() == Some(&output)
                && p.destination.parent() == j.destination.parent()
        }) {
            j.destination = previous.destination.clone();
        }
        if counts
            .get(&j.destination.to_string_lossy().to_lowercase())
            .copied()
            .unwrap_or(0)
            > 1
            || j.destination
                .file_name()
                .unwrap_or_default()
                .as_bytes()
                .len()
                > 240
            || reserved.contains(&j.destination.to_string_lossy().to_lowercase())
        {
            let key = format!("{:x}", Sha256::digest(j.source.as_os_str().as_bytes()));
            let stem = j.source.file_stem().unwrap_or_default().to_string_lossy();
            let mut end = stem.len().min(200);
            while !stem.is_char_boundary(end) {
                end -= 1
            }
            j.destination = j.destination.with_file_name(format!(
                "{}.{}-{}.mkv",
                &stem[..end],
                j.source.extension().unwrap_or_default().to_string_lossy(),
                &key[..12]
            ));
        }
        reserved.insert(j.destination.to_string_lossy().to_lowercase());
        if j.status != Status::Failed
            && j.destination.exists()
            && let Err(e) = restore(j, state, s, r, false)
        {
            ev.log(format!("Saved result not reused: {e}"));
        }
    }
    ev.log(format!(
        "Found {} videos; {} unchanged files used cached details",
        jobs.len(),
        candidates.len().saturating_sub(misses.len())
    ));
    Ok((jobs, names))
}
use std::os::unix::ffi::OsStrExt;
#[derive(serde::Serialize, serde::Deserialize, Default)]
struct Hardware {
    signature: String,
    passed: BTreeSet<String>,
}
fn hardware_signature(s: &Settings, r: &Runner) -> Result<String> {
    let ff = r
        .run(
            "ffmpeg",
            &args(&["-version"]),
            Some(Duration::from_secs(10)),
        )?
        .stdout;
    let device = fs::metadata(&s.device)
        .map(|m| format!("{:?}", files::identity(&m)))
        .unwrap_or_default();
    let driver = fs::metadata("/usr/lib/dri/radeonsi_drv_video.so")
        .map(|m| format!("{:?}", files::identity(&m)))
        .unwrap_or_default();
    Ok(format!(
        "{:x}",
        Sha256::digest(
            format!(
                "v1:{ff}:{device}:{driver}:{}:{}:{}:{}",
                s.device, s.codec, s.profile, s.resolution
            )
            .as_bytes()
        )
    ))
}
pub fn doctor(
    s: &Settings,
    state: &Path,
    r: &Runner,
    ev: &Events,
    force: bool,
) -> Result<BTreeSet<u32>> {
    s.validate()?;
    let signature = hardware_signature(s, r)?;
    let path = state.join("native-hardware.json");
    let mut cache: Hardware = fs::read(&path)
        .ok()
        .and_then(|v| serde_json::from_slice(&v).ok())
        .filter(|v: &Hardware| v.signature == signature)
        .unwrap_or(Hardware {
            signature,
            passed: BTreeSet::new(),
        });
    if !force && !cache.passed.is_empty() {
        return Ok(cache.passed.iter().filter_map(|b| b.parse().ok()).collect());
    }
    ensure!(
        Path::new(&s.device).exists(),
        "Graphics device is unavailable. Check Settings and the render-device permissions"
    );
    let root =
        std::env::temp_dir().join(format!("petal-hardware-{}", uuid::Uuid::new_v4().simple()));
    fs::create_dir(&root)?;
    let result = (|| {
        let mut passed = BTreeSet::new();
        for bits in [8, 10] {
            ev.log(format!("Checking {} / {} / {bits}-bit", s.codec, s.profile));
            let fixture = root.join(format!("source-{bits}.mkv"));
            let output = root.join(format!("output-{bits}.mkv"));
            let canvas = match s.resolution.as_str() {
                "480p" => "854x480",
                "720p" => "1280x720",
                "1440p" => "2560x1440",
                "2160p" => "3840x2160",
                _ => "1920x1080",
            };
            let pattern = format!("testsrc2=size={canvas}:rate=24");
            let mut a = args(&[
                "-hide_banner",
                "-nostdin",
                "-v",
                "error",
                "-n",
                "-f",
                "lavfi",
                "-i",
                &pattern,
                "-t",
                "0.5",
                "-pix_fmt",
                if bits == 10 { "yuv420p10le" } else { "yuv420p" },
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "18",
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
            ]);
            a.push(fixture.clone().into());
            let attempt = (|| -> Result<()> {
                r.run("ffmpeg", &a, Some(Duration::from_secs(60)))?;
                let m = media::probe(&fixture, r)?;
                r.run(
                    "ffmpeg",
                    &media::encode(&m, &output, s, false, &BTreeMap::new(), None)?,
                    Some(Duration::from_secs(90)),
                )?;
                let mut thorough = s.clone();
                thorough.strict = true;
                media::verify(&m, &output, &thorough, r, false, None)?;
                Ok(())
            })();
            match attempt {
                Ok(()) => {
                    passed.insert(bits);
                }
                Err(e) => ev.log(format!("{bits}-bit check: {e}")),
            }
        }
        ensure!(
            !passed.is_empty(),
            "This compression setting did not pass its hardware check. Try H.265 or inspect the log"
        );
        cache.passed = passed.iter().map(u32::to_string).collect();
        files::atomic_json(&path, &cache)?;
        Ok(passed)
    })();
    let _ = fs::remove_dir_all(root);
    result
}
struct Cleanup {
    paths: Vec<PathBuf>,
    journal: PathBuf,
    root: PathBuf,
}
impl Cleanup {
    fn new(root: &Path, state: &Path) -> Result<Self> {
        let journal = state.join(format!(
            "native-active-{}.json",
            uuid::Uuid::new_v4().simple()
        ));
        Ok(Self {
            paths: Vec::new(),
            journal,
            root: root.into(),
        })
    }
    fn track(&mut self, p: PathBuf) -> Result<()> {
        self.paths.push(p);
        files::atomic_json(&self.journal, &json!({"root":self.root,"paths":self.paths}))
    }
}
impl Drop for Cleanup {
    fn drop(&mut self) {
        let mut clean = true;
        for p in &self.paths {
            if p.exists() && fs::remove_file(p).is_err() {
                clean = false
            }
        }
        if clean {
            let _ = fs::remove_file(&self.journal);
        }
    }
}
pub fn recover_outputs(root: &Path, state: &Path) -> Result<()> {
    if !state.exists() {
        return Ok(());
    }
    for item in fs::read_dir(state)? {
        let item = item?;
        if !item
            .file_name()
            .to_string_lossy()
            .starts_with("native-active-")
        {
            continue;
        }
        let v: Value = serde_json::from_slice(&fs::read(item.path())?)?;
        if v["root"].as_str() != root.to_str() {
            continue;
        }
        for raw in v["paths"]
            .as_array()
            .context("Unreadable cleanup journal")?
        {
            let p = PathBuf::from(raw.as_str().context("Invalid temporary path")?);
            ensure!(
                p.starts_with(root)
                    && p.file_name()
                        .unwrap_or_default()
                        .to_string_lossy()
                        .starts_with(".petal-")
                    && p.file_name()
                        .unwrap()
                        .to_string_lossy()
                        .contains(".partial"),
                "Unsafe cleanup record"
            );
            let _parent = files::parent(&p, false)?;
            if fs::symlink_metadata(&p).is_ok() {
                ensure!(
                    fs::symlink_metadata(&p)?.is_file(),
                    "Temporary file changed; retained"
                );
                fs::remove_file(p)?;
            }
        }
        fs::remove_file(item.path())?;
    }
    Ok(())
}
fn prepare_art(
    m: &Media,
    s: &Settings,
    dir: &Path,
    r: &Runner,
    cleanup: &mut Cleanup,
) -> Result<BTreeMap<u64, PathBuf>> {
    let mut art = BTreeMap::new();
    for cover in media::plan(m, s)?.artwork {
        let id = cover["index"].as_u64().unwrap();
        let ext = if cover["codec_name"] == "mjpeg" {
            "jpg"
        } else {
            "png"
        };
        let path = dir.join(format!(
            ".petal-{}.partial.{ext}",
            uuid::Uuid::new_v4().simple()
        ));
        cleanup.track(path.clone())?;
        let mut a = args(&[
            "-hide_banner",
            "-nostdin",
            "-v",
            "error",
            "-n",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
        ]);
        a.push(m.path.clone().into());
        a.extend(args(&[
            "-map",
            &format!("0:{id}"),
            "-c:v",
            "copy",
            "-frames:v",
            "1",
            "-f",
            "image2",
        ]));
        a.push(path.clone().into());
        r.run("ffmpeg", &a, Some(Duration::from_secs(30)))?;
        art.insert(id, path);
    }
    Ok(art)
}
fn finish(
    mut job: Job,
    tmp: PathBuf,
    s: Settings,
    mut r: Runner,
    state: PathBuf,
    ev: Events,
    _cleanup: Cleanup,
) -> Job {
    r.priority = 5;
    let began = Instant::now();
    let result = (|| -> Result<()> {
        job.status = Status::Verifying;
        job.message = "Checking the compressed copy".into();
        ev.send(Event::Job(Box::new(job.clone())));
        let m = job.media.as_ref().context("Missing original details")?;
        media::verify(m, &tmp, &s, &r, false, None)?;
        ensure!(
            files::fingerprint(&job.source)? == job.source_key,
            "Original changed during compression; compressed copy was not kept"
        );
        let source_sha = files::hash_file(&job.source, &r.control)?;
        let output_sha = files::hash_file(&tmp, &r.control)?;
        ensure!(
            files::fingerprint(&job.source)? == job.source_key,
            "Original changed before the compressed copy was saved"
        );
        r.control.check()?;
        files::keep_output(&tmp, &job.destination)?;
        job.final_size = fs::metadata(&job.destination)?.len();
        job.verify_seconds = Some(began.elapsed().as_secs_f64());
        job.proof = Some(Proof {
            source_key: job.source_key.clone(),
            output_key: files::fingerprint(&job.destination)?,
            source_sha,
            output_sha,
            settings: s.clone(),
            full: s.strict || m.duration().is_none(),
        });
        job.status = Status::Completed;
        job.progress = 1.;
        job.message = "Checked and saved safely".into();
        save_job(&job, &state)?;
        Ok(())
    })();
    if let Err(e) = result {
        job.status = if r.control.cancel.load(std::sync::atomic::Ordering::Relaxed) {
            Status::Cancelled
        } else {
            Status::Failed
        };
        job.message = e.to_string();
    }
    ev.send(Event::Job(Box::new(job.clone())));
    job
}
pub fn batch(
    jobs: Vec<Job>,
    root: &Path,
    state: &Path,
    s: &Settings,
    r: &Runner,
    ev: &Events,
) -> Result<()> {
    s.validate()?;
    let _lock = files::OutputLock::acquire(root)?;
    recover_outputs(root, state)?;
    if jobs
        .iter()
        .all(|j| j.status.finished() || j.trashed || j.relocated_to.is_some())
    {
        for j in jobs {
            ev.send(Event::Job(Box::new(j)));
        }
        return Ok(());
    }
    let mut effective = s.clone();
    let passed = match doctor(&effective, state, r, ev, false) {
        Ok(p) => p,
        Err(error) if effective.codec == "av1_vaapi" && r.control.check().is_ok() => {
            ev.log(format!(
                "AV1 did not pass its output checks: {error}. Checking H.265 instead."
            ));
            effective.codec = "hevc_vaapi".into();
            let p = doctor(&effective, state, r, ev, false)?;
            ev.send(Event::Applied(effective.clone()));
            ev.send(Event::Hardware(
                "Using checked H.265: AV1 did not pass the graphics-driver frame checks.".into(),
            ));
            p
        }
        Err(e) => return Err(e),
    };
    let s = &effective;
    let mut verifier: Option<thread::JoinHandle<Job>> = None;
    let (qtx, qrx) = mpsc::channel::<Job>();
    let mut qr = r.clone();
    qr.priority = 10;
    let qs = state.to_path_buf();
    let qe = ev.clone();
    let quality = thread::spawn(move || {
        for mut job in qrx {
            if qr.control.check().is_err() {
                break;
            }
            job.quality = match media::quality(job.media.as_ref().unwrap(), &job.destination, &qr) {
                Ok(q) => q,
                Err(e) => json!({"status":"Unavailable","reason":e.to_string()}),
            };
            if let Err(e) = save_job(&job, &qs) {
                qe.log(format!("Could not save picture check: {e}"))
            };
            qe.send(Event::Job(Box::new(job)));
        }
    });
    let mut count = 0;
    let mut drain = |handle: Option<thread::JoinHandle<Job>>| {
        if let Some(h) = handle
            && let Ok(j) = h.join()
            && s.auto_quality
            && count < 3
            && j.status == Status::Completed
            && j.media
                .as_ref()
                .and_then(Media::duration)
                .is_some_and(|d| d >= 2.)
        {
            count += 1;
            let _ = qtx.send(j);
        }
    };
    let mut failed_decoders = BTreeSet::new();
    for mut job in jobs {
        if r.control.check().is_err() {
            job.status = Status::Cancelled;
            job.message = "Stopped safely; original kept".into();
            ev.send(Event::Job(Box::new(job)));
            continue;
        }
        if r.control.wait().is_err() {
            job.status = Status::Cancelled;
            job.message = "Stopped safely; original kept".into();
            ev.send(Event::Job(Box::new(job)));
            continue;
        }
        if job.trashed || job.relocated_to.is_some() || job.status == Status::Skipped {
            continue;
        }
        let result = (|| -> Result<Option<(PathBuf, Cleanup)>> {
            job.source_key = files::fingerprint(&job.source)?;
            let m = media::probe(&job.source, r)?;
            job.size = fs::metadata(&job.source)?.len();
            job.media = Some(m.clone());
            let plan = match media::plan(&m, s) {
                Ok(p) => p,
                Err(e) => {
                    job.status = Status::Skipped;
                    job.message = e.to_string();
                    return Ok(None);
                }
            };
            if job.destination.exists() {
                if restore(&mut job, state, s, r, false)? {
                    return Ok(None);
                }
                job.status = Status::Skipped;
                job.message = "A different output already exists; both files kept".into();
                return Ok(None);
            }
            if !passed.contains(&m.bits()) {
                job.status = Status::Skipped;
                job.message = "This picture precision did not pass the graphics check".into();
                return Ok(None);
            }
            let _parent = files::parent(&job.destination, true)?;
            let tmp = job.destination.parent().unwrap().join(format!(
                ".petal-{}.partial.mkv",
                uuid::Uuid::new_v4().simple()
            ));
            let mut cleanup = Cleanup::new(root, state)?;
            cleanup.track(tmp.clone())?;
            let art = prepare_art(&m, s, tmp.parent().unwrap(), r, &mut cleanup)?;
            job.output_dimensions = Some(media::target(&m, s)?);
            job.encoder_quality = Some(s.quality(job.output_dimensions.unwrap()));
            job.encoder_profile = Some(s.profile.clone());
            job.encoder_codec = Some(s.codec.clone());
            job.status = Status::Encoding;
            job.message = plan.warnings.join("; ");
            ev.send(Event::Job(Box::new(job.clone())));
            let began = Instant::now();
            let mut progress = |line: &str| {
                if let Some(raw) = line.strip_prefix("out_time_us=")
                    && let (Ok(t), Some(d)) = (raw.parse::<f64>(), m.duration())
                {
                    ev.send(Event::Progress(
                        job.source.clone(),
                        (t / 1e6 / d).clamp(0., 0.999),
                        "Compressing".into(),
                    ));
                }
            };
            let decode_key = format!("{}:{}", m.video()["codec_name"], m.video()["pix_fmt"]);
            let hardware = m.rotation() == 0. && !failed_decoders.contains(&decode_key);
            let first = r.stream(
                "ffmpeg",
                &media::encode(&m, &tmp, s, hardware, &art, None)?,
                None,
                false,
                &mut progress,
            );
            if let Err(e) = first {
                r.control.check()?;
                if hardware && !e.to_string().to_lowercase().contains("no space left") {
                    failed_decoders.insert(decode_key);
                    ev.log(format!(
                        "Using software decoding for {}. Compression still uses the graphics card",
                        job.relative.display()
                    ));
                    if tmp.exists() {
                        fs::remove_file(&tmp)?;
                    }
                    r.stream(
                        "ffmpeg",
                        &media::encode(&m, &tmp, s, false, &art, None)?,
                        None,
                        false,
                        progress,
                    )?;
                } else {
                    return Err(e);
                }
            }
            job.encode_seconds = Some(began.elapsed().as_secs_f64());
            let bytes = fs::metadata(&tmp)?.len();
            if bytes == 0
                || bytes >= job.size
                || bytes as f64 > job.size as f64 * (1. - s.min_savings / 100.)
            {
                job.status = Status::Skipped;
                job.message = "The compressed copy was not small enough to keep".into();
                return Ok(None);
            }
            Ok(Some((tmp, cleanup)))
        })();
        match result {
            Ok(Some((tmp, cleanup))) => {
                drain(verifier.take());
                let r = r.clone();
                let s = s.clone();
                let state = state.to_path_buf();
                let ev = ev.clone();
                verifier = Some(thread::spawn(move || {
                    finish(job, tmp, s, r, state, ev, cleanup)
                }));
            }
            Ok(None) => ev.send(Event::Job(Box::new(job))),
            Err(e) => {
                job.status = if r.control.check().is_err() {
                    Status::Cancelled
                } else {
                    Status::Failed
                };
                job.message = e.to_string();
                ev.send(Event::Job(Box::new(job)));
            }
        }
    }
    drain(verifier.take());
    drop(drain);
    drop(qtx);
    let _ = quality.join();
    Ok(())
}
pub fn check_existing(job: &mut Job, state: &Path, s: &Settings, r: &Runner) -> Result<()> {
    let current = job.clone();
    ensure!(
        restore(job, state, s, r, true)?,
        "No previously verified result matches these files; original kept"
    );
    if let Some(proof) = &job.proof {
        let m = media::probe(&job.source, r)?;
        let mut settings = proof.settings.clone();
        settings.strict = true;
        media::verify(&m, &job.destination, &settings, r, proof.full, None)?;
        if !proof.full {
            if let Some(p) = &mut job.proof {
                p.full = true;
                p.settings = settings;
            }
            save_job(job, state)?;
        }
    }
    ensure!(
        job.source == current.source && job.destination == current.destination,
        "Saved file paths differ"
    );
    Ok(())
}
pub fn samples(
    job: &Job,
    dir: &Path,
    state: &Path,
    s: &Settings,
    r: &Runner,
    ev: &Events,
) -> Result<()> {
    ensure!(
        !dir.starts_with(
            job.input_root
                .as_deref()
                .unwrap_or(job.source.parent().unwrap())
        ),
        "Choose a sample folder outside the source tree"
    );
    let _lock = files::OutputLock::acquire(dir)?;
    let m = media::probe(&job.source, r)?;
    let d = m
        .duration()
        .context("Samples need a known duration")?
        .min(8.);
    let start = (m.duration().unwrap() - d) / 2.;
    for profile in ["High Quality", "Balanced", "Maximum Savings"] {
        let mut settings = s.clone();
        settings.profile = profile.into();
        settings.strict = true;
        doctor(&settings, state, r, ev, false)?;
        let out = dir.join(format!(
            "sample-{}-{}.mkv",
            profile.replace(' ', "-").to_lowercase(),
            uuid::Uuid::new_v4().simple()
        ));
        let tmp = out.with_file_name(format!(
            ".petal-{}.partial.mkv",
            uuid::Uuid::new_v4().simple()
        ));
        let mut cleanup = Cleanup::new(dir, state)?;
        cleanup.track(tmp.clone())?;
        let art = prepare_art(&m, &settings, dir, r, &mut cleanup)?;
        r.run(
            "ffmpeg",
            &media::encode(&m, &tmp, &settings, false, &art, Some((start, d)))?,
            None,
        )?;
        media::verify(&m, &tmp, &settings, r, false, Some(d))?;
        files::keep_output(&tmp, &out)?;
        ev.log(format!("{} sample saved: {}", profile, out.display()));
    }
    Ok(())
}
