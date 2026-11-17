"""Small, explainable recommendations grounded in measured jobs, not promises."""
from dataclasses import dataclass
from statistics import fmean

from .models import Job, Settings, Status
from .media import target_size


@dataclass(frozen=True)
class Recommendation:
    title: str
    detail: str
    action: str = ""
    value: str = ""
    button: str = ""


def recommend(settings: Settings, jobs: list[Job]) -> Recommendation | None:
    all_measured = [j for j in jobs if j.status == Status.COMPLETED and ("mean" in j.quality or j.quality.get("geometry_note"))]
    measured = [j for j in all_measured if "mean" in j.quality
                if (not j.encoder_profile or j.encoder_profile == settings.profile)
                and (not j.encoder_codec or j.encoder_codec == settings.codec)
                and (not j.media or not j.output_dimensions or target_size(j.media, settings) == j.output_dimensions)
                and (j.encoder_quality is None or not j.output_dimensions
                     or j.encoder_quality == settings.quality_for_size(j.output_dimensions))]
    geometry = [j for j in all_measured if j.quality.get("geometry_note")]
    if geometry:
        return Recommendation("Review the picture framing", geometry[0].quality["geometry_note"],
                              "details", button="See quality details")
    weak = [j for j in measured if j.quality.get("status") == "Review suggested"]
    if weak:
        worst = min(j.quality.get("worst_scene", j.quality["mean"]) for j in weak)
        if settings.profile != "High Quality":
            return Recommendation("Try High Quality for the next files",
                                  f"The weakest sampled scene scored {worst:.1f} VMAF. High Quality can retain more detail, with larger files. Sample scores are a guide, not a whole-video guarantee.",
                                  "profile", "High Quality", "Use High Quality")
        return Recommendation("Inspect the weaker scenes",
                              f"The weakest sampled scene scored {worst:.1f} even with High Quality. Review its scene positions and compare the original before changing resolution.",
                              "details", button="Review scenes")
    timed = [j for j in jobs if j.status == Status.COMPLETED and j.encode_seconds and j.verify_seconds]
    if settings.strict and len(timed) >= 2:
        encode, verify = sum(j.encode_seconds for j in timed), sum(j.verify_seconds for j in timed)
        if verify > encode * 1.25:
            return Recommendation("Faster checks could help this batch",
                                  f"Full verification took {verify:.1f}s versus {encode:.1f}s encoding across {len(timed)} files. Fast mode samples parts instead of decoding every frame; full mode checks more of the file.",
                                  "fast", button="Use fast verification")
    if len(measured) >= 2:
        mean = fmean(j.quality["mean"] for j in measured)
        savings = [100 * (1 - j.final_size / j.size) for j in measured if j.size]
        if not savings:
            return None
        saving = fmean(savings)
        high = all(j.quality["mean"] >= 97 and j.quality.get("p10", 0) >= 93
                   and j.quality.get("worst_scene", 0) >= 95 for j in measured)
        if high and saving < 25 and settings.profile != "Maximum Savings":
            profile = "Balanced" if settings.profile == "High Quality" else "Maximum Savings"
            return Recommendation("There may be room for smaller files",
                                  f"Checked scenes averaged {mean:.1f} VMAF, with {saving:.1f}% savings on those files. You can try {profile}; finer texture may change, so compare the next quality reports.",
                                  "profile", profile, f"Try {profile}")
        return Recommendation("Keep these settings for now",
                              f"Checked scenes averaged {mean:.1f} VMAF and those files saved {saving:.1f}%. Results cover sampled scenes only; your current profile is a reasonable starting point.")
    return None
