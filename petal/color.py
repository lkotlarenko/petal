"""Carry existing SDR color properties; never request a color-space conversion.

Output -color_* flags can cause FFmpeg to auto-insert software format converters
on hardware surfaces. Frame properties and encoded headers avoid that negotiation.
"""
from .models import Media, Settings

PRIMARIES = {"bt709": 1, "bt470m": 4, "bt470bg": 5, "smpte170m": 6,
             "smpte240m": 7, "film": 8, "bt2020": 9, "smpte428": 10,
             "smpte431": 11, "smpte432": 12, "jedec-p22": 22, "ebu3213": 22}
TRANSFER = {"bt709": 1, "bt470m": 4, "bt470bg": 5, "smpte170m": 6,
            "smpte240m": 7, "linear": 8, "log100": 9, "log316": 10,
            "iec61966-2-4": 11, "bt1361e": 12, "iec61966-2-1": 13,
            "bt2020-10": 14, "bt2020-12": 15, "smpte2084": 16,
            "smpte428": 17, "arib-std-b67": 18}
MATRIX = {"gbr": 0, "bt709": 1, "fcc": 4, "bt470bg": 5, "smpte170m": 6,
          "smpte240m": 7, "ycgco": 8, "bt2020nc": 9, "bt2020c": 10,
          "smpte2085": 11, "chroma-derived-nc": 12, "chroma-derived-c": 13, "ictcp": 14}
RANGE = {"tv": 0, "limited": 0, "mpeg": 0, "pc": 1, "full": 1, "jpeg": 1}


def frame_color(media: Media) -> str:
    fields = []
    for key, option, values in (("color_primaries", "color_primaries", PRIMARIES),
                                ("color_transfer", "color_trc", TRANSFER),
                                ("color_space", "colorspace", MATRIX)):
        value = media.video.get(key)
        if value in values:
            fields.append(f"{option}={values[value]}")
    value = media.video.get("color_range")
    if value in RANGE:
        fields.append(f"range={'full' if RANGE[value] else 'limited'}")
    return "setparams=" + ":".join(fields) if fields else ""


def software_scale_color(media: Media) -> str:
    """Tell swscale to keep the input range/matrix when resizing or repacking."""
    args = []
    matrix = {"bt709": "bt709", "fcc": "fcc", "bt470bg": "bt601", "smpte170m": "bt601",
              "smpte240m": "smpte240m"}.get(media.video.get("color_space"))
    if matrix:
        args += [f"in_color_matrix={matrix}", f"out_color_matrix={matrix}"]
    value = media.video.get("color_range")
    if value in RANGE:
        label = "full" if RANGE[value] else "limited"
        args += [f"in_range={label}", f"out_range={label}"]
    return ":" + ":".join(args) if args else ""


def bitstream_color(media: Media, settings: Settings) -> list[str]:
    av1 = settings.codec == "av1_vaapi"
    fields = []
    for key, option, values in (
            ("color_primaries", "color_primaries" if av1 else "colour_primaries", PRIMARIES),
            ("color_transfer", "transfer_characteristics", TRANSFER),
            ("color_space", "matrix_coefficients", MATRIX)):
        value = media.video.get(key)
        if value in values:
            fields.append(f"{option}={values[value]}")
    value = media.video.get("color_range")
    if value in RANGE:
        fields.append(f"{'color_range' if av1 else 'video_full_range_flag'}={RANGE[value]}")
    if not fields:
        return []
    name = "av1_metadata" if av1 else "hevc_metadata"
    return ["-bsf:v:0", name + "=" + ":".join(fields)]
