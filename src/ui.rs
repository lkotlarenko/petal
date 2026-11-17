//! Native Rust desktop UI. Media/file work is always dispatched off the UI thread.
use crate::{engine, files, media, model::*, process::Runner};
use eframe::egui::{self, Color32, RichText, Vec2};
use std::{
    collections::{BTreeSet, VecDeque},
    path::PathBuf,
    sync::mpsc,
    thread,
    time::{Duration, Instant},
};
const PINK: Color32 = Color32::from_rgb(237, 171, 198);
const GREEN: Color32 = Color32::from_rgb(177, 221, 198);
const AMBER: Color32 = Color32::from_rgb(242, 192, 131);
const RED: Color32 = Color32::from_rgb(239, 161, 170);
#[derive(Clone)]
enum Action {
    Move(String, Vec<Job>),
    Trash(Vec<Job>),
}
pub struct App {
    pub state: Library,
    state_dir: PathBuf,
    rx: mpsc::Receiver<Event>,
    tx: mpsc::Sender<Event>,
    worker: Option<thread::JoinHandle<()>>,
    control: Control,
    busy: String,
    status: String,
    logs: VecDeque<String>,
    finished: bool,
    query: String,
    filter: usize,
    selected: BTreeSet<PathBuf>,
    settings_open: bool,
    report_open: bool,
    quality_open: Option<Job>,
    details: Option<Job>,
    log_open: bool,
    confirm: Option<Action>,
    closing: bool,
    report_sources: Vec<PathBuf>,
    smoke: bool,
    frames: u32,
    started: Instant,
    screenshot_requested: bool,
    notice: String,
}
impl App {
    pub fn new(cc: &eframe::CreationContext<'_>, demo: bool, smoke: bool) -> Self {
        let mut style = (*cc.egui_ctx.style()).clone();
        style.visuals = egui::Visuals::dark();
        style.visuals.panel_fill = Color32::from_rgb(30, 31, 37);
        style.visuals.window_fill = Color32::from_rgb(35, 36, 43);
        style.visuals.widgets.noninteractive.bg_fill = Color32::from_rgb(40, 41, 49);
        style.visuals.widgets.inactive.bg_fill = Color32::from_rgb(49, 50, 60);
        style.visuals.selection.bg_fill = Color32::from_rgb(92, 63, 80);
        style.spacing.item_spacing = Vec2::new(10., 10.);
        style.spacing.button_padding = Vec2::new(14., 9.);
        style
            .text_styles
            .insert(egui::TextStyle::Body, egui::FontId::proportional(15.));
        style
            .text_styles
            .insert(egui::TextStyle::Button, egui::FontId::proportional(14.));
        cc.egui_ctx.set_style(style);
        let state_dir = files::xdg("state");
        let (mut state, notice) = match Library::load(&state_dir) {
            Ok(s) => (s, String::new()),
            Err(e) => (
                Library::default(),
                format!("Saved history could not be read and was left untouched: {e}"),
            ),
        };
        for j in &mut state.jobs {
            if matches!(j.status, Status::Encoding | Status::Verifying) {
                j.status = Status::Cancelled;
                j.message = "Interrupted; ready to resume".into();
            }
        }
        if demo {
            state = demo_state();
        }
        let (tx, rx) = mpsc::channel();
        let report_sources = state.report.iter().map(|j| j.source.clone()).collect();
        Self {
            state,
            state_dir,
            rx,
            tx,
            worker: None,
            control: Control::default(),
            busy: String::new(),
            status: "Ready when you are".into(),
            logs: VecDeque::new(),
            finished: demo,
            query: String::new(),
            filter: 0,
            selected: BTreeSet::new(),
            settings_open: false,
            report_open: demo && std::env::var_os("PETAL_DEMO_REPORT").is_some(),
            quality_open: None,
            details: None,
            log_open: false,
            confirm: None,
            closing: false,
            report_sources,
            smoke: smoke || demo,
            frames: 0,
            started: Instant::now(),
            screenshot_requested: false,
            notice,
        }
    }
    fn save(&mut self) {
        if self.smoke {
            return;
        }
        if let Err(e) = self.state.save(&self.state_dir) {
            self.notice = format!("Could not save the list: {e}");
        }
    }
    fn spawn(
        &mut self,
        kind: &str,
        ctx: &egui::Context,
        op: impl FnOnce(Runner, Events) -> anyhow::Result<()> + Send + 'static,
    ) {
        if self.worker.is_some() {
            return;
        }
        if self.smoke {
            self.notice = "Demo only · open Petal normally to process files".into();
            return;
        }
        self.control = Control::default();
        let r = Runner {
            control: self.control.clone(),
            ..Runner::default()
        };
        let ev = Events {
            tx: self.tx.clone(),
            ctx: Some(ctx.clone()),
        };
        self.busy = kind.into();
        self.status = kind.into();
        self.worker = Some(thread::spawn(move || {
            let result = op(r, ev.clone());
            ev.send(Event::Done(match result {
                Ok(()) => "Finished safely".into(),
                Err(e) => format!("{e:#}"),
            }));
        }));
    }
    fn events(&mut self, ctx: &egui::Context) {
        let mut dirty = false;
        while let Ok(event) = self.rx.try_recv() {
            match event {
                Event::Applied(s) => {
                    self.state.settings = s;
                    dirty = true;
                }
                Event::Log(s) => {
                    self.status = s.clone();
                    self.logs.push_back(s);
                    while self.logs.len() > 2000 {
                        self.logs.pop_front();
                    }
                }
                Event::Job(j) => {
                    let j = *j;
                    if let Some(row) = self.state.jobs.iter_mut().find(|r| r.source == j.source) {
                        *row = j.clone();
                    } else {
                        self.state.jobs.push(j.clone());
                    }
                    if let Some(row) = self.state.report.iter_mut().find(|r| r.source == j.source) {
                        *row = j;
                    }
                    dirty = true
                }
                Event::Progress(p, v, message) => {
                    if let Some(j) = self.state.jobs.iter_mut().find(|j| j.source == p) {
                        j.progress = v;
                    }
                    self.status = format!(
                        "{message} · {} · {:.0}%",
                        p.file_name().unwrap_or_default().to_string_lossy(),
                        v * 100.
                    );
                }
                Event::Scanned(jobs, names) => {
                    let sources = jobs
                        .iter()
                        .map(|j| j.source.clone())
                        .collect::<BTreeSet<_>>();
                    self.state
                        .jobs
                        .retain(|j| j.status.finished() && !sources.contains(&j.source));
                    self.state.jobs.extend(jobs.clone());
                    self.state.report = jobs;
                    self.state.report_output = Some(PathBuf::from(&self.state.output));
                    self.state.namespaces = names;
                    self.report_sources =
                        self.state.report.iter().map(|j| j.source.clone()).collect();
                    dirty = true
                }
                Event::Hardware(s) => self.notice = s,
                Event::Done(s) => {
                    self.status = s.clone();
                    self.logs.push_back(s);
                }
            }
        }
        if self.worker.as_ref().is_some_and(|w| w.is_finished()) {
            let _ = self.worker.take().unwrap().join();
            if self.busy == "Compressing videos" {
                self.state.report = self
                    .state
                    .jobs
                    .iter()
                    .filter(|j| self.report_sources.contains(&j.source))
                    .cloned()
                    .collect();
                self.state.report_output = Some(PathBuf::from(&self.state.output));
                self.report_open = true;
            }
            self.busy.clear();
            dirty = true;
            if self.closing {
                ctx.send_viewport_cmd(egui::ViewportCommand::Close);
            }
        }
        if dirty {
            self.save()
        }
        if self.worker.is_some() {
            ctx.request_repaint_after(Duration::from_millis(100));
        }
    }
    fn scan(&mut self, ctx: &egui::Context) {
        if self.state.output.trim().is_empty() {
            self.notice = "Choose an output folder first".into();
            return;
        }
        let inputs = if self.state.inputs.is_empty() {
            vec![PathBuf::from(&self.state.source)]
        } else {
            self.state.inputs.clone()
        };
        let output = PathBuf::from(&self.state.output);
        let old = self.state.jobs.clone();
        let names = self.state.namespaces.clone();
        let s = self.state.settings.clone();
        let state = self.state_dir.clone();
        self.save();
        self.spawn("Finding videos", ctx, move |r, ev| {
            let (jobs, names) = engine::scan(&inputs, &output, &state, &s, &old, &names, &r, &ev)?;
            ev.send(Event::Scanned(jobs, names));
            Ok(())
        });
    }
    fn start(&mut self, ctx: &egui::Context) {
        if self.state.output.is_empty() {
            self.notice = "Choose an output folder first".into();
            return;
        }
        let output = PathBuf::from(&self.state.output);
        let jobs = self
            .state
            .jobs
            .iter()
            .filter(|j| {
                matches!(j.status, Status::Waiting | Status::Cancelled)
                    && !j.trashed
                    && j.relocated_to.is_none()
            })
            .cloned()
            .collect::<Vec<_>>();
        if jobs.is_empty() {
            self.notice = "Add or return videos to Jobs first".into();
            return;
        }
        self.report_sources = self
            .state
            .report
            .iter()
            .map(|j| j.source.clone())
            .chain(jobs.iter().map(|j| j.source.clone()))
            .collect();
        let s = self.state.settings.clone();
        let dir = self.state_dir.clone();
        self.spawn("Compressing videos", ctx, move |r, ev| {
            engine::batch(jobs, &output, &dir, &s, &r, &ev)
        });
    }
    fn selected_jobs(&self) -> Vec<Job> {
        self.state
            .jobs
            .iter()
            .filter(|j| self.selected.contains(&j.source))
            .cloned()
            .collect()
    }
    fn open(path: &std::path::Path) {
        let _ = std::process::Command::new("xdg-open").arg(path).spawn();
    }
    fn requeue(&mut self, source: &std::path::Path) {
        if let Some(j) = self.state.jobs.iter_mut().find(|j| j.source == source)
            && !j.trashed
            && j.relocated_to.is_none()
            && j.source.is_file()
        {
            j.status = Status::Waiting;
            j.progress = 0.;
            j.message = "Ready to try again".into();
        }
        self.finished = false;
        self.save();
    }
    fn action(&mut self, action: Action, ctx: &egui::Context) {
        let state = self.state_dir.clone();
        let s = self.state.settings.clone();
        match action {
            Action::Move(category, jobs) => {
                let roots = jobs
                    .iter()
                    .filter_map(|j| j.output_root.clone().or(self.state.report_output.clone()))
                    .collect::<BTreeSet<_>>();
                if roots.len() != 1 {
                    self.notice = "Choose files from one output folder at a time".into();
                    return;
                }
                let root = roots.into_iter().next().unwrap();
                self.spawn("Moving originals", ctx, move |r, ev| {
                    files::organize(&jobs, &root, &state, &category, &r, &ev)
                });
            }
            Action::Trash(jobs) => {
                self.spawn("Moving originals to Trash", ctx, move |r, ev| {
                    for mut j in jobs {
                        r.control.check()?;
                        ev.log(format!(
                            "Checking {} before moving its original to Trash",
                            j.relative.display()
                        ));
                        let result = (|| -> anyhow::Result<()> {
                            let root = j.output_root.clone().context("Missing output folder")?;
                            let _lock = files::OutputLock::acquire(&root)?;
                            engine::check_existing(&mut j, &state, &s, &r)?;
                            files::trash_original(&mut j, &r)?;
                            Ok(())
                        })();
                        if let Err(e) = result {
                            j.relocation_error = format!("Original kept: {e}");
                            ev.log(j.relocation_error.clone());
                        }
                        ev.send(Event::Job(Box::new(j)));
                    }
                    Ok(())
                });
            }
        }
    }
    fn settings(&mut self, ctx: &egui::Context) {
        if !self.settings_open {
            return;
        }
        let mut open = true;
        egui::Window::new("Compression settings").open(&mut open).default_width(550.).resizable(false).show(ctx,|ui|{
ui.label("Apply these choices to the next batch. Nothing is sent online.");
ui.separator();
ui.add_enabled_ui(self.worker.is_none(),|ui|{
egui::Grid::new("settingsgrid").num_columns(2).spacing([20.,14.]).show(ui,|ui|{
ui.label("Video format");
egui::ComboBox::from_id_salt("codec").selected_text(if self.state.settings.codec=="av1_vaapi"{
"AV1 · smaller files"}
else{
"H.265 · broad compatibility"}
).show_ui(ui,|ui|{
ui.selectable_value(&mut self.state.settings.codec,"av1_vaapi".into(),"AV1");
ui.selectable_value(&mut self.state.settings.codec,"hevc_vaapi".into(),"H.265");
}
);
ui.end_row();
ui.label("Graphics device");
ui.text_edit_singleline(&mut self.state.settings.device);
ui.end_row();
ui.label("Verification");
ui.checkbox(&mut self.state.settings.strict,"Thorough · recommended").on_hover_text("Check every decoded frame. Uncheck to check the beginning, middle and end; track timing is still checked.");
ui.end_row();
ui.label("Picture checks");
ui.checkbox(&mut self.state.settings.auto_quality,"Check scenes in the first 3 new videos");
ui.end_row();
ui.label("Minimum saving");
ui.add(egui::DragValue::new(&mut self.state.settings.min_savings).range(0. ..=99.).suffix(" %"));
ui.end_row();
ui.label("Audio");
egui::ComboBox::from_id_salt("audio").selected_text(if self.state.settings.audio=="copy"{
"Keep original audio"}
else{
"Opus"}
).show_ui(ui,|ui|{
ui.selectable_value(&mut self.state.settings.audio,"copy".into(),"Keep original audio");
ui.selectable_value(&mut self.state.settings.audio,"opus".into(),"Opus · smaller audio");
}
);
ui.end_row();
ui.label("Extra tracks");
egui::ComboBox::from_id_salt("tracks").selected_text(match self.state.settings.incompatible.as_str(){
"skip"=>"Keep exactly, or skip","omit"=>"Leave unsupported tracks out",_=>"Keep compatible tracks"}
).show_ui(ui,|ui|{
ui.selectable_value(&mut self.state.settings.incompatible,"compatible".into(),"Keep / convert compatible tracks");
ui.selectable_value(&mut self.state.settings.incompatible,"skip".into(),"Keep exactly, or skip");
ui.selectable_value(&mut self.state.settings.incompatible,"omit".into(),"Explicitly leave unsupported tracks out");
}
);
ui.end_row();
}
);
ui.separator();
if ui.button("Check graphics support").clicked(){
let s=self.state.settings.clone();
let state=self.state_dir.clone();
self.spawn("Checking graphics support",ctx,move|r,ev|{
let passed=engine::doctor(&s,&state,&r,&ev,true)?;
ev.send(Event::Hardware(format!("Ready · checked {} · {}-bit",s.profile,passed.iter().map(u32::to_string).collect::<Vec<_>>().join(" / "))));
Ok(())}
);
}
if ui.button("Compare samples of selected video…").clicked(){
let selected=self.selected_jobs();
if selected.len()!=1{
self.notice="Select one video first".into();
}
else if let Some(dir)=rfd::FileDialog::new().set_title("Save comparison samples outside the source folder").pick_folder(){
let job=selected[0].clone();
let s=self.state.settings.clone();
let state=self.state_dir.clone();
self.spawn("Making comparison samples",ctx,move|r,ev|engine::samples(&job,&dir,&state,&s,&r,&ev));
}
}
if ui.button("Add AppImage to applications").clicked(){
self.notice=match install_desktop(){
Ok(p)=>format!("Added to applications · {}",p.display()),Err(e)=>e.to_string()}
;
}
if ui.button("Open logs and saved results").clicked(){
Self::open(&self.state_dir);
}
}
);
}
);

        self.settings_open = open;
    }
    fn report(&mut self, ctx: &egui::Context) {
        if !self.report_open {
            return;
        }
        let mut open = true;
        egui::Window::new("Your batch report").open(&mut open).default_width(660.).resizable(false).show(ctx,|ui|{
let sums=summary(&self.state.report);
ui.label(RichText::new("YOUR BATCH, AT A GLANCE").small().weak());
ui.heading("A little lighter.");
ui.add_space(8.);
ui.label(RichText::new(format!("{} saved",size(sums["saved"].as_u64().unwrap_or(0)))).size(42.).color(GREEN));
ui.label(format!("{:.1}% smaller across {} compressed videos · {} unfinished",sums["percent"].as_f64().unwrap_or(0.),sums["completed"],sums["unfinished"]));
ui.add_space(12.);
ui.columns(3,|cols|{
for(i,(key,label,color,filter))in[("completed","Compressed",GREEN,1),("skipped","Skipped",AMBER,2),("failed","Needs attention",RED,3)].into_iter().enumerate(){
egui::Frame::group(cols[i].style()).inner_margin(16.).show(&mut cols[i],|ui|{
ui.label(RichText::new(sums[key].as_u64().unwrap_or(0).to_string()).size(30.).color(color));
if ui.button(label).clicked(){
self.finished=true;
self.filter=filter;
self.query.clear();
self.report_open=false;
}
}
);
}
}
);
ui.add_space(12.);
if let Some(root)=&self.state.report_output{
ui.label(format!("Saved on this computer · {}",root.display()));
}
ui.separator();
ui.strong("Bring the rest together");
ui.label("Keep their folders and filenames. Nothing is uploaded.");
ui.horizontal_wrapped(|ui|{
for(category,status)in[("skipped",Status::Skipped),("failed",Status::Failed)]{
let jobs=self.state.report.iter().filter(|j|j.status==status&&!j.trashed&&j.relocated_to.is_none()).cloned().collect::<Vec<_>>();
if ui.add_enabled(self.worker.is_none()&&!jobs.is_empty(),egui::Button::new(format!("Move {} {category} originals…",jobs.len()))).clicked(){
self.confirm=Some(Action::Move(category.into(),jobs));
}
}
}
);
ui.add_space(12.);
ui.strong("Keep just the smaller copies");
ui.label("Move successfully compressed originals to your desktop Trash. The compressed copies stay in the output folder.");
let jobs=self.state.report.iter().filter(|j|j.status==Status::Completed&&!j.trashed&&(self.smoke||j.source.exists())).cloned().collect::<Vec<_>>();
if ui.add_enabled(self.worker.is_none()&&!jobs.is_empty(),egui::Button::new(format!("Move {} compressed originals to Trash…",jobs.len())).fill(Color32::from_rgb(87,49,59))).clicked(){
self.confirm=Some(Action::Trash(jobs));
}
if !self.busy.is_empty(){
ui.separator();
ui.label(&self.status);
if ui.button("Stop safely").clicked(){
self.control.stop();
}
}
}
);

        if !open {
            self.report_open = false;
        }
    }
    fn confirmation(&mut self, ctx: &egui::Context) {
        let Some(action) = self.confirm.clone() else {
            return;
        };
        let mut go = false;
        let mut cancel = false;
        egui::Modal::new(egui::Id::new("confirm-action")).show(ctx,|ui|{
ui.set_max_width(540.);
match &action{
Action::Move(category,jobs)=>{
ui.heading("Move these originals?");
ui.label(format!("{} files will move into the {category} folder inside their output folder, keeping relative paths. Existing files are never replaced.",jobs.len()));
ui.label("On the same filesystem this is an atomic move. Across filesystems the copied bytes are checked before the original is removed.");
}
,Action::Trash(jobs)=>{
ui.heading("Move originals to Trash?");
ui.colored_label(AMBER,format!("This moves {} original videos to your desktop Trash.",jobs.len()));
ui.label("The smaller compressed copies stay in place. Petal checks that the files still match the successful compression before touching any original.");
ui.label("You can restore originals until you empty Trash. Disk space is only freed after Trash is emptied. Petal never empties Trash or permanently deletes these files.");
}
}
ui.add_space(12.);
ui.horizontal(|ui|{
cancel=ui.button("Keep originals").clicked();
go=ui.button(RichText::new(if matches!(action,Action::Trash(_)){
"Move to Trash"}
else{
"Move originals"}
).color(PINK)).clicked();
}
);
}
);

        if cancel {
            self.confirm = None;
        }
        if go {
            self.confirm = None;
            self.action(action, ctx);
        }
    }
    fn quality_window(&mut self, ctx: &egui::Context) {
        let Some(job) = self.quality_open.clone() else {
            return;
        };
        let mut open = true;
        egui::Window::new("Picture quality").open(&mut open).default_width(620.).show(ctx,|ui|{
ui.heading(job.source.file_name().unwrap_or_default().to_string_lossy());
let q=&job.quality;
if q.get("geometry_note").is_some_and(|v|!v.as_str().unwrap_or("").is_empty())||q["status"]=="Geometry mismatch"{
ui.colored_label(AMBER,"Frame size needs attention. Earlier scores may be misleading.");
}
else if let Some(score)=q["mean"].as_f64(){
let color=if q["status"]=="Review suggested"{
AMBER}
else{
GREEN}
;
ui.label(RichText::new(format!("{score:.0} / 100")).size(46.).color(color));
ui.label(if q["status"]=="Review suggested"{
"Compare these scenes visually"}
else{
"Looks close in the checked scenes"}
);
if let Some(scenes)=q["scenes"].as_array(){
for scene in scenes{
let start=scene["start"].as_f64().unwrap_or(0.);
let score=scene["mean"].as_f64().unwrap_or(0.);
ui.label(format!("{}:{:02} – {}:{:02}",start as u64/60,start as u64%60,(start+2.)as u64/60,(start+2.)as u64%60));
ui.add(egui::ProgressBar::new((score/100.)as f32).text(format!("{score:.0}/100")).fill(color));
}
}
}
else{
ui.label(q["reason"].as_str().unwrap_or("No picture check yet"));
}
ui.label("A similarity score, not a percentage of quality retained. Only short scenes are checked, at the output size; loss from downscaling is separate.");
ui.horizontal(|ui|{
if ui.button("Open original").clicked(){
Self::open(job.relocated_to.as_deref().unwrap_or(&job.source));
}
if ui.button("Open compressed").clicked(){
Self::open(&job.destination);
}
}
);
if ui.add_enabled(self.worker.is_none()&&!job.trashed,egui::Button::new("Recheck this video")).clicked(){
let mut j=job.clone();
let s=self.state.settings.clone();
let state=self.state_dir.clone();
self.spawn("Checking picture quality",ctx,move|r,ev|{
engine::check_existing(&mut j,&state,&s,&r)?;
j.quality=match media::quality(j.media.as_ref().context("Missing video details")?,&j.destination,&r){
Ok(q)=>q,Err(e)=>serde_json::json!({
"status":"Unavailable","reason":e.to_string()}
)}
;
ev.send(Event::Job(Box::new(j)));
Ok(())}
);
}
ui.collapsing("Technical details",|ui|{
ui.monospace(serde_json::to_string_pretty(q).unwrap_or_default());
}
);
}
);

        if !open {
            self.quality_open = None;
        }
    }
}
use anyhow::Context;
impl eframe::App for App {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        self.frames += 1;
        if let Some(path) = std::env::var_os("PETAL_SCREENSHOT") {
            ctx.request_repaint();
            if !self.screenshot_requested && self.started.elapsed() >= Duration::from_millis(500) {
                self.screenshot_requested = true;
                ctx.send_viewport_cmd(egui::ViewportCommand::Screenshot(Default::default()));
            }
            let image = ctx.input(|i| {
                i.events.iter().find_map(|e| {
                    if let egui::Event::Screenshot { image, .. } = e {
                        Some(image.clone())
                    } else {
                        None
                    }
                })
            });
            if let Some(image) = image {
                let pixels = image
                    .pixels
                    .iter()
                    .flat_map(|p| p.to_array())
                    .collect::<Vec<_>>();
                if let Err(e) = image::save_buffer(
                    path,
                    &pixels,
                    image.size[0] as u32,
                    image.size[1] as u32,
                    image::ColorType::Rgba8,
                ) {
                    eprintln!("Screenshot failed: {e}")
                }
                ctx.send_viewport_cmd(egui::ViewportCommand::Close);
            }
        }

