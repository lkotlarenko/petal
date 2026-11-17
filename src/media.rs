//! FFmpeg command construction, track policy, metadata/timeline verification and picture checks.
use crate::{
    model::{Media, Settings, number},
    process::{Runner, args},
};
use anyhow::{Context, Result, bail, ensure};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    ffi::OsString,
    path::{Path, PathBuf},
    time::Duration,
};
pub fn probe(path: &Path, r: &Runner) -> Result<Media> {
    let mut a = args(&[
        "-v",
        "error",
        "-protocol_whitelist",
        "file,pipe",
        "-show_streams",
        "-show_format",
        "-show_chapters",
        "-of",
        "json",
    ]);
    a.push(path.into());
    let text = r.run("ffprobe", &a, Some(Duration::from_secs(120)))?;
    let mut m: Media = serde_json::from_str(&text.stdout)?;
    m.path = path.into();
    ensure!(!m.videos().is_empty(), "No playable video stream");
    Ok(m)
}
pub fn ratio(v: &str) -> f64 {
    let p = v.replace(':', "/");
    if let Some((a, b)) = p.split_once('/') {
        a.parse::<f64>().unwrap_or(0.) / b.parse::<f64>().unwrap_or(1.)
    } else {
        p.parse().unwrap_or(0.)
    }
}
pub fn display(m: &Media) -> Result<(f64, f64)> {
    let v = m.video();
    let sar = ratio(v["sample_aspect_ratio"].as_str().unwrap_or("1:1"));
    let sar = if sar.is_finite() && sar > 0. { sar } else { 1. };
    let (mut w, mut h) = (
        v["width"].as_u64().unwrap_or(0) as f64 * sar,
        v["height"].as_u64().unwrap_or(0) as f64,
    );
    let angle = m.rotation();
    ensure!(
        [0., 90., 180., 270.]
            .iter()
            .any(|a| (angle - a).abs() < 0.01),
        "Unusual rotation needs review"
    );
    if (angle - 90.).abs() < 0.01 || (angle - 270.).abs() < 0.01 {
        std::mem::swap(&mut w, &mut h)
    }
    ensure!(
        w >= 2. && h >= 2. && w.is_finite() && h.is_finite(),
        "Invalid picture dimensions"
    );
    Ok((w, h))
}
pub fn target(m: &Media, s: &Settings) -> Result<(u32, u32)> {
    let (w, h) = display(m)?;
    let bound = match s.resolution.as_str() {
        "480p" => Some((854., 480.)),
        "720p" => Some((1280., 720.)),
        "1080p" => Some((1920., 1080.)),
        "1440p" => Some((2560., 1440.)),
        "2160p" => Some((3840., 2160.)),
        _ => None,
    };
    let (maxw, maxh) = match bound {
        Some((l, t)) => {
            if w > h {
                (l, t)
            } else if h > w {
                (t, l)
            } else {
                (t, t)
            }
        }
        None => (w, h),
    };
    let f = 1_f64.min(maxw / w).min(maxh / h);
    let even = |v: f64, cap: f64| {
        (2. * (v * f / 2.).round())
            .min(2. * (v / 2.).floor())
            .min(cap)
            .max(2.) as u32
    };
    Ok((even(w, maxw), even(h, maxh)))
}
#[derive(Clone, Default)]
pub struct Plan {
    pub streams: Vec<Value>,
    pub artwork: Vec<Value>,
    pub subtitles: BTreeMap<u64, String>,
    pub metadata: BTreeMap<String, String>,
    pub warnings: Vec<String>,
}
const AUDIO: &[&str] = &[
    "aac",
    "ac3",
    "eac3",
    "dts",
    "truehd",
    "flac",
    "alac",
    "mp2",
    "mp3",
    "opus",
    "vorbis",
    "pcm_s16le",
    "pcm_s24le",
    "pcm_s32le",
    "pcm_f32le",
    "pcm_f64le",
    "pcm_s16be",
    "pcm_s24be",
    "mlp",
    "wavpack",
    "tta",
];
const SUBS: &[&str] = &[
    "subrip",
    "ass",
    "ssa",
    "webvtt",
    "hdmv_pgs_subtitle",
    "dvd_subtitle",
];
pub fn plan(m: &Media, s: &Settings) -> Result<Plan> {
    ensure!(
        m.videos().len() == 1,
        "Multiple video angles need manual review"
    );
    ensure!(!m.hdr(), "HDR / wide-gamut video needs manual review");
    ensure!(
        m.bits() <= 10,
        "Picture precision above 10 bits is not supported"
    );
    ensure!(
        ["", "unknown", "progressive"].contains(&m.video()["field_order"].as_str().unwrap_or("")),
        "Interlaced video needs manual review"
    );
    ensure!(
        ["", "yuv420p", "yuvj420p", "nv12", "yuv420p10le", "p010le"]
            .contains(&m.video()["pix_fmt"].as_str().unwrap_or("")),
        "This picture format needs manual review"
    );
    let mut p = Plan::default();
    for stream in &m.streams {
        let kind = stream["codec_type"].as_str().unwrap_or("");
        let codec = stream["codec_name"].as_str().unwrap_or("");
        let id = stream["index"].as_u64().context("Missing stream number")?;
        if s.incompatible == "compatible" {
            if kind == "video" && stream != m.video() && ["mjpeg", "png"].contains(&codec) {
                p.artwork.push(stream.clone());
                p.warnings.push("Cover art kept as an attachment".into());
                continue;
            }
            if kind == "data"
                && stream["codec_tag_string"] == "tmcd"
                && let Some(t) = stream["tags"]["timecode"].as_str()
            {
                p.metadata
                    .insert(format!("petal_timecode_stream_{id}"), t.into());
                p.warnings.push("Timecode kept as metadata".into());
                continue;
            }
            if kind == "subtitle" && ["mov_text", "text"].contains(&codec) {
                p.subtitles.insert(id, "ass".into());
                p.warnings
                    .push("Subtitle text and timing converted to ASS".into());
            }
        }
        let issue = if kind == "video" && stream != m.video() {
            Some("unsupported cover art")
        } else if kind == "audio" && s.audio == "copy" && !AUDIO.contains(&codec) {
            Some("unsupported audio track")
        } else if kind == "subtitle" && !SUBS.contains(&codec) && !p.subtitles.contains_key(&id) {
            Some("unsupported subtitle track")
        } else if !["video", "audio", "subtitle", "attachment"].contains(&kind) {
            Some("unsupported auxiliary track")
        } else {
            None
        };
        if kind == "audio" && s.audio == "opus" {
            ensure!(
                [
                    "mono",
                    "stereo",
                    "3.0",
                    "quad",
                    "5.0",
                    "5.0(side)",
                    "5.1",
                    "5.1(side)",
                    "6.1",
                    "7.1"
                ]
                .contains(&stream["channel_layout"].as_str().unwrap_or("")),
                "Audio layout needs review before Opus conversion"
            )
        };
        if let Some(issue) = issue {
            if s.incompatible != "omit" {
                bail!("Track {id}: {issue}; review the track setting")
            }
            p.warnings
                .push(format!("Explicitly omitted track {id}: {issue}"));
        } else {
            p.streams.push(stream.clone());
        }
    }
    Ok(p)
}
fn color_code(value: &str, table: &[(&str, i32)]) -> Option<i32> {
    table.iter().find(|(s, _)| *s == value).map(|(_, n)| *n)
}
const PRIM: &[(&str, i32)] = &[
    ("bt709", 1),
    ("bt470m", 4),
    ("bt470bg", 5),
    ("smpte170m", 6),
    ("smpte240m", 7),
    ("film", 8),
    ("bt2020", 9),
    ("smpte428", 10),
    ("smpte431", 11),
    ("smpte432", 12),
    ("jedec-p22", 22),
    ("ebu3213", 22),
];
const TRC: &[(&str, i32)] = &[
    ("bt709", 1),
    ("bt470m", 4),
    ("bt470bg", 5),
    ("smpte170m", 6),
    ("smpte240m", 7),
    ("linear", 8),
    ("log100", 9),
    ("log316", 10),
    ("iec61966-2-4", 11),
    ("bt1361e", 12),
    ("iec61966-2-1", 13),
    ("bt2020-10", 14),
    ("bt2020-12", 15),
    ("smpte2084", 16),
    ("smpte428", 17),
    ("arib-std-b67", 18),
];
const MATRIX: &[(&str, i32)] = &[
    ("gbr", 0),
    ("bt709", 1),
    ("fcc", 4),
    ("bt470bg", 5),
    ("smpte170m", 6),
    ("smpte240m", 7),
    ("ycgco", 8),
    ("bt2020nc", 9),
    ("bt2020c", 10),
    ("smpte2085", 11),
    ("chroma-derived-nc", 12),
    ("chroma-derived-c", 13),
    ("ictcp", 14),
];
fn range(m: &Media) -> Option<i32> {
    match m.video()["color_range"].as_str() {
        Some("tv" | "limited" | "mpeg") => Some(0),
        Some("pc" | "full" | "jpeg") => Some(1),
        _ => None,
    }
}
pub fn scale_color(m: &Media) -> String {
    let mut v = Vec::new();
    let matrix = match m.video()["color_space"].as_str().unwrap_or("") {
        "bt709" => Some("bt709"),
        "fcc" => Some("fcc"),
        "bt470bg" | "smpte170m" => Some("bt601"),
        "smpte240m" => Some("smpte240m"),
        _ => None,
    };
    if let Some(m) = matrix {
        v.push(format!("in_color_matrix={m}:out_color_matrix={m}"))
    }
    if let Some(r) = range(m) {
        let r = if r == 1 { "full" } else { "limited" };
        v.push(format!("in_range={r}:out_range={r}"))
    }
    if v.is_empty() {
        String::new()
    } else {
        format!(":{}", v.join(":"))
    }
}
fn colors(m: &Media, av1: bool, frame: bool) -> String {
    let mut v = Vec::new();
    for (key, opt, table) in [
        (
            "color_primaries",
            if frame || av1 {
                "color_primaries"
            } else {
                "colour_primaries"
            },
            PRIM,
        ),
        (
            "color_transfer",
            if frame {
                "color_trc"
            } else {
                "transfer_characteristics"
            },
            TRC,
        ),
        (
            "color_space",
            if frame {
                "colorspace"
            } else {
                "matrix_coefficients"
            },
            MATRIX,
        ),
    ] {
        if let Some(n) = color_code(m.video()[key].as_str().unwrap_or(""), table) {
            v.push(format!("{opt}={n}"))
        }
    }
    if let Some(r) = range(m) {
        v.push(if frame {
            format!("range={}", if r == 1 { "full" } else { "limited" })
        } else {
            format!(
                "{}={r}",
                if av1 {
                    "color_range"
                } else {
                    "video_full_range_flag"
                }
            )
        })
    }
    v.join(":")
}
pub fn encode(
    m: &Media,
    out: &Path,
    s: &Settings,
    gpu: bool,
    art: &BTreeMap<u64, PathBuf>,
    sample: Option<(f64, f64)>,
) -> Result<Vec<OsString>> {
    let p = plan(m, s)?;
    let (w, h) = target(m, s)?;
    let (dw, dh) = display(m)?;
    let sar = dw / dh * h as f64 / w as f64;
    let numerator = (sar * 1_000_000.).round() as u64;
    let fmt = if m.bits() == 10 { "p010le" } else { "nv12" };
    let mut a = args(&[
        "-hide_banner",
        "-nostdin",
        "-v",
        "warning",
        "-xerror",
        "-n",
        "-init_hw_device",
        &format!("vaapi=petal:{}", s.device),
        "-filter_hw_device",
        "petal",
        "-filter_threads",
        "2",
    ]);
    if gpu {
        ensure!(m.rotation() == 0., "Rotated inputs use software decoding");
        a.extend(args(&[
            "-hwaccel",
            "vaapi",
            "-hwaccel_device",
            "petal",
            "-hwaccel_output_format",
            "vaapi",
        ]));
    } else {
        a.extend(args(&["-threads:v:0", "4"]));
    }
    if let Some((start, _)) = sample {
        a.extend(args(&["-ss", &start.to_string()]));
    }
    a.extend(args(&["-protocol_whitelist", "file,pipe", "-i"]));
    a.push(m.path.clone().into_os_string());
    if let Some((_, length)) = sample {
        a.extend(args(&["-t", &length.to_string()]));
    }
    for st in &p.streams {
        a.extend(args(&[
            "-map",
            &format!("0:{}", st["index"].as_u64().unwrap()),
        ]));
    }
    a.extend(args(&["-map_metadata", "0", "-map_chapters", "0"]));
    for (i, st) in p.streams.iter().enumerate() {
        let id = st["index"].as_u64().unwrap();
        if let Some(codec) = p.subtitles.get(&id) {
            a.extend(args(&[&format!("-c:{i}"), codec]));
        } else if st["codec_type"] != "video" && !(st["codec_type"] == "audio" && s.audio == "opus")
        {
            a.extend(args(&[&format!("-c:{i}"), "copy"]));
        }
    }
    for (k, v) in &p.metadata {
        a.extend(args(&["-metadata", &format!("{k}={v}")]));
    }
    let attachment_start = p
        .streams
        .iter()
        .filter(|st| st["codec_type"] == "attachment")
        .count();
    for (offset, cover) in p.artwork.iter().enumerate() {
        let at = attachment_start + offset;
        let id = cover["index"].as_u64().unwrap();
        let path = art.get(&id).context("Cover art was not prepared")?;
        let (mime, ext) = if cover["codec_name"] == "mjpeg" {
            ("image/jpeg", "jpg")
        } else {
            ("image/png", "png")
        };
        a.push("-attach".into());
        a.push(path.into());
        a.extend(args(&[
            &format!("-metadata:s:t:{at}"),
            &format!("mimetype={mime}"),
            &format!("-metadata:s:t:{at}"),
            &format!("filename=cover-{id}.{ext}"),
        ]));
    }
    let mut filter = if gpu {
        format!("scale_vaapi=w={w}:h={h}:format={fmt},setsar={numerator}/1000000:max=1000000")
    } else {
        format!(
            "scale={w}:{h}:flags=lanczos{},setsar={numerator}/1000000:max=1000000,format={fmt},hwupload",
            scale_color(m)
        )
    };
    let frame = colors(m, true, true);
    if !frame.is_empty() {
        filter = format!("setparams={frame},{filter},setparams={frame}")
    };
    let quality = s.quality((w, h)).to_string();
    a.extend(args(&[
        "-filter:v:0",
        &filter,
        "-c:v:0",
        &s.codec,
        "-rc_mode:v:0",
        "CQP",
        if s.codec == "av1_vaapi" {
            "-global_quality:v:0"
        } else {
            "-qp:v:0"
        },
        &quality,
        "-noautoscale:v:0",
    ]));
    let bits = colors(m, s.codec == "av1_vaapi", false);
    if !bits.is_empty() {
        a.extend(args(&[
            "-bsf:v:0",
            &format!(
                "{}={bits}",
                if s.codec == "av1_vaapi" {
                    "av1_metadata"
                } else {
                    "hevc_metadata"
                }
            ),
        ]));
    }
    let fps = ratio(m.video()["avg_frame_rate"].as_str().unwrap_or("0/1"));
    let gop = if fps.is_finite() && fps > 0. {
        (fps * 4.).round().clamp(12., 1200.) as u32
    } else {
        240
    };
    a.extend(args(&[
        "-g:v:0",
        &gop.to_string(),
        "-fps_mode:v:0",
        "passthrough",
        "-enc_time_base:v:0",
        "filter",
        "-metadata:s:v:0",
        "rotate=0",
    ]));
    if s.audio == "opus" {
        for (i, st) in p
            .streams
            .iter()
            .filter(|st| st["codec_type"] == "audio")
            .enumerate()
        {
            let ch = st["channels"].as_u64().unwrap_or(2);
            let bitrate = if ch == 1 {
                96
            } else if ch == 2 {
                160
            } else {
                512.min(ch * 64)
            };
            a.extend(args(&[
                &format!("-c:a:{i}"),
                "libopus",
                &format!("-b:a:{i}"),
                &format!("{bitrate}k"),
                &format!("-vbr:a:{i}"),
                "on",
            ]));
        }
    }
    a.extend(args(&[
        "-max_muxing_queue_size",
        "4096",
        "-progress",
        "pipe:1",
        "-nostats",
        "-f",
        "matroska",
    ]));
    a.push(out.into());
    Ok(a)
}
pub fn decoded(m: &Media, r: &Runner) -> Result<BTreeSet<(u32, u32)>> {
    let mut a = args(&[
        "-v",
        "error",
        "-protocol_whitelist",
        "file,pipe",
        "-select_streams",
        &m.video()["index"].to_string(),
        "-read_intervals",
        "%+#8",
        "-show_frames",
        "-show_entries",
        "frame=width,height",
        "-of",
        "json",
    ]);
    a.push(m.path.clone().into());
    let data: Value =
        serde_json::from_str(&r.run("ffprobe", &a, Some(Duration::from_secs(60)))?.stdout)?;
    Ok(data["frames"]
        .as_array()
        .context("No decoded frames")?
        .iter()
        .filter_map(|f| Some((f["width"].as_u64()? as u32, f["height"].as_u64()? as u32)))
        .collect())
}
pub fn timeline(m: &Media, r: &Runner, empty: &BTreeSet<u64>) -> Result<BTreeMap<u64, (f64, f64)>> {
    let mut a = args(&[
        "-v",
        "warning",
        "-protocol_whitelist",
        "file,pipe",
        "-show_packets",
        "-show_entries",
        "packet=stream_index,pts_time,duration_time,size",
        "-of",
        "compact=p=0",
    ]);
    a.push(m.path.clone().into());
    let mut spans = BTreeMap::new();
    r.stream("ffprobe", &a, None, false, |line| {
        let fields: BTreeMap<_, _> = line.split('|').filter_map(|p| p.split_once('=')).collect();
        let id = fields
            .get("stream_index")
            .and_then(|x| x.parse::<u64>().ok());
        let pts = fields.get("pts_time").and_then(|x| x.parse::<f64>().ok());
        if let (Some(id), Some(pts)) = (id, pts) {
            if empty.contains(&id)
                && fields
                    .get("size")
                    .and_then(|x| x.parse::<u64>().ok())
                    .unwrap_or(0)
                    <= 2
            {
                return;
            }
            let dur = fields
                .get("duration_time")
                .and_then(|x| x.parse::<f64>().ok())
                .unwrap_or(0.)
                .max(0.);
            if (pts + dur).is_finite() {
                let e = spans.entry(id).or_insert((pts, pts + dur));
                e.0 = e.0.min(pts);
                e.1 = e.1.max(pts + dur);
            }
        }
    })?;
    Ok(spans)
}
pub fn decode(path: &Path, r: &Runner, window: Option<(f64, f64)>) -> Result<()> {
    let mut a = args(&[
        "-hide_banner",
        "-nostdin",
        "-v",
        "warning",
        "-xerror",
        "-err_detect",
        "explode",
        "-threads:v:0",
        "4",
        "-threads:a",
        "2",
        "-filter_threads",
        "1",
    ]);
    if let Some((start, _)) = window {
        a.extend(args(&["-ss", &start.to_string()]));
    }
    a.extend(args(&["-protocol_whitelist", "file,pipe", "-i"]));
    a.push(path.into());
    a.extend(args(&["-map", "0:V", "-map", "0:a?", "-sn", "-dn"]));
    if let Some((_, length)) = window {
        a.extend(args(&["-t", &length.to_string()]));
    }
    a.extend(args(&["-f", "null", "-"]));
    r.run("ffmpeg", &a, None)?;
    Ok(())
}
fn kind(s: &Value) -> &str {
    if s["disposition"]["attached_pic"] == 1
        || (s["codec_type"] == "attachment"
            && s["tags"]["mimetype"]
                .as_str()
                .unwrap_or("")
                .starts_with("image/"))
    {
        "artwork"
    } else {
        s["codec_type"].as_str().unwrap_or("")
    }
}
fn tag_field<'a>(v: &'a Value, key: &str) -> Option<&'a Value> {
    v.as_object()?
        .iter()
        .find(|(k, _)| k.eq_ignore_ascii_case(key))
        .map(|(_, v)| v)
}
pub fn verify(
    m: &Media,
    path: &Path,
    s: &Settings,
    r: &Runner,
    metadata_only: bool,
    sample: Option<f64>,
) -> Result<Media> {
    let out = probe(path, r)?;
    let p = plan(m, s)?;
    let expected = p.streams.iter().chain(p.artwork.iter()).collect::<Vec<_>>();
    ensure!(
        out.videos().len() == 1
            && out.video()["codec_name"]
                == if s.codec == "av1_vaapi" {
                    "av1"
                } else {
                    "hevc"
                },
        "Compressed video is missing or uses the wrong format"
    );
    let size = target(m, s)?;
    ensure!(
        (
            out.video()["width"].as_u64().unwrap_or(0) as u32,
            out.video()["height"].as_u64().unwrap_or(0) as u32
        ) == size,
        "Compressed picture size differs from the requested size"
    );
    let decoded = decoded(&out, r)?;
    ensure!(
        !decoded.is_empty() && decoded.iter().all(|x| *x == size),
        "Decoded picture size differs from its stored size; original kept"
    );
    let (a, b) = (display(m)?, display(&out)?);
    ensure!(
        ((a.0 / a.1) / (b.0 / b.1) - 1.).abs() <= 0.002 && out.rotation() == 0.,
        "Framing or rotation changed"
    );
    ensure!(m.bits() == out.bits(), "Picture precision changed");
    ensure!(
        expected.len() == out.streams.len(),
        "A track was lost or added"
    );
    for (before, after) in expected.iter().zip(&out.streams) {
        ensure!(kind(before) == kind(after), "Track order or type changed");
        if kind(before) == "artwork" {
            continue;
        }
        if kind(before) != "video" && !(kind(before) == "audio" && s.audio == "opus") {
            let id = before["index"].as_u64().unwrap();
            let want = p
                .subtitles
                .get(&id)
                .map(String::as_str)
                .unwrap_or(before["codec_name"].as_str().unwrap_or(""));
            ensure!(after["codec_name"] == want, "A copied track format changed")
        };
        for tag in ["language", "title", "filename", "mimetype"] {
            if let Some(v) = tag_value(&before["tags"], tag)
                && v != "und"
                && !v.is_empty()
            {
                ensure!(
                    tag_value(&after["tags"], tag) == Some(v),
                    "Track {tag} changed"
                )
            }
        }
        for flag in [
            "default",
            "forced",
            "hearing_impaired",
            "visual_impaired",
            "comment",
        ] {
            ensure!(
                before["disposition"][flag].as_i64().unwrap_or(0)
                    == after["disposition"][flag].as_i64().unwrap_or(0),
                "Track {flag} flag changed"
            )
        }
        if kind(before) == "audio" {
            ensure!(
                before["channels"] == after["channels"],
                "Audio channel count changed"
            )
        }
    }
    for key in [
        "color_primaries",
        "color_transfer",
        "color_space",
        "color_range",
    ] {
        if let Some(v) = m.video()[key].as_str()
            && !["unknown", "unspecified", ""].contains(&v)
        {
            ensure!(out.video()[key] == v, "Picture {key} changed")
        }
    }
    if sample.is_none() {
        ensure!(
            m.chapters.len() == out.chapters.len(),
            "Chapter count changed"
        );
        for (a, b) in m.chapters.iter().zip(&out.chapters) {
            for k in ["start_time", "end_time"] {
                if let (Some(a), Some(b)) = (number(&a[k]), number(&b[k])) {
                    ensure!((a - b).abs() < 0.05, "Chapter timing changed")
                }
            }
            ensure!(a["tags"] == b["tags"], "Chapter labels changed")
        }
    }
    for tag in ["title", "artist", "comment", "description", "copyright"] {
        if let Some(v) = tag_value(&m.format["tags"], tag) {
            ensure!(
                tag_value(&out.format["tags"], tag) == Some(v),
                "Video {tag} changed"
            )
        }
    }
    for (key, value) in &p.metadata {
        ensure!(
            out.format["tags"].as_object().is_some_and(|t| t
                .iter()
                .any(|(k, v)| k.eq_ignore_ascii_case(key) && v == value)),
            "Timecode metadata changed"
        )
    }
    let duration = sample.or_else(|| m.duration());
    if let Some(d) = duration {
        ensure!(
            out.duration()
                .is_some_and(|o| (o - d).abs() <= 0.35_f64.max((d * 0.005).min(2.))),
            "Video duration changed"
        )
    };
    if metadata_only {
        return Ok(out);
    }
    if sample.is_none() {
        let empty = p
            .streams
            .iter()
            .filter(|st| {
                st["codec_name"] == "mov_text"
                    && p.subtitles.contains_key(&st["index"].as_u64().unwrap())
            })
            .map(|st| st["index"].as_u64().unwrap())
            .collect();
        let src = timeline(m, r, &empty)?;
        let dst = timeline(&out, r, &BTreeSet::new())?;
        let sv = src
            .get(&m.video()["index"].as_u64().unwrap())
            .context("No original video timestamps")?;
        let ov = dst
            .get(&out.video()["index"].as_u64().unwrap())
            .context("No compressed video timestamps")?;
        for (before, after) in expected.iter().zip(&out.streams) {
            if !["video", "audio", "subtitle"].contains(&kind(before)) {
                continue;
            }
            if let Some(a) = src.get(&before["index"].as_u64().unwrap()) {
                let b = dst
                    .get(&after["index"].as_u64().unwrap())
                    .context("A track has no output packets")?;
                ensure!(
                    ((a.0 - sv.0) - (b.0 - ov.0)).abs() <= 0.15
                        && ((a.1 - sv.0) - (b.1 - ov.0)).abs() <= 0.15,
                    "Track timing changed or video was truncated"
                )
            }
        }
    }
    if s.strict || duration.is_none() {
        decode(path, r, None)?
    } else {
        let d = out.duration().context("Unknown output duration")?;
        let mut windows = vec![
            (0., 8_f64.min(d)),
            ((d / 2. - 2.).max(0.), (d / 2. + 6.).min(d)),
            ((d - 8.).max(0.), d),
        ];
        windows.sort_by(|a, b| a.0.total_cmp(&b.0));
        let mut merged: Vec<(f64, f64)> = Vec::new();
        for (a, b) in windows {
            if let Some(last) = merged.last_mut()
                && a <= last.1
            {
                last.1 = last.1.max(b);
                continue;
            }
            merged.push((a, b));
        }
        for (a, b) in merged {
            decode(path, r, Some((a, b - a)))?;
        }
    }
    Ok(out)
}
pub fn quality(m: &Media, path: &Path, r: &Runner) -> Result<Value> {
    let source_key = crate::files::fingerprint(&m.path)?;
    let output_key = crate::files::fingerprint(path)?;
    let out = probe(path, r)?;
    let size = (
        out.video()["width"].as_u64().context("Missing width")? as u32,
        out.video()["height"].as_u64().context("Missing height")? as u32,
    );
    ensure!(
        decoded(&out, r)?.iter().all(|x| *x == size),
        "Frame size needs attention; score unavailable"
    );
    let d = m
        .duration()
        .zip(out.duration())
        .map(|(a, b)| a.min(b))
        .context("Picture checks need a known duration")?;
    ensure!(
        d >= 2. && !m.hdr(),
        "Picture check needs a longer SDR video"
    );
    let starts = if d < 12. {
        vec![(d / 2. - 1.).max(0.)]
    } else {
        vec![d * 0.15, d * 0.48, d * 0.81]
    };
    let model = if size.0 as u64 * size.1 as u64 > 2560 * 1440 {
        "vmaf_4k_v0.6.1neg"
    } else {
        "vmaf_v0.6.1neg"
    };
    let pix = if m.bits() == 10 {
        "yuv420p10le"
    } else {
        "yuv420p"
    };
    let mut all = Vec::new();
    let mut scenes = Vec::new();
    for start in starts {
        let mut a = args(&[
            "-hide_banner",
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-filter_complex_threads",
            "2",
        ]);
        for media in [m, &out] {
            let origin = number(&media.format["start_time"]).unwrap_or(0.);
            let offset = (number(&media.video()["start_time"]).unwrap_or(origin) - origin).max(0.);
            a.extend(args(&[
                "-ss",
                &format!("{:.6}", start + offset),
                "-t",
                "2",
                "-threads:v:0",
                "2",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
            ]));
            a.push(media.path.clone().into());
        }
        let graph = format!(
            "[0:{}]scale={}:{}:flags=lanczos{},format={pix},setsar=1,settb=AVTB[ref];[1:{}]scale={}:{}:flags=lanczos{},format={pix},setsar=1,settb=AVTB[dist];[dist][ref]libvmaf=model=version={model}:log_fmt=json:log_path=/dev/stdout:n_threads=2:shortest=1:repeatlast=0:ts_sync_mode=nearest[q]",
            m.video()["index"],
            size.0,
            size.1,
            scale_color(m),
            out.video()["index"],
            size.0,
            size.1,
            scale_color(&out)
        );
        a.extend(args(&[
            "-filter_complex",
            &graph,
            "-map",
            "[q]",
            "-an",
            "-sn",
            "-dn",
            "-f",
            "null",
            "/dev/null",
        ]));
        let v: Value =
            serde_json::from_str(&r.run("ffmpeg", &a, Some(Duration::from_secs(180)))?.stdout)?;
        let mut scores = v["frames"]
            .as_array()
            .context("No picture scores")?
            .iter()
            .filter_map(|f| f["metrics"]["vmaf"].as_f64())
            .collect::<Vec<_>>();
        ensure!(
            scores.len() >= 8 && scores.iter().all(|x| x.is_finite()),
            "Too few matching frames"
        );
        let mean = scores.iter().sum::<f64>() / scores.len() as f64;
        scores.sort_by(f64::total_cmp);
        let p10 = scores[((scores.len() - 1) as f64 * 0.1) as usize];
        scenes.push(json!({"start":start,"duration":2.,"mean":mean,"p10":p10}));
        all.extend(scores);
    }
    ensure!(
        crate::files::fingerprint(&m.path)? == source_key
            && crate::files::fingerprint(path)? == output_key,
        "A video changed during picture checks; scores discarded"
    );
    let mean = all.iter().sum::<f64>() / all.len() as f64;
    all.sort_by(f64::total_cmp);
    let p10 = all[((all.len() - 1) as f64 * 0.1) as usize];
    let worst = scenes
        .iter()
        .filter_map(|s| s["mean"].as_f64())
        .fold(f64::INFINITY, f64::min);
    Ok(
        json!({"status":if mean<90.||p10<80.||worst<85.{"Review suggested"}else{"No concern in sampled scenes"},"mean":mean,"p10":p10,"worst_scene":worst,"scenes":scenes,"model":model,"resolution":[size.0,size.1]}),
    )
}

fn tag_value<'a>(v: &'a Value, k: &str) -> Option<&'a str> {
    tag_field(v, k)?.as_str()
}
