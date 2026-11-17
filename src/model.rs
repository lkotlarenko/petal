use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
    sync::{
        Arc, Condvar, Mutex,
        atomic::{AtomicBool, Ordering},
        mpsc::Sender,
    },
};

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq, Default)]
pub enum Status {
    #[default]
    Waiting,
    Encoding,
    Verifying,
    Completed,
    Skipped,
    Failed,
    Cancelled,
}
impl Status {
    pub fn finished(&self) -> bool {
        matches!(self, Self::Completed | Self::Skipped | Self::Failed)
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
pub struct Settings {
    pub codec: String,
    pub profile: String,
    pub device: String,
    pub strict: bool,
    pub min_savings: f64,
    pub audio: String,
    pub incompatible: String,
    pub auto_quality: bool,
    pub resolution: String,
}
impl Default for Settings {
    fn default() -> Self {
        Self {
            codec: "av1_vaapi".into(),
            profile: "Balanced".into(),
            device: "/dev/dri/renderD128".into(),
            strict: true,
            min_savings: 5.,
            audio: "copy".into(),
            incompatible: "compatible".into(),
            auto_quality: true,
            resolution: "1080p".into(),
        }
    }
}
impl Settings {
    pub fn validate(&self) -> anyhow::Result<()> {
        anyhow::ensure!(
            ["av1_vaapi", "hevc_vaapi"].contains(&self.codec.as_str())
                && ["High Quality", "Balanced", "Maximum Savings"].contains(&self.profile.as_str()),
            "Choose a supported compression setting"
        );
        anyhow::ensure!(
            self.device.starts_with('/') && (0. ..=99.).contains(&self.min_savings),
            "Invalid device or savings setting"
        );
        anyhow::ensure!(
            ["480p", "720p", "1080p", "1440p", "2160p", "original"]
                .contains(&self.resolution.as_str()),
            "Invalid picture size"
        );
        anyhow::ensure!(
            ["copy", "opus"].contains(&self.audio.as_str())
                && ["compatible", "skip", "omit"].contains(&self.incompatible.as_str()),
            "Invalid track setting"
        );
        Ok(())
    }
    pub fn quality(&self, size: (u32, u32)) -> i32 {
        let av = self.codec == "av1_vaapi";
        let base = match (self.profile.as_str(), av) {
            ("High Quality", true) => 80,
            ("Maximum Savings", true) => 135,
            (_, true) => 105,
            ("High Quality", false) => 20,
            ("Maximum Savings", false) => 29,
            _ => 24,
        };
        let pixels = size.0 as u64 * size.1 as u64;
        let offset = if pixels <= 854 * 480 {
            if av { -12 } else { -2 }
        } else if pixels <= 1280 * 720 {
            if av { -6 } else { -1 }
        } else if pixels <= 1920 * 1080 {
            0
        } else if pixels <= 2560 * 1440 {
            if av { 4 } else { 1 }
        } else if av {
            8
        } else {
            2
        };
        base + offset
    }
}
#[derive(Clone, Debug, Serialize, Deserialize, Default)]
#[serde(default)]
pub struct Media {
    pub path: PathBuf,
    pub streams: Vec<Value>,
    pub format: Value,
    pub chapters: Vec<Value>,
}
impl Media {
    pub fn videos(&self) -> Vec<&Value> {
        self.streams
            .iter()
            .filter(|s| {
                s["codec_type"] == "video"
                    && s["disposition"]["attached_pic"].as_i64().unwrap_or(0) == 0
            })
            .collect()
    }
    pub fn video(&self) -> &Value {
        self.streams
            .iter()
            .find(|s| {
                s["codec_type"] == "video"
                    && s["disposition"]["attached_pic"].as_i64().unwrap_or(0) == 0
            })
            .unwrap_or(&Value::Null)
    }
    pub fn duration(&self) -> Option<f64> {
        [&self.format["duration"], &self.video()["duration"]]
            .into_iter()
            .filter_map(number)
            .find(|x| x.is_finite() && *x > 0. && *x < 1e10)
    }
    pub fn rotation(&self) -> f64 {
        self.video()["side_data_list"]
            .as_array()
            .and_then(|a| a.iter().find_map(|s| number(&s["rotation"])))
            .or_else(|| number(&self.video()["tags"]["rotate"]))
            .unwrap_or(0.)
            .rem_euclid(360.)
    }
    pub fn bits(&self) -> u32 {
        let p = self.video()["pix_fmt"].as_str().unwrap_or("");
        if p.contains("10") {
            10
        } else if p.contains("12") {
            12
        } else {
            self.video()["bits_per_raw_sample"]
                .as_str()
                .and_then(|v| v.parse().ok())
                .filter(|v| *v > 0)
                .unwrap_or(8)
        }
    }
    pub fn hdr(&self) -> bool {
        let v = self.video();
        ["smpte2084", "arib-std-b67"].contains(&v["color_transfer"].as_str().unwrap_or(""))
            || v["color_primaries"] == "bt2020"
            || v["side_data_list"].as_array().is_some_and(|a| {
                a.iter().any(|s| {
                    let t = s["side_data_type"].as_str().unwrap_or("").to_lowercase();
                    ["mastering", "content light", "dovi", "hdr"]
                        .iter()
                        .any(|x| t.contains(x))
                })
            })
    }
}
pub fn number(v: &Value) -> Option<f64> {
    v.as_f64().or_else(|| v.as_str()?.parse().ok())
}
#[derive(Clone, Debug, Serialize, Deserialize, Default)]
#[serde(default)]
pub struct Job {
    pub source: PathBuf,
    pub relative: PathBuf,
    pub destination: PathBuf,
    pub media: Option<Media>,
    pub size: u64,
    pub status: Status,
    pub message: String,
    pub final_size: u64,
    pub progress: f64,
    pub source_key: String,
    pub quality: Value,
    pub output_dimensions: Option<(u32, u32)>,
    pub encoder_quality: Option<i32>,
    pub input_root: Option<PathBuf>,
    pub explicit_file: bool,
    pub encode_seconds: Option<f64>,
    pub verify_seconds: Option<f64>,
    pub encoder_profile: Option<String>,
    pub encoder_codec: Option<String>,
    pub relocated_to: Option<PathBuf>,
    pub relocation_error: String,
    pub output_root: Option<PathBuf>,
    pub proof: Option<Proof>,
    pub trashed: bool,
}
#[derive(Clone, Debug, Serialize, Deserialize, Default)]
pub struct Proof {
    pub source_key: String,
    pub output_key: String,
    pub source_sha: String,
    pub output_sha: String,
    pub settings: Settings,
    pub full: bool,
}
#[derive(Clone, Serialize, Deserialize, Default)]
#[serde(default)]
pub struct Library {
    pub source: String,
    pub output: String,
    pub settings: Settings,
    pub inputs: Vec<PathBuf>,
    pub namespaces: BTreeMap<String, String>,
    pub jobs: Vec<Job>,
    pub report: Vec<Job>,
    pub report_output: Option<PathBuf>,
}
impl Library {
    pub fn load(dir: &Path) -> anyhow::Result<Self> {
        let native = dir.join("native-v1.json");
        if native.exists() {
            return Ok(serde_json::from_slice(&std::fs::read(native)?)?);
        }
        let mut s = Self::default();
        let config = crate::files::xdg("config").join("settings.json");
        if config.exists() {
            let v: Value = serde_json::from_slice(&std::fs::read(config)?)?;
            s.source = v["source"].as_str().unwrap_or("").into();
            s.output = v["output"].as_str().unwrap_or("").into();
            s.settings = serde_json::from_value(v["settings"].clone()).unwrap_or_default();
            s.inputs = serde_json::from_value(v["drop_inputs"].clone()).unwrap_or_default();
            s.namespaces = serde_json::from_value(v["drop_namespaces"].clone()).unwrap_or_default();
        }
        let history = dir.join("history.json");
        if history.exists() {
            let h: Value = serde_json::from_slice(&std::fs::read(history)?)?;
            s.jobs = serde_json::from_value(h["jobs"].clone())?;
            s.report = serde_json::from_value(h["report"].clone()).unwrap_or_default();
            s.report_output = serde_json::from_value(h["output"].clone()).unwrap_or(None);
        }
        Ok(s)
    }
    pub fn save(&self, dir: &Path) -> anyhow::Result<()> {
        crate::files::atomic_json(&dir.join("native-v1.json"), self)
    }
}
#[derive(Clone, Debug)]
pub enum Event {
    Applied(Settings),
    Log(String),
    Job(Box<Job>),
    Progress(PathBuf, f64, String),
    Scanned(Vec<Job>, BTreeMap<String, String>),
    Done(String),
    Hardware(String),
}
#[derive(Clone)]
pub struct Control {
    pub cancel: Arc<AtomicBool>,
    pub pause: Arc<(Mutex<bool>, Condvar)>,
}
impl Default for Control {
    fn default() -> Self {
        Self {
            cancel: Arc::new(AtomicBool::new(false)),
            pause: Arc::new((Mutex::new(false), Condvar::new())),
        }
    }
}
impl Control {
    pub fn check(&self) -> anyhow::Result<()> {
        anyhow::ensure!(
            !self.cancel.load(Ordering::Relaxed)
                && !crate::process::INTERRUPTED.load(Ordering::Relaxed),
            "Stopped safely"
        );
        Ok(())
    }
    pub fn stop(&self) {
        self.cancel.store(true, Ordering::Relaxed);
        self.set_pause(false);
    }
    pub fn set_pause(&self, b: bool) {
        *self.pause.0.lock().unwrap() = b;
        self.pause.1.notify_all();
    }
    pub fn wait(&self) -> anyhow::Result<()> {
        let mut p = self.pause.0.lock().unwrap();
        while *p && !self.cancel.load(Ordering::Relaxed) {
            p = self.pause.1.wait(p).unwrap();
        }
        self.check()
    }
}
#[derive(Clone)]
pub struct Events {
    pub tx: Sender<Event>,
    pub ctx: Option<eframe::egui::Context>,
}
impl Events {
    pub fn send(&self, e: Event) {
        let _ = self.tx.send(e);
        if let Some(c) = &self.ctx {
            c.request_repaint();
        }
    }
    pub fn log(&self, s: impl Into<String>) {
        self.send(Event::Log(s.into()));
    }
}
pub fn size(n: u64) -> String {
    let mut v = n as f64;
    for u in ["B", "KiB", "MiB", "GiB", "TiB"] {
        if v < 1024. || u == "TiB" {
            return format!("{v:.1} {u}");
        }
        v /= 1024.;
    }
    String::new()
}
pub fn summary(jobs: &[Job]) -> Value {
    let mut c = [0_u64; 4];
    let (mut old, mut saved) = (0_u64, 0_u64);
    for j in jobs {
        match j.status {
            Status::Completed => {
                c[0] += 1;
                old += j.size;
                saved += j.size.saturating_sub(j.final_size)
            }
            Status::Skipped => c[1] += 1,
            Status::Failed => c[2] += 1,
            _ => c[3] += 1,
        }
    }
    json!({"completed":c[0],"skipped":c[1],"failed":c[2],"unfinished":c[3],"saved":saved,"percent":if old>0{100.*saved as f64/old as f64}else{0.}})
}
