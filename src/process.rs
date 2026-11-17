//! Bounded pipe draining and process-group cancellation, with Linux parent-death protection.
use crate::model::Control;
use anyhow::{Context, Result, bail};
use std::{
    collections::VecDeque,
    ffi::OsString,
    io::{BufRead, BufReader},
    os::unix::process::CommandExt,
    process::{Command, Stdio},
    sync::mpsc,
    thread,
    time::{Duration, Instant},
};
#[derive(Debug)]
pub struct Output {
    pub stdout: String,
    pub stderr: String,
}
#[derive(Clone, Default)]
pub struct Runner {
    pub control: Control,
    pub priority: i32,
}
impl Runner {
    pub fn run(
        &self,
        program: &str,
        args: &[OsString],
        timeout: Option<Duration>,
    ) -> Result<Output> {
        self.stream(program, args, timeout, true, |_| {})
    }
    pub fn stream(
        &self,
        program: &str,
        args: &[OsString],
        timeout: Option<Duration>,
        capture: bool,
        mut line: impl FnMut(&str),
    ) -> Result<Output> {
        self.control.check()?;
        let parent = std::process::id() as libc::pid_t;
        let priority = self.priority.clamp(0, 19);
        let mut cmd = Command::new(program);
        cmd.args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .process_group(0)
            .env(
                "LIBVA_DRIVER_NAME",
                std::env::var("LIBVA_DRIVER_NAME").unwrap_or("radeonsi".into()),
            );
        unsafe {
            cmd.pre_exec(move || {
                if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL) != 0 {
                    return Err(std::io::Error::last_os_error());
                }
                if priority > 0 {
                    libc::setpriority(libc::PRIO_PROCESS, 0, priority);
                }
                if libc::getppid() != parent {
                    libc::_exit(125)
                }
                Ok(())
            });
        }
        let mut child = cmd
            .spawn()
            .with_context(|| format!("Could not start {program}"))?;
        let pid = child.id() as i32;
        let (tx, rx) = mpsc::sync_channel(256);
        let out = child.stdout.take().unwrap();
        let err = child.stderr.take().unwrap();
        let tx2 = tx.clone();
        let a = thread::spawn(move || {
            for line in BufReader::new(out).lines() {
                if tx
                    .send((
                        false,
                        line.unwrap_or_else(|e| format!("Pipe read error: {e}")),
                    ))
                    .is_err()
                {
                    break;
                }
            }
        });
        let b = thread::spawn(move || {
            for line in BufReader::new(err).lines() {
                if tx2
                    .send((
                        true,
                        line.unwrap_or_else(|e| format!("Pipe read error: {e}")),
                    ))
                    .is_err()
                {
                    break;
                }
            }
        });
        let started = Instant::now();
        let mut stdout = String::new();
        let mut errors = VecDeque::new();
        let mut corrupt = None;
        let mut failure = None;
        loop {
            if self.control.check().is_err() {
                failure = Some("Stopped safely".to_string());
                break;
            }
            if timeout.is_some_and(|d| started.elapsed() > d) {
                failure = Some(format!("{program} took longer than expected"));
                break;
            }
            match rx.recv_timeout(Duration::from_millis(25)) {
                Ok((is_err, s)) => {
                    if is_err {
                        let lower = s.to_lowercase();
                        if [
                            "file ended prematurely",
                            "packet corrupt",
                            "corrupt decoded frame",
                            "error during demuxing",
                        ]
                        .iter()
                        .any(|x| lower.contains(x))
                        {
                            corrupt = Some(s.clone())
                        }
                        errors.push_back(s);
                        while errors.len() > 150 {
                            errors.pop_front();
                        }
                    } else {
                        line(&s);
                        if capture {
                            stdout.push_str(&s);
                            stdout.push('\n');
                            if stdout.len() > 32 * 1024 * 1024 {
                                failure = Some("Media information exceeded the size limit".into());
                                break;
                            }
                        }
                    }
                }
                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    if child.try_wait()?.is_some() {
                        break;
                    }
                }
                Err(mpsc::RecvTimeoutError::Timeout) => {}
            }
        }
        if failure.is_some() {
            unsafe {
                libc::kill(-pid, libc::SIGTERM);
            }
            thread::sleep(Duration::from_millis(80));
            unsafe {
                libc::kill(-pid, libc::SIGKILL);
            }
        }
        let status = child.wait()?;
        drop(rx);
        let _ = a.join();
        let _ = b.join();
        let stderr = errors.into_iter().collect::<Vec<_>>().join("\n");
        if let Some(e) = failure {
            bail!("{e}")
        }
        if !status.success() {
            bail!("{program} could not finish:\n{stderr}")
        }
        if let Some(c) = corrupt {
            bail!("Damaged media reported: {c}")
        }
        Ok(Output { stdout, stderr })
    }
}
pub fn args(items: &[&str]) -> Vec<OsString> {
    items.iter().map(OsString::from).collect()
}

pub static INTERRUPTED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
pub extern "C" fn interrupt(_: libc::c_int) {
    INTERRUPTED.store(true, std::sync::atomic::Ordering::Relaxed);
}
