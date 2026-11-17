# Desktop review

The 0.5 interface is native egui/OpenGL. Current screenshots are captured from the Rust binary using demonstration filenames/numbers, not private media. They show the Finished list and report.

Reviewed: readable full folder paths, profile/size controls, Jobs/Finished separation, search/filter/selection, right-click actions, colored statuses, report totals, confirmation text, advanced Settings, icon assets and safe shutdown. The report's Trash action names originals and explains that smaller copies remain. Empty/search states and single-video button wording are handled.

Automated package smoke tests cover launching and closing the actual native GUI, menu integration and six AppImage icon sizes. Rust filesystem/media tests cover the operations behind controls. A startup smoke test is not a complete interactive accessibility or every-compositor certification. Portal appearance and desktop Trash availability depend on the host.

Before each release, also check: narrow-window scrolling, keyboard focus/tab order, long/Unicode paths, many rows, active stop/close, unavailable device/portal, report reopen after restart and selection actions while idle. Keep the UI and these statements aligned with actual checks; never infer driver correctness from a demo screenshot.
