//! Local-only, no-overwrite file operations. Same-device moves do not copy file data.
use crate::{
    model::{Control, Event, Events, Job, Status},
    process::Runner,
};
use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    ffi::{CString, OsString},
    fs::{self, File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    os::{
        fd::{AsRawFd, FromRawFd},
        unix::{
            ffi::OsStrExt,
            fs::{MetadataExt, OpenOptionsExt},
        },
    },
    path::{Component, Path, PathBuf},
};
pub fn xdg(kind: &str) -> PathBuf {
    let (var, suffix) = match kind {
        "config" => ("XDG_CONFIG_HOME", ".config"),
        "cache" => ("XDG_CACHE_HOME", ".cache"),
        _ => ("XDG_STATE_HOME", ".local/state"),
    };
    let home = PathBuf::from(std::env::var_os("HOME").unwrap_or_else(|| "/tmp".into()));
    let base = std::env::var_os(var)
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
        .unwrap_or_else(|| home.join(suffix));
    base.join("petal")
}
pub fn sync_dir(p: &Path) -> Result<()> {
    File::open(p)?.sync_all()?;
    Ok(())
}
pub fn atomic_json(p: &Path, v: &impl Serialize) -> Result<()> {
    let parent = p.parent().context("Missing state folder")?;
    fs::create_dir_all(parent)?;
    let tmp = parent.join(format!(".petal-{}.json.tmp", uuid::Uuid::new_v4().simple()));
    let result = (|| {
        let mut f = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&tmp)?;
        serde_json::to_writer(&mut f, v)?;
        f.flush()?;
        f.sync_all()?;
        fs::rename(&tmp, p)?;
        sync_dir(parent)
    })();
    if tmp.exists() {
        let _ = fs::remove_file(tmp);
    }
    result
}
pub fn identity(m: &fs::Metadata) -> (u64, u64, u64, i64, i64) {
    (m.dev(), m.ino(), m.len(), m.mtime(), m.mtime_nsec())
}
fn stable(a: &fs::Metadata, b: &fs::Metadata) -> bool {
    identity(a) == identity(b) && a.ctime() == b.ctime() && a.ctime_nsec() == b.ctime_nsec()
}
pub fn open_source(p: &Path) -> Result<File> {
    let _ = parent(p, false)?;
    let f = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
        .open(p)?;
    ensure!(
        f.metadata()?.is_file(),
        "Not a regular file: {}",
        p.display()
    );
    Ok(f)
}
pub fn fingerprint(p: &Path) -> Result<String> {
    let mut f = open_source(p)?;
    let m = f.metadata()?;
    let mut h = Sha256::new();
    h.update(format!(
        "{}:{}:{}:{}",
        m.dev(),
        m.ino(),
        m.len(),
        m.mtime() as i128 * 1_000_000_000 + m.mtime_nsec() as i128
    ));
    let mut buf = Vec::with_capacity(1024 * 1024);
    (&mut f).take(1024 * 1024).read_to_end(&mut buf)?;
    h.update(&buf);
    if m.len() > 1024 * 1024 {
        f.seek(SeekFrom::Start(m.len().saturating_sub(1024 * 1024)))?;
        buf.clear();
        (&mut f).take(1024 * 1024).read_to_end(&mut buf)?;
        h.update(&buf);
    }
    ensure!(stable(&m, &f.metadata()?), "File changed while checking it");
    Ok(format!("{:x}", h.finalize()))
}
pub fn hash_file(p: &Path, c: &Control) -> Result<String> {
    hash(&mut open_source(p)?, c)
}
pub fn hash(f: &mut File, c: &Control) -> Result<String> {
    let before = f.metadata()?;
    ensure!(before.is_file(), "Only regular files may be checked");
    f.seek(SeekFrom::Start(0))?;
    let mut h = Sha256::new();
    let mut buffer = vec![0; 1024 * 1024];
    loop {
        c.check()?;
        let n = f.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        h.update(&buffer[..n]);
    }
    ensure!(
        stable(&before, &f.metadata()?),
        "File changed while checking it"
    );
    Ok(format!("{:x}", h.finalize()))
}
pub fn validate_roots(source: &Path, output: &Path) -> Result<(PathBuf, PathBuf)> {
    let source = fs::canonicalize(source)?;
    ensure!(source.is_dir(), "Choose an existing source folder");
    let output = absolute(output)?;
    ensure!(
        source != output && !source.starts_with(&output) && !output.starts_with(&source),
        "Source and output must be separate folders"
    );
    Ok((source, output))
}
pub fn absolute(p: &Path) -> Result<PathBuf> {
    let p = if p.is_absolute() {
        p.to_path_buf()
    } else {
        std::env::current_dir()?.join(p)
    };
    ensure!(
        !p.components().any(|x| matches!(x, Component::ParentDir)),
        "Parent traversal is not allowed"
    );
    if p.exists() {
        return Ok(fs::canonicalize(p)?);
    }
    let name = p.file_name().context("Choose a folder")?;
    let parent_path = p.parent().context("Missing parent")?;
    Ok(absolute(parent_path)?.join(name))
}
pub fn parent(p: &Path, create: bool) -> Result<File> {
    ensure!(
        p.is_absolute() && !p.components().any(|c| matches!(c, Component::ParentDir)),
        "Unsafe file path"
    );
    let mut fd = File::open("/")?;
    for part in p.parent().context("Missing parent")?.components() {
        if let Component::Normal(name) = part {
            let name = CString::new(name.as_bytes())?;
            if create {
                let r = unsafe { libc::mkdirat(fd.as_raw_fd(), name.as_ptr(), 0o755) };
                if r == 0 {
                    fd.sync_all()?
                } else if std::io::Error::last_os_error().raw_os_error() != Some(libc::EEXIST) {
                    return Err(std::io::Error::last_os_error().into());
                }
            }
            let n = unsafe {
                libc::openat(
                    fd.as_raw_fd(),
                    name.as_ptr(),
                    libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
                )
            };
            if n < 0 {
                return Err(std::io::Error::last_os_error().into());
            }
            fd = unsafe { File::from_raw_fd(n) };
        }
    }
    Ok(fd)
}
fn leaf(p: &Path) -> Result<CString> {
    Ok(CString::new(
        p.file_name().context("Missing filename")?.as_bytes(),
    )?)
}
pub fn rename_new(a: &Path, b: &Path) -> Result<()> {
    let ad = parent(a, false)?;
    let bd = parent(b, true)?;
    let r = unsafe {
        libc::renameat2(
            ad.as_raw_fd(),
            leaf(a)?.as_ptr(),
            bd.as_raw_fd(),
            leaf(b)?.as_ptr(),
            libc::RENAME_NOREPLACE,
        )
    };
    if r != 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    ad.sync_all()?;
    bd.sync_all()?;
    Ok(())
}
pub struct OutputLock(File);
impl OutputLock {
    pub fn acquire(root: &Path) -> Result<Self> {
        fs::create_dir_all(root)?;
        let f = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW)
            .open(root.join(".petal.lock"))?;
        ensure!(
            unsafe { libc::flock(f.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } == 0,
            "Another Petal operation is using this output folder"
        );
        Ok(Self(f))
    }
}
impl Drop for OutputLock {
    fn drop(&mut self) {
        unsafe {
            libc::flock(self.0.as_raw_fd(), libc::LOCK_UN);
        }
    }
}
pub fn keep_output(tmp: &Path, dst: &Path) -> Result<()> {
    let ad = parent(tmp, false)?;
    let bd = parent(dst, true)?;
    open_source(tmp)?.sync_all()?;
    let r = unsafe {
        libc::linkat(
            ad.as_raw_fd(),
            leaf(tmp)?.as_ptr(),
            bd.as_raw_fd(),
            leaf(dst)?.as_ptr(),
            0,
        )
    };
    if r != 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    bd.sync_all()?;
    fs::remove_file(tmp)?;
    ad.sync_all()?;
    Ok(())
}
#[derive(Clone, Serialize, Deserialize)]
struct MoveRecord {
    source: PathBuf,
    destination: PathBuf,
    hold: PathBuf,
    temporary: PathBuf,
    original: (u64, u64, u64, i64, i64),
    sha: String,
    phase: String,
    same_device: bool,
}
pub struct Mover {
    root: PathBuf,
    path: PathBuf,
    records: BTreeMap<String, MoveRecord>,
}
impl Mover {
    pub fn new(root: &Path, state: &Path) -> Result<Self> {
        let root = absolute(root)?;
        let key = format!("{:x}", Sha256::digest(root.as_os_str().as_bytes()));
        let path = state.join(format!("native-moves-{}.json", &key[..24]));
        let records = if path.exists() {
            serde_json::from_slice(&fs::read(&path)?)?
        } else {
            BTreeMap::new()
        };
        Ok(Self {
            root,
            path,
            records,
        })
    }
    fn save(&self) -> Result<()> {
        atomic_json(&self.path, &self.records)
    }
    pub fn recover(&mut self, c: &Control) -> Result<()> {
        let mut dirty = false;
        for r in self.records.values_mut() {
            c.check()?;
            if r.phase == "done" {
                continue;
            }
            ensure!(
                r.destination.starts_with(&self.root)
                    && r.hold.parent() == r.source.parent()
                    && r.hold
                        .file_name()
                        .unwrap_or_default()
                        .to_string_lossy()
                        .starts_with(".petal-"),
                "Unsafe recovery record"
            );
            if fs::symlink_metadata(&r.temporary).is_ok() {
                let name = r
                    .temporary
                    .file_name()
                    .unwrap_or_default()
                    .to_string_lossy();
                ensure!(
                    r.temporary.parent() == r.destination.parent()
                        && name.starts_with(".petal-")
                        && name.ends_with(".move-copy"),
                    "Unsafe temporary copy record"
                );
                let _parent = parent(&r.temporary, false)?;
                ensure!(
                    fs::symlink_metadata(&r.temporary)?.is_file(),
                    "Temporary copy changed; retained"
                );
                fs::remove_file(&r.temporary)?;
                sync_dir(r.temporary.parent().unwrap())?;
            }
            if r.same_device && !r.source.exists() && r.destination.exists() {
                ensure!(
                    identity(&fs::symlink_metadata(&r.destination)?) == r.original,
                    "Moved file changed; inspect {}",
                    r.destination.display()
                );
                r.phase = "done".into();
                dirty = true;
                continue;
            }
            if r.hold.exists() {
                ensure!(
                    identity(&fs::symlink_metadata(&r.hold)?) == r.original,
                    "Held original changed; retained for inspection"
                );
                rename_new(&r.hold, &r.source)?;
                r.phase = "copied".into();
                dirty = true;
            } else if r.phase == "held" && !r.source.exists() {
                ensure!(
                    !r.sha.is_empty() && hash_file(&r.destination, c)? == r.sha,
                    "Recovery copy could not be verified"
                );
                r.phase = "done".into();
                dirty = true;
            }
        }
        if dirty { self.save() } else { Ok(()) }
    }
    fn finish_cross(
        &mut self,
        record: &mut MoveRecord,
        source: &mut File,
        c: &Control,
        key: &str,
    ) -> Result<()> {
        record.phase = "held".into();
        self.records.insert(key.to_string(), record.clone());
        self.save()?;
        if let Err(error) = rename_new(&record.source, &record.hold) {
            if record.hold.exists() && !record.source.exists() {
                let _ = rename_new(&record.hold, &record.source);
            }
            return Err(error);
        }
        let checked = (|| -> Result<()> {
            ensure!(
                identity(&fs::symlink_metadata(&record.hold)?) == record.original,
                "Original path changed"
            );
            ensure!(
                hash(source, c)? == record.sha,
                "Original changed before removal"
            );
            let checked = source.metadata()?;
            let before_copy = fs::symlink_metadata(&record.destination)?;
            ensure!(
                hash_file(&record.destination, c)? == record.sha,
                "Destination changed before source removal"
            );
            ensure!(
                stable(&checked, &source.metadata()?),
                "Original changed during final verification"
            );
            ensure!(
                stable(&before_copy, &fs::symlink_metadata(&record.destination)?),
                "Destination changed during final verification"
            );
            ensure!(
                identity(&fs::symlink_metadata(&record.hold)?) == record.original,
                "Held source path changed"
            );
            fs::remove_file(&record.hold)?;
            sync_dir(record.hold.parent().unwrap())?;
            Ok(())
        })();
        if checked.is_err() && record.hold.exists() {
            rename_new(&record.hold, &record.source)?;
        }
        checked?;
        record.phase = "done".into();
        self.records.insert(key.to_string(), record.clone());
        self.save()?;
        Ok(())
    }
    pub fn relocate(&mut self, job: &mut Job, category: &str, c: &Control) -> Result<()> {
        ensure!(
            matches!(
                (category, &job.status),
                ("skipped", Status::Skipped) | ("failed", Status::Failed)
            ),
            "Only skipped or failed originals can be moved"
        );
        ensure!(
            job.relocated_to.is_none() && !job.trashed,
            "This original has already been organized"
        );
        ensure!(
            !job.relative.is_absolute()
                && !job
                    .relative
                    .components()
                    .any(|x| matches!(x, Component::ParentDir)),
            "Unsafe relative path"
        );
        let dst = self.root.join(category).join(&job.relative);
        ensure!(
            !job.source.starts_with(&self.root),
            "Original is already inside the output folder"
        );
        if let Some(r) = self.records.get(&job.source.to_string_lossy().to_string())
            && r.phase == "done"
            && !job.source.exists()
        {
            job.relocated_to = Some(r.destination.clone());
            return Ok(());
        }
        let mut source = open_source(&job.source)?;
        let initial = source.metadata()?;
        ensure!(
            unsafe { libc::flock(source.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } == 0,
            "Original is in use"
        );
        if !job.source_key.is_empty() {
            ensure!(
                fingerprint(&job.source)? == job.source_key,
                "Original changed since processing; left in place"
            )
        };
        let dest_dir = parent(&dst, true)?;
        let same = initial.dev() == dest_dir.metadata()?.dev();
        if let Some(mut previous) = self.records.get(&key_for(&job.source)).cloned()
            && !previous.same_device
            && !previous.sha.is_empty()
            && dst.exists()
        {
            ensure!(
                previous.original == identity(&initial),
                "Original changed since interrupted move"
            );
            ensure!(
                hash_file(&dst, c)? == previous.sha && hash(&mut source, c)? == previous.sha,
                "Recovery copy changed; original retained"
            );
            self.finish_cross(&mut previous, &mut source, c, &key_for(&job.source))?;
            job.relocated_to = Some(dst);
            return Ok(());
        }
        ensure!(
            !dst.try_exists()? && fs::symlink_metadata(&dst).is_err(),
            "Destination already exists; original kept"
        );
        let key = job.source.to_string_lossy().to_string();
        let id = uuid::Uuid::new_v4().simple().to_string();
        let mut record = MoveRecord {
            source: job.source.clone(),
            destination: dst.clone(),
            hold: job
                .source
                .parent()
                .unwrap()
                .join(format!(".petal-{id}.move-source")),
            temporary: dst.parent().unwrap().join(format!(".petal-{id}.move-copy")),
            original: identity(&initial),
            sha: String::new(),
            phase: "prepared".into(),
            same_device: same,
        };
        self.records.insert(key.clone(), record.clone());
        self.save()?;
        c.check()?;
        if same {
            ensure!(
                identity(&fs::symlink_metadata(&job.source)?) == record.original,
                "Original changed"
            );
            source.sync_all()?;
            rename_new(&job.source, &dst)?;
            record.phase = "done".into();
            self.records.insert(key, record);
            self.save()?;
            job.relocated_to = Some(dst);
            return Ok(());
        }
        let result = (|| -> Result<()> {
            let mut target = OpenOptions::new()
                .write(true)
                .read(true)
                .create_new(true)
                .mode(initial.mode() & 0o777)
                .custom_flags(libc::O_NOFOLLOW)
                .open(&record.temporary)?;
            let mut buf = vec![0; 1024 * 1024];
            let mut h = Sha256::new();
            loop {
                c.check()?;
                let n = source.read(&mut buf)?;
                if n == 0 {
                    break;
                }
                target.write_all(&buf[..n])?;
                h.update(&buf[..n]);
            }
            ensure!(
                stable(&initial, &source.metadata()?),
                "Original changed during copy"
            );
            target.set_times(
                std::fs::FileTimes::new()
                    .set_accessed(initial.accessed()?)
                    .set_modified(initial.modified()?),
            )?;
            target.sync_all()?;
            record.sha = format!("{:x}", h.finalize());
            ensure!(
                hash(&mut target, c)? == record.sha,
                "Copied bytes failed verification"
            );
            self.records.insert(key.clone(), record.clone());
            self.save()?;
            keep_output(&record.temporary, &dst)?;
            record.phase = "copied".into();
            self.records.insert(key.clone(), record.clone());
            self.save()?;
            c.check()?;
            ensure!(
                stable(&initial, &source.metadata()?),
                "Original changed; verified copy retained"
            );
            self.finish_cross(&mut record, &mut source, c, &key)?;
            Ok(())
        })();
        if record.temporary.exists() {
            let _ = fs::remove_file(&record.temporary);
        }
        result?;
        job.relocated_to = Some(dst);
        Ok(())
    }
}
pub fn organize(
    jobs: &[Job],
    root: &Path,
    state: &Path,
    category: &str,
    runner: &Runner,
    events: &Events,
) -> Result<()> {
    let _lock = OutputLock::acquire(root)?;
    let mut mover = Mover::new(root, state)?;
    mover.recover(&runner.control)?;
    recover_legacy_moves(root, state, &runner.control)?;
    for (i, job) in jobs.iter().enumerate() {
        runner.control.check()?;
        let mut job = job.clone();
        events.log(format!(
            "Moving {} ({}/{})",
            job.relative.display(),
            i + 1,
            jobs.len()
        ));
        if let Err(e) = mover.relocate(&mut job, category, &runner.control) {
            job.relocation_error = e.to_string();
            events.log(format!("Kept original: {e}"));
        }
        events.send(Event::Job(Box::new(job)));
    }
    Ok(())
}
/// Restore Python-era source holds; never remove an original during migration.
pub fn recover_legacy_moves(root: &Path, state: &Path, c: &Control) -> Result<()> {
    let key = format!("{:x}", Sha256::digest(root.to_string_lossy().as_bytes()));
    let path = state.join(format!("relocations-{}.json", &key[..24]));
    if !path.exists() {
        return Ok(());
    }
    let records: serde_json::Value = serde_json::from_slice(&fs::read(path)?)?;
    for r in records
        .as_object()
        .context("Unreadable move journal")?
        .values()
    {
        if r["phase"] == "moved" {
            continue;
        }
        let source = PathBuf::from(r["source"].as_str().context("Missing recovery path")?);
        let hold = r["hold"].as_str().context("Missing hold name")?;
        ensure!(
            hold.starts_with(".petal-") && hold.ends_with(".move-source") && !hold.contains('/'),
            "Unsafe recovery name"
        );
        let held = source.parent().context("Missing source folder")?.join(hold);
        if held.exists() {
            c.check()?;
            rename_new(&held, &source)?;
        }
    }
    Ok(())
}
pub fn trash_original(job: &mut Job, runner: &Runner) -> Result<()> {
    ensure!(
        job.status == Status::Completed && !job.trashed && job.relocated_to.is_none(),
        "Only verified compressed originals can go to Trash"
    );
    let proof = job.proof.as_ref().context(
        "This older result needs a fresh verification before its original can go to Trash",
    )?;
    ensure!(
        job.source != job.destination,
        "Original and compressed copy must be different files"
    );
    ensure!(
        fingerprint(&job.source)? == proof.source_key
            && fingerprint(&job.destination)? == proof.output_key,
        "A file changed since verification; nothing was trashed"
    );
    ensure!(
        proof.full,
        "A thorough verification is required before moving an original to Trash"
    );
    ensure!(
        !proof.source_sha.is_empty() && !proof.output_sha.is_empty(),
        "Full verification proof is missing"
    );
    ensure!(
        hash_file(&job.source, &runner.control)? == proof.source_sha
            && hash_file(&job.destination, &runner.control)? == proof.output_sha,
        "A file no longer matches its verified checksum; nothing was trashed"
    );
    runner.control.check()?;
    runner.run("gio",&[OsString::from("trash"),OsString::from("--"),job.source.as_os_str().to_owned()],None).context("Could not move the original to the desktop Trash. No permanent-delete fallback is used")?;
    job.trashed = true;
    Ok(())
}

fn key_for(p: &Path) -> String {
    p.to_string_lossy().to_string()
}