        self.events(ctx);
        if self.smoke && std::env::var_os("PETAL_SMOKE").is_some() {
            ctx.request_repaint();
        }
        if crate::process::INTERRUPTED.load(std::sync::atomic::Ordering::Relaxed) {
            self.closing = true;
            self.control.stop();
            if self.worker.is_none() {
                ctx.send_viewport_cmd(egui::ViewportCommand::Close);
            }
        }
        if ctx.input(|i| i.viewport().close_requested()) && self.worker.is_some() {
            ctx.send_viewport_cmd(egui::ViewportCommand::CancelClose);
            self.closing = true;
            self.control.stop();
        }
        if self.smoke && std::env::var_os("PETAL_SMOKE").is_some() && self.frames > 5 {
            ctx.send_viewport_cmd(egui::ViewportCommand::Close);
        }
        let idle = self.worker.is_none();
        let blocked = self.confirm.is_some();
        egui::TopBottomPanel::top("header")
            .frame(
                egui::Frame::new()
                    .fill(Color32::from_rgb(30, 31, 37))
                    .inner_margin(egui::Margin::symmetric(24, 18)),
            )
            .show(ctx, |ui| {
                ui.horizontal(|ui| {
                    ui.heading(RichText::new("✿  Petal").size(32.).color(PINK));
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        if ui.button("Settings").clicked() {
                            self.settings_open = true;
                        }
                        let saved = summary(&self.state.jobs)["saved"].as_u64().unwrap_or(0);
                        ui.label(
                            RichText::new(format!("{} saved", size(saved)))
                                .size(22.)
                                .color(GREEN),
                        );
                    });
                });
                ui.label(
                    RichText::new("Smaller videos. Everything stays on your computer.").weak(),
                );
            });
        egui::TopBottomPanel::bottom("controls")
            .frame(
                egui::Frame::new()
                    .fill(Color32::from_rgb(30, 31, 37))
                    .inner_margin(egui::Margin::symmetric(24, 14)),
            )
            .show(ctx, |ui| {
                if !self.busy.is_empty() {
                    ui.label(&self.status);
                    if let Some(job) = self
                        .state
                        .jobs
                        .iter()
                        .find(|j| j.status == Status::Encoding)
                    {
                        ui.add(
                            egui::ProgressBar::new(job.progress as f32)
                                .fill(PINK)
                                .show_percentage(),
                        );
                    }
                }
                ui.horizontal_wrapped(|ui| {
                    let n = self
                        .state
                        .jobs
                        .iter()
                        .filter(|j| {
                            matches!(j.status, Status::Waiting | Status::Cancelled)
                                && !j.trashed
                                && j.relocated_to.is_none()
                        })
                        .count();
                    if ui
                        .add_enabled(
                            idle && !blocked && n > 0,
                            egui::Button::new(
                                RichText::new(format!(
                                    "Compress {n} {}",
                                    if n == 1 { "video" } else { "videos" }
                                ))
                                .color(Color32::from_rgb(35, 31, 38)),
                            )
                            .fill(PINK),
                        )
                        .clicked()
                    {
                        self.start(ctx);
                    }
                    if ui
                        .add_enabled(
                            !idle && !blocked && self.busy == "Compressing videos",
                            egui::Button::new(if *self.control.pause.0.lock().unwrap() {
                                "Resume"
                            } else {
                                "Pause after current"
                            }),
                        )
                        .clicked()
                    {
                        let paused = *self.control.pause.0.lock().unwrap();
                        self.control.set_pause(!paused);
                    }
                    if ui
                        .add_enabled(!idle, egui::Button::new("Stop safely"))
                        .clicked()
                    {
                        self.control.stop();
                    }
                    if ui.button("Detailed log").clicked() {
                        self.log_open = !self.log_open;
                    }
                    if ui
                        .add_enabled(
                            !self.state.report.is_empty(),
                            egui::Button::new("Batch report"),
                        )
                        .clicked()
                    {
                        self.report_open = true;
                    }
                });
            });
        egui::CentralPanel::default()
            .frame(
                egui::Frame::new()
                    .fill(Color32::from_rgb(30, 31, 37))
                    .inner_margin(egui::Margin::symmetric(24, 8)),
            )
            .show(ctx, |ui| {
                ui.add_enabled_ui(idle && !blocked, |ui| {
                    egui::Frame::group(ui.style())
                        .inner_margin(14.)
                        .show(ui, |ui| {
                            let field_width = (ui.available_width() - 240.).max(180.);
                            ui.horizontal(|ui| {
                                ui.add_sized([104., 24.], egui::Label::new("Source folder"));
                                ui.add_sized(
                                    [field_width, 30.],
                                    egui::TextEdit::singleline(&mut self.state.source),
                                );
                                if ui.button("Choose…").clicked()
                                    && let Some(p) = rfd::FileDialog::new().pick_folder()
                                {
                                    self.state.source = p.to_string_lossy().into();
                                    self.state.inputs.clear();
                                    self.state.namespaces.clear();
                                }
                            });
                            ui.horizontal(|ui| {
                                ui.add_sized([104., 24.], egui::Label::new("Output folder"));
                                ui.add_sized(
                                    [field_width, 30.],
                                    egui::TextEdit::singleline(&mut self.state.output),
                                );
                                if ui.button("Choose…").clicked()
                                    && let Some(p) = rfd::FileDialog::new().pick_folder()
                                {
                                    self.state.output = p.to_string_lossy().into();
                                }
                            });
                            ui.horizontal_wrapped(|ui| {
                                ui.label("Quality");
                                egui::ComboBox::from_id_salt("profile")
                                    .selected_text(&self.state.settings.profile)
                                    .show_ui(ui, |ui| {
                                        for p in ["High Quality", "Balanced", "Maximum Savings"] {
                                            ui.selectable_value(
                                                &mut self.state.settings.profile,
                                                p.into(),
                                                p,
                                            );
                                        }
                                    });
                                ui.label("Picture size");
                                egui::ComboBox::from_id_salt("resolution")
                                    .selected_text(if self.state.settings.resolution == "2160p" {
                                        "4K"
                                    } else {
                                        &self.state.settings.resolution
                                    })
                                    .show_ui(ui, |ui| {
                                        for p in
                                            ["480p", "720p", "1080p", "1440p", "2160p", "original"]
                                        {
                                            ui.selectable_value(
                                                &mut self.state.settings.resolution,
                                                p.into(),
                                                if p == "2160p" {
                                                    "4K"
                                                } else if p == "original" {
                                                    "Keep original size"
                                                } else {
                                                    p
                                                },
                                            );
                                        }
                                    });
                                if ui.button("Find videos").clicked() {
                                    self.scan(ctx);
                                }
                            });
                        });
                });
                ui.add_space(10.);
                if !self.notice.is_empty() {
                    ui.horizontal_wrapped(|ui| {
                        ui.colored_label(AMBER, &self.notice);
                        if ui.small_button("Dismiss").clicked() {
                            self.notice.clear();
                        }
                    });
                }
                ui.horizontal_wrapped(|ui| {
                    if ui
                        .selectable_label(
                            !self.finished,
                            format!(
                                "Jobs  {}",
                                self.state
                                    .jobs
                                    .iter()
                                    .filter(|j| !j.status.finished())
                                    .count()
                            ),
                        )
                        .clicked()
                    {
                        self.finished = false;
                    }
                    if ui
                        .selectable_label(
                            self.finished,
                            format!(
                                "Finished  {}",
                                self.state
                                    .jobs
                                    .iter()
                                    .filter(|j| j.status.finished())
                                    .count()
                            ),
                        )
                        .clicked()
                    {
                        self.finished = true;
                    }
                    if ui
                        .add_enabled(idle && !blocked, egui::Button::new("Add videos…"))
                        .clicked()
                        && let Some(paths) = rfd::FileDialog::new().pick_files()
                    {
                        if self.state.inputs.is_empty() && !self.state.source.is_empty() {
                            self.state.inputs.push(PathBuf::from(&self.state.source));
                            self.state
                                .namespaces
                                .insert(self.state.source.clone(), String::new());
                        }
                        self.state.inputs.extend(paths);
                        self.scan(ctx);
                    }
                    if ui
                        .add_enabled(
                            idle && !self.selected.is_empty() && !blocked,
                            egui::Button::new("Remove from list"),
                        )
                        .clicked()
                    {
                        self.state
                            .jobs
                            .retain(|j| !self.selected.contains(&j.source));
                        self.selected.clear();
                        self.save();
                    }
                    if ui
                        .add_enabled(
                            idle && !self.selected.is_empty() && !blocked,
                            egui::Button::new(if self.finished {
                                "Return to Jobs"
                            } else {
                                "Skip selected"
                            }),
                        )
                        .clicked()
                    {
                        let selected = self.selected.clone();
                        for p in selected {
                            if self.finished {
                                self.requeue(&p);
                            } else if let Some(j) =
                                self.state.jobs.iter_mut().find(|j| j.source == p)
                            {
                                j.status = Status::Skipped;
                                j.message = "Skipped by you".into();
                                if let Some(report) =
                                    self.state.report.iter_mut().find(|r| r.source == j.source)
                                {
                                    *report = j.clone();
                                } else {
                                    self.state.report.push(j.clone());
                                }
                            }
                        }
                        self.save();
                    }
                });
                ui.horizontal(|ui| {
                    ui.add(
                        egui::TextEdit::singleline(&mut self.query)
                            .hint_text("Find a filename, folder or result…")
                            .desired_width((ui.available_width() - 170.).max(120.)),
                    );
                    if self.finished {
                        egui::ComboBox::from_id_salt("resultfilter")
                            .selected_text(
                                ["All results", "Compressed", "Skipped", "Needs attention"]
                                    [self.filter],
                            )
                            .show_ui(ui, |ui| {
                                for (i, s) in
                                    ["All results", "Compressed", "Skipped", "Needs attention"]
                                        .iter()
                                        .enumerate()
                                {
                                    ui.selectable_value(&mut self.filter, i, *s);
                                }
                            });
                    }
                });
                ui.separator();
                ui.horizontal(|ui| {
                    if ui.small_button("Select all shown").clicked() {
                        let q = self.query.to_lowercase();
                        for j in &self.state.jobs {
                            if j.status.finished() == self.finished
                                && (self.filter == 0
                                    || !self.finished
                                    || matches!(
                                        (&j.status, self.filter),
                                        (Status::Completed, 1)
                                            | (Status::Skipped, 2)
                                            | (Status::Failed, 3)
                                    ))
                                && (q.is_empty()
                                    || format!(
                                        "{} {} {:?}",
                                        j.relative.display(),
                                        j.message,
                                        j.status
                                    )
                                    .to_lowercase()
                                    .contains(&q))
                            {
                                self.selected.insert(j.source.clone());
                            }
                        }
                    }
                    if ui.small_button("Clear selection").clicked() {
                        self.selected.clear();
                    }
                    ui.weak(format!("{} selected", self.selected.len()));
                });
                let query = self.query.to_lowercase();
                let rows = self
                    .state
                    .jobs
                    .iter()
                    .enumerate()
                    .filter(|(_, j)| j.status.finished() == self.finished)
                    .filter(|(_, j)| {
                        !self.finished
                            || self.filter == 0
                            || matches!(
                                (&j.status, self.filter),
                                (Status::Completed, 1) | (Status::Skipped, 2) | (Status::Failed, 3)
                            )
                    })
                    .filter(|(_, j)| {
                        query.is_empty()
                            || format!("{} {} {:?}", j.relative.display(), j.message, j.status)
                                .to_lowercase()
                                .contains(&query)
                    })
                    .map(|(i, _)| i)
                    .collect::<Vec<_>>();
                if rows.is_empty() {
                    ui.add_space(55.);
                    ui.vertical_centered(|ui| {
                        ui.heading(if query.is_empty() {
                            if self.finished {
                                "Your results will appear here"
                            } else {
                                "Ready for your next batch"
                            }
                        } else {
                            "No matching videos"
                        });
                        ui.label(if query.is_empty() {
                            "Add videos or drop files and folders into the window."
                        } else {
                            "Try another filename or clear the search."
                        });
                    });
                }
                egui::ScrollArea::vertical()
                    .auto_shrink([false, false])
                    .show_rows(ui, 48., rows.len(), |ui, range| {
                        for i in range {
                            let row = self.state.jobs[rows[i]].clone();
                            let j = &row;
                            let color = match j.status {
                                Status::Completed => GREEN,
                                Status::Skipped => AMBER,
                                Status::Failed => RED,
                                _ => Color32::LIGHT_GRAY,
                            };
                            let response = egui::Frame::new()
                                .fill(if self.selected.contains(&j.source) {
                                    Color32::from_rgb(72, 51, 66)
                                } else if i % 2 == 0 {
                                    Color32::from_rgb(39, 40, 48)
                                } else {
                                    Color32::from_rgb(34, 35, 42)
                                })
                                .inner_margin(8.)
                                .show(ui, |ui| {
                                    ui.horizontal(|ui| {
                                        let mut selected = self.selected.contains(&j.source);
                                        if ui.checkbox(&mut selected, "").changed() {
                                            if selected {
                                                self.selected.insert(j.source.clone());
                                            } else {
                                                self.selected.remove(&j.source);
                                            }
                                        }
                                        ui.vertical(|ui| {
                                            ui.label(
                                                RichText::new(
                                                    j.source
                                                        .file_name()
                                                        .unwrap_or_default()
                                                        .to_string_lossy(),
                                                )
                                                .strong(),
                                            );
                                            ui.label(
                                                RichText::new(
                                                    j.relative
                                                        .parent()
                                                        .unwrap_or(std::path::Path::new(""))
                                                        .to_string_lossy(),
                                                )
                                                .small()
                                                .weak(),
                                            );
                                        });
                                        ui.with_layout(
                                            egui::Layout::right_to_left(egui::Align::Center),
                                            |ui| {
                                                ui.colored_label(
                                                    color,
                                                    if j.trashed {
                                                        "Original in Trash".into()
                                                    } else if j.relocated_to.is_some() {
                                                        "Original moved".into()
                                                    } else {
                                                        format!("{:?}", j.status)
                                                    },
                                                );
                                                ui.label(size(j.size));
                                                if j.status == Status::Completed {
                                                    ui.label(format!("now {}", size(j.final_size)));
                                                }
                                            },
                                        );
                                    });
                                })
                                .response
                                .interact(egui::Sense::click());
                            if response.double_clicked() {
                                Self::open(j.relocated_to.as_deref().unwrap_or(&j.source));
                            }
                            response
                                .on_hover_text(format!(
                                    "{}\n{}\n{}",
                                    j.source.display(),
                                    j.destination.display(),
                                    j.message
                                ))
                                .context_menu(|ui| {
                                    if ui.button("Open original").clicked() {
                                        Self::open(j.relocated_to.as_deref().unwrap_or(&j.source));
                                        ui.close();
                                    }
                                    if ui.button("Open compressed").clicked() {
                                        Self::open(&j.destination);
                                        ui.close();
                                    }
                                    if ui.button("Show folder").clicked() {
                                        Self::open(
                                            j.relocated_to
                                                .as_deref()
                                                .unwrap_or(&j.source)
                                                .parent()
                                                .unwrap(),
                                        );
                                        ui.close();
                                    }
                                    if ui.button("Copy file path").clicked() {
                                        ui.ctx().copy_text(
                                            j.relocated_to
                                                .as_deref()
                                                .unwrap_or(&j.source)
                                                .display()
                                                .to_string(),
                                        );
                                        ui.close();
                                    }
                                    if ui.button("File details").clicked() {
                                        self.details = Some(j.clone());
                                        ui.close();
                                    }
                                    if ui.button("Picture quality").clicked() {
                                        self.quality_open = Some(j.clone());
                                        ui.close();
                                    }
                                    if ui
                                        .add_enabled(
                                            idle && !j.trashed && j.relocated_to.is_none(),
                                            egui::Button::new("Return to Jobs"),
                                        )
                                        .clicked()
                                    {
                                        self.requeue(&j.source);
                                        ui.close();
                                    }
                                    for (kind, status) in
                                        [("skipped", Status::Skipped), ("failed", Status::Failed)]
                                    {
                                        if j.status == status
                                            && j.relocated_to.is_none()
                                            && ui
                                                .add_enabled(
                                                    idle,
                                                    egui::Button::new(format!(
                                                        "Move this {kind} original…"
                                                    )),
                                                )
                                                .clicked()
                                        {
                                            self.confirm =
                                                Some(Action::Move(kind.into(), vec![j.clone()]));
                                            ui.close();
                                        }
                                    }
                                });
                        }
                    });
            });
        if idle && !blocked {
            let dropped = ctx.input(|i| {
                i.raw
                    .dropped_files
                    .iter()
                    .filter_map(|f| f.path.clone())
                    .collect::<Vec<_>>()
            });
            if !dropped.is_empty() {
                if self.state.inputs.is_empty() && !self.state.source.is_empty() {
                    self.state.inputs.push(PathBuf::from(&self.state.source));
                    self.state
                        .namespaces
                        .insert(self.state.source.clone(), String::new());
                }
                self.state.inputs.extend(dropped);
                self.scan(ctx);
            }
        }
        self.settings(ctx);
        self.report(ctx);
        self.quality_window(ctx);
        self.confirmation(ctx);
        if let Some(j) = self.details.clone() {
            let mut open = true;
            egui::Window::new("File details")
                .open(&mut open)
                .default_width(650.)
                .show(ctx, |ui| {
                    ui.label(format!(
                        "{}\n\nStatus: {:?}\n{}\n\nOriginal: {}\nCompressed: {}",
                        j.relative.display(),
                        j.status,
                        j.message,
                        j.source.display(),
                        j.destination.display()
                    ));
                    if let Some(p) = j.relocated_to {
                        ui.label(format!("Moved original: {}", p.display()));
                    }
                    if !j.relocation_error.is_empty() {
                        ui.colored_label(AMBER, j.relocation_error);
                    }
                });
            if !open {
                self.details = None;
            }
        }
        if self.log_open {
            egui::Window::new("Detailed log")
                .open(&mut self.log_open)
                .default_size([800., 400.])
                .show(ctx, |ui| {
                    egui::ScrollArea::vertical()
                        .stick_to_bottom(true)
                        .show(ui, |ui| {
                            for line in &self.logs {
                                ui.monospace(line);
                            }
                        });
                });
        }
    }
    fn on_exit(&mut self, _gl: Option<&eframe::glow::Context>) {
        self.control.stop();
        if let Some(w) = self.worker.take() {
            let _ = w.join();
        }
        self.save();
    }
}
pub fn install_desktop() -> anyhow::Result<PathBuf> {
    use std::os::unix::fs::PermissionsExt;
    let app = std::env::var_os("APPIMAGE")
        .map(PathBuf::from)
        .unwrap_or(std::env::current_exe()?);
    anyhow::ensure!(
        app.is_absolute() && app.is_file(),
        "Run Petal from its permanent location first"
    );
    let data = std::env::var_os("XDG_DATA_HOME")
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
        .unwrap_or_else(|| {
            PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".local/share")
        });
    anyhow::ensure!(
        !format!("{}{}", app.display(), data.display()).contains(['\r', '\n']),
        "Paths must not contain line breaks"
    );
    let icon = data.join("icons/hicolor/256x256/apps/petal.png");
    std::fs::create_dir_all(icon.parent().unwrap())?;
    std::fs::write(&icon, include_bytes!("../assets/petal.png"))?;
    let folder = data.join("applications");
    std::fs::create_dir_all(&folder)?;
    let entry = folder.join("petal.desktop");
    let exec = app
        .to_string_lossy()
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('`', "\\`")
        .replace('$', "\\$")
        .replace('%', "%%");
    let text = format!(
        "[Desktop Entry]\nType=Application\nName=Petal\nComment=Smaller videos, kept on your computer\nExec=\"{exec}\"\nIcon={}\nTerminal=false\nCategories=AudioVideo;Video;\nStartupWMClass=petal\n",
        icon.display()
    );
    std::fs::write(&entry, text)?;
    std::fs::set_permissions(&entry, std::fs::Permissions::from_mode(0o644))?;
    Ok(entry)
}
pub fn run(demo: bool, smoke: bool) -> anyhow::Result<()> {
    let image = image::load_from_memory(include_bytes!("../assets/petal.png"))?.into_rgba8();
    let icon = egui::IconData {
        width: image.width(),
        height: image.height(),
        rgba: image.into_raw(),
    };
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([1180., 820.])
            .with_min_inner_size([840., 660.])
            .with_app_id("petal")
            .with_icon(icon),
        renderer: eframe::Renderer::Glow,
        ..Default::default()
    };
    eframe::run_native(
        "Petal",
        options,
        Box::new(move |cc| Ok(Box::new(App::new(cc, demo, smoke)))),
    )
    .map_err(|e| anyhow::anyhow!(e.to_string()))
}
fn demo_state() -> Library {
    let mut l = Library {
        source: "/home/demo/Videos".into(),
        output: "/home/demo/Compressed".into(),
        ..Default::default()
    };
    for (name, status, size, final_size) in [
        ("Coastal walk.mp4", Status::Completed, 860, 420),
        ("Evening lights.mp4", Status::Skipped, 620, 0),
        ("Garden in spring.mkv", Status::Completed, 480, 220),
        ("Old recording.mov", Status::Failed, 300, 0),
        ("Forest trail.mp4", Status::Waiting, 520, 0),
    ] {
        l.jobs.push(Job{
source:PathBuf::from(&l.source).join(name),relative:name.into(),destination:PathBuf::from(&l.output).join(name).with_extension("mkv"),status,size:size*1024*1024,final_size:final_size*1024*1024,output_root:Some(PathBuf::from(&l.output)),message:"Demonstration data".into(),quality:serde_json::json!({
"status":"No concern in sampled scenes","mean":97.2,"p10":94.1,"worst_scene":95.6,"scenes":[{
"start":12.,"mean":98.}
,{
"start":54.,"mean":96.}
,{
"start":98.,"mean":97.}
]}
),..Default::default()}
);
    }
    l.report = l.jobs.clone();
    l.report_output = Some(l.output.clone().into());
    l
}

/// Generate desktop icon sizes without any GUI or Python runtime.
pub fn write_icons(root: &std::path::Path) -> anyhow::Result<()> {
    let icon = image::load_from_memory(include_bytes!("../assets/petal.png"))?;
    for size in [32, 48, 64, 128, 256, 512] {
        let folder = root.join(format!("{size}x{size}/apps"));
        std::fs::create_dir_all(&folder)?;
        icon.resize_exact(size, size, image::imageops::FilterType::Lanczos3)
            .save(folder.join("petal.png"))?;
    }
    Ok(())
}
