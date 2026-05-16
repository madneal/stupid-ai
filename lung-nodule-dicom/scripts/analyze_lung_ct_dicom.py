#!/usr/bin/env python3
"""Inspect chest CT DICOM studies for lung nodule decision support.

The script intentionally creates review artifacts and rough candidates rather
than a diagnosis. It is designed for use by the lung-nodule-dicom skill.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass
class SeriesSummary:
    series_uid: str
    modality: str
    description: str
    protocol: str
    body_part: str
    image_type: str
    instance_count: int
    rows: int | None
    columns: int | None
    pixel_spacing_mm: list[float] | None
    slice_thickness_mm: float | None
    spacing_between_slices_mm: float | None
    convolution_kernel: str
    manufacturer: str
    score: int
    warnings: list[str]


@dataclass
class Candidate:
    candidate_id: int
    slice_index: int
    z_mm: float | None
    x_px: float
    y_px: float
    x_mm: float
    y_mm: float
    equivalent_diameter_mm: float
    area_mm2: float
    mean_hu: float
    max_hu: float
    bbox_px: list[int]
    crop_file: str
    note: str


@dataclass
class HospitalReportFinding:
    finding_id: int
    source_text: str
    location: str
    nodule_type: str
    size_text: str
    dimensions_mm: list[float]
    max_diameter_mm: float | None
    change: str
    recommendation: str
    reported_absent: bool
    note: str


def die(message: str, code: int = 2) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def load_modules() -> tuple[Any, Any, Any, Any]:
    missing: list[str] = []
    try:
        import pydicom  # type: ignore
    except Exception:
        pydicom = None
        missing.append("pydicom")
    try:
        import numpy as np  # type: ignore
    except Exception:
        np = None
        missing.append("numpy")
    try:
        from PIL import Image, ImageDraw  # type: ignore
    except Exception:
        Image = None
        ImageDraw = None
        missing.append("Pillow")

    if missing:
        die(
            "missing required dependencies: "
            + ", ".join(missing)
            + ". Install with: python3 -m pip install -r scripts/requirements.txt"
        )
    return pydicom, np, Image, ImageDraw


def iter_input_files(inputs: list[Path]) -> Iterable[Path]:
    for item in inputs:
        if item.is_dir():
            for path in item.rglob("*"):
                if path.is_file() and not path.name.startswith("."):
                    yield path
        elif item.is_file():
            yield item


def to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        if isinstance(value, (list, tuple)) and value:
            value = value[0]
        return float(value)
    except Exception:
        return None


def to_float_list(value: Any) -> list[float] | None:
    if value is None:
        return None
    try:
        values = [float(v) for v in list(value)]
    except Exception:
        first = to_float(value)
        return [first] if first is not None else None
    return values if values else None


def to_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def dicom_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "\\".join(str(v) for v in value)
    return str(value)


def redact_phi_text(text: str) -> str:
    replacements = [
        r"(姓名|患者姓名|病人姓名|name)\s*[:：]\s*[^\s，,；;]+",
        r"(性别|sex)\s*[:：]\s*[^\s，,；;]+",
        r"(年龄|age)\s*[:：]\s*[^\s，,；;]+",
        r"(住院号|门诊号|检查号|影像号|病历号|登记号|accession|patient\s*id|id)\s*[:：]\s*[A-Za-z0-9_.-]+",
        r"(电话|手机号|mobile|phone)\s*[:：]\s*[0-9+\-() ]{6,}",
        r"(身份证号|身份证|证件号)\s*[:：]\s*[0-9Xx*]{8,}",
    ]
    redacted = text
    for pattern in replacements:
        redacted = re.sub(pattern, r"\1: [REDACTED]", redacted, flags=re.IGNORECASE)
    return redacted


def split_report_lines(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    chunks: list[str] = []
    for line in normalized.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = re.split(r"(?<=[。；;])\s*|\s{2,}", line)
        chunks.extend(part.strip() for part in parts if part.strip())
    return chunks


def convert_report_measurement(value: str, unit: str | None) -> float:
    number = float(value)
    unit_text = (unit or "mm").lower()
    if unit_text in {"cm", "厘米"}:
        return number * 10.0
    return number


def extract_report_dimensions(text: str) -> tuple[str, list[float]]:
    dimensions: list[float] = []
    size_texts: list[str] = []
    multi_pattern = re.compile(
        r"(\d+(?:\.\d+)?)\s*(?:x|X|×|\*)\s*"
        r"(\d+(?:\.\d+)?)(?:\s*(?:x|X|×|\*)\s*(\d+(?:\.\d+)?))?"
        r"\s*(mm|毫米|cm|厘米)?",
        re.IGNORECASE,
    )
    spans: list[tuple[int, int]] = []
    for match in multi_pattern.finditer(text):
        unit = match.group(4)
        values = [
            convert_report_measurement(group, unit)
            for group in match.groups()[:3]
            if group is not None
        ]
        if values:
            dimensions.extend(values)
            size_texts.append(match.group(0))
            spans.append(match.span())

    single_pattern = re.compile(r"(\d+(?:\.\d+)?)\s*(mm|毫米|cm|厘米)", re.IGNORECASE)
    for match in single_pattern.finditer(text):
        if any(start <= match.start() < end for start, end in spans):
            continue
        dimensions.append(convert_report_measurement(match.group(1), match.group(2)))
        size_texts.append(match.group(0))

    return "; ".join(size_texts), dimensions


def extract_report_location(text: str) -> str:
    patterns = [
        "右肺上叶",
        "右肺中叶",
        "右肺下叶",
        "左肺上叶",
        "左肺下叶",
        "右上肺",
        "右中肺",
        "右下肺",
        "左上肺",
        "左下肺",
        "两肺",
        "双肺",
        "胸膜下",
        "叶间裂",
        "right upper lobe",
        "right middle lobe",
        "right lower lobe",
        "left upper lobe",
        "left lower lobe",
        "bilateral",
        "subpleural",
        "fissure",
    ]
    lower = text.lower()
    found: list[str] = []
    for pattern in patterns:
        if pattern in text or pattern in lower:
            found.append(pattern)
    return ", ".join(dict.fromkeys(found))


def extract_report_nodule_type(text: str) -> str:
    lower = text.lower()
    if any(term in text for term in ("部分实性", "混合磨玻璃", "亚实性")) or any(
        term in lower for term in ("part-solid", "part solid", "subsolid")
    ):
        return "part-solid/subsolid"
    if "磨玻璃" in text or any(
        term in lower for term in ("ground-glass", "ground glass", "ggo")
    ):
        return "ground-glass"
    if "实性" in text or "solid" in lower:
        return "solid"
    if "钙化" in text or "calcified" in lower:
        return "calcified"
    if "结节" in text or "nodule" in lower:
        return "nodule"
    return ""


def extract_report_change(text: str) -> str:
    lower = text.lower()
    if any(term in text for term in ("新发", "新增")) or "new" in lower:
        return "new"
    if any(term in text for term in ("增大", "增长", "进展")) or any(
        term in lower for term in ("increased", "growth", "enlarged")
    ):
        return "increased"
    if any(term in text for term in ("缩小", "吸收", "减少")) or any(
        term in lower for term in ("decreased", "smaller", "resolved")
    ):
        return "decreased/resolving"
    if any(term in text for term in ("稳定", "无明显变化", "同前")) or any(
        term in lower for term in ("stable", "unchanged")
    ):
        return "stable"
    return ""


def extract_report_recommendation(text: str) -> str:
    if any(term in text for term in ("建议", "随访", "复查", "复诊")):
        return text
    lower = text.lower()
    if any(term in lower for term in ("recommend", "follow-up", "follow up", "repeat ct")):
        return text
    return ""


def is_report_absence_statement(text: str) -> bool:
    lower = text.lower()
    chinese_absent = re.search(r"(未见|没有|无)(明显)?[^。；;\n]{0,12}(结节|占位|肿块)", text)
    english_absent = re.search(r"\b(no|without)\b.{0,24}\b(nodule|mass)\b", lower)
    return bool(chinese_absent or english_absent)


def looks_like_report_nodule_line(text: str) -> bool:
    lower = text.lower()
    terms = (
        "结节",
        "磨玻璃",
        "实性",
        "占位",
        "肿块",
        "ggo",
        "ground-glass",
        "ground glass",
        "nodule",
        "mass",
    )
    return any(term in text or term in lower for term in terms)


def parse_hospital_report_text(text: str) -> list[HospitalReportFinding]:
    findings: list[HospitalReportFinding] = []
    for line in split_report_lines(redact_phi_text(text)):
        if not looks_like_report_nodule_line(line):
            continue
        size_text, dimensions = extract_report_dimensions(line)
        reported_absent = is_report_absence_statement(line)
        nodule_type = "absence statement" if reported_absent else extract_report_nodule_type(line)
        findings.append(
            HospitalReportFinding(
                finding_id=len(findings) + 1,
                source_text=line,
                location=extract_report_location(line),
                nodule_type=nodule_type,
                size_text=size_text,
                dimensions_mm=dimensions,
                max_diameter_mm=max(dimensions) if dimensions else None,
                change=extract_report_change(line),
                recommendation=extract_report_recommendation(line),
                reported_absent=reported_absent,
                note="Extracted from hospital report text; verify against the original report image.",
            )
        )
    return findings


def finding_from_json(item: dict[str, Any], finding_id: int) -> HospitalReportFinding:
    raw_dimensions = item.get("dimensions_mm") or item.get("size_mm") or []
    if isinstance(raw_dimensions, (int, float, str)):
        raw_dimensions = [raw_dimensions]
    dimensions: list[float] = []
    for value in raw_dimensions:
        try:
            dimensions.append(float(value))
        except Exception:
            continue
    source_text = redact_phi_text(str(item.get("source_text") or item.get("text") or ""))
    return HospitalReportFinding(
        finding_id=finding_id,
        source_text=source_text,
        location=str(item.get("location") or ""),
        nodule_type=str(item.get("nodule_type") or item.get("type") or ""),
        size_text=str(item.get("size_text") or ""),
        dimensions_mm=dimensions,
        max_diameter_mm=(
            float(item["max_diameter_mm"])
            if item.get("max_diameter_mm") is not None
            else (max(dimensions) if dimensions else None)
        ),
        change=str(item.get("change") or ""),
        recommendation=str(item.get("recommendation") or ""),
        reported_absent=bool(item.get("reported_absent", False)),
        note=str(
            item.get("note")
            or "Structured hospital report finding; verify against the original report image."
        ),
    )


def load_hospital_report_json(path: Path) -> tuple[list[HospitalReportFinding], str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    report_text = ""
    findings: list[HospitalReportFinding] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                findings.append(finding_from_json(item, len(findings) + 1))
    elif isinstance(data, dict):
        report_text = str(data.get("report_text") or data.get("text") or "")
        raw_findings = data.get("findings") or []
        if isinstance(raw_findings, list):
            for item in raw_findings:
                if isinstance(item, dict):
                    findings.append(finding_from_json(item, len(findings) + 1))
    else:
        die(f"unsupported hospital report JSON shape: {path}")
    return findings, report_text


def ocr_hospital_report_images(
    image_paths: list[Path], language: str, warnings: list[str]
) -> str:
    if not image_paths:
        return ""
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
    except Exception:
        warnings.append(
            "Hospital report images were provided, but pytesseract/Pillow OCR is "
            "unavailable. Use model vision to transcribe the report into text or JSON."
        )
        return ""

    chunks: list[str] = []
    for path in image_paths:
        if not path.exists():
            warnings.append(f"Hospital report image does not exist: {path}")
            continue
        try:
            text = pytesseract.image_to_string(Image.open(path), lang=language)
        except Exception as exc:
            warnings.append(f"OCR failed for hospital report image {path.name}: {exc}")
            continue
        chunks.append(f"[OCR source: {path.name}]\n{text.strip()}")
    if chunks:
        warnings.append(
            "Hospital report OCR was used. Verify extracted findings against the "
            "original report image before making clinical comparisons."
        )
    return "\n\n".join(chunks)


def load_hospital_report_inputs(
    args: argparse.Namespace, warnings: list[str]
) -> tuple[list[HospitalReportFinding], bool]:
    requested = bool(
        args.hospital_report_text
        or args.hospital_report_json
        or args.hospital_report_image
    )
    findings: list[HospitalReportFinding] = []
    report_text_parts: list[str] = []

    if args.hospital_report_json:
        json_findings, json_text = load_hospital_report_json(args.hospital_report_json)
        findings.extend(json_findings)
        if json_text and not json_findings:
            report_text_parts.append(json_text)

    if args.hospital_report_text:
        report_text_parts.append(args.hospital_report_text.read_text(encoding="utf-8"))

    if args.hospital_report_image:
        report_text_parts.append(
            ocr_hospital_report_images(
                args.hospital_report_image, args.ocr_language, warnings
            )
        )

    if report_text_parts:
        parsed = parse_hospital_report_text("\n\n".join(report_text_parts))
        findings.extend(parsed)

    for index, finding in enumerate(findings, start=1):
        finding.finding_id = index
    return findings, requested


def uid_for_output(uid: str, include_phi: bool) -> str:
    if include_phi:
        return uid
    return "uid_hash_" + hashlib.sha256(uid.encode("utf-8")).hexdigest()[:16]


def z_position(ds: Any) -> float | None:
    value = getattr(ds, "ImagePositionPatient", None)
    if value is None or len(value) < 3:
        return None
    return to_float(value[2])


def read_metadata(pydicom: Any, path: Path) -> Any | None:
    try:
        ds = pydicom.dcmread(str(path), stop_before_pixels=True, force=True)
    except Exception:
        return None
    if not getattr(ds, "SeriesInstanceUID", None):
        return None
    return ds


def score_series(meta: dict[str, Any]) -> tuple[int, list[str]]:
    score = 0
    warnings: list[str] = []
    modality = meta.get("modality", "").upper()
    text = " ".join(
        str(meta.get(key, "")).lower()
        for key in ("description", "protocol", "body_part", "image_type")
    )
    count = int(meta.get("instance_count") or 0)
    thickness = meta.get("slice_thickness_mm")
    rows = meta.get("rows") or 0
    columns = meta.get("columns") or 0

    if modality == "CT":
        score += 100
    else:
        score -= 50
        warnings.append("Series modality is not CT.")

    if count >= 180:
        score += 35
    elif count >= 80:
        score += 25
    elif count >= 25:
        score += 10
    else:
        score -= 30
        warnings.append("Low slice count; may be scout, localizer, or incomplete coverage.")

    if rows >= 256 and columns >= 256:
        score += 10

    if any(token in text for token in ("chest", "lung", "thorax", "肺", "胸")):
        score += 30
    if any(token in text for token in ("localizer", "scout", "topogram", "定位", "surview")):
        score -= 120
        warnings.append("Series looks like a scout/localizer.")
    if any(token in text for token in ("cor", "sag", "mpr", "reformat")):
        score -= 5
        warnings.append("Series may be reformatted rather than primary axial images.")

    if thickness is not None:
        if thickness <= 1.5:
            score += 25
        elif thickness <= 3.0:
            score += 10
            warnings.append("Slice thickness is above ideal thin-section review.")
        elif thickness >= 5.0:
            score -= 25
            warnings.append("Thick slices can miss or blur small nodules.")

    return score, warnings


def collect_series(
    pydicom: Any, paths: list[Path], include_phi: bool
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    metadata_by_uid: dict[str, dict[str, Any]] = {}

    for path in iter_input_files(paths):
        ds = read_metadata(pydicom, path)
        if ds is None:
            continue
        uid = str(ds.SeriesInstanceUID)
        row = {
            "path": path,
            "instance_number": to_int(getattr(ds, "InstanceNumber", None)),
            "z_position": z_position(ds),
            "sop_instance_uid": dicom_text(getattr(ds, "SOPInstanceUID", "")),
        }
        grouped.setdefault(uid, []).append(row)
        if uid not in metadata_by_uid:
            metadata_by_uid[uid] = {
                "series_uid": uid_for_output(uid, include_phi),
                "series_uid_raw": uid,
                "modality": dicom_text(getattr(ds, "Modality", "")),
                "description": dicom_text(getattr(ds, "SeriesDescription", "")),
                "protocol": dicom_text(getattr(ds, "ProtocolName", "")),
                "body_part": dicom_text(getattr(ds, "BodyPartExamined", "")),
                "image_type": dicom_text(getattr(ds, "ImageType", "")),
                "rows": to_int(getattr(ds, "Rows", None)),
                "columns": to_int(getattr(ds, "Columns", None)),
                "pixel_spacing_mm": to_float_list(getattr(ds, "PixelSpacing", None)),
                "slice_thickness_mm": to_float(getattr(ds, "SliceThickness", None)),
                "spacing_between_slices_mm": to_float(
                    getattr(ds, "SpacingBetweenSlices", None)
                ),
                "convolution_kernel": dicom_text(
                    getattr(ds, "ConvolutionKernel", "")
                ),
                "manufacturer": dicom_text(getattr(ds, "Manufacturer", "")),
            }

    summaries: list[dict[str, Any]] = []
    for uid, instances in grouped.items():
        meta = dict(metadata_by_uid[uid])
        meta["instance_count"] = len(instances)
        score, warnings = score_series(meta)
        meta["score"] = score
        meta["warnings"] = warnings
        public_summary = asdict(
            SeriesSummary(**{k: meta[k] for k in SeriesSummary.__annotations__})
        )
        public_summary["series_uid_raw"] = uid
        summaries.append(public_summary)

    summaries.sort(key=lambda item: item["score"], reverse=True)
    return summaries, grouped


def sort_instances(instances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if any(item["z_position"] is not None for item in instances):
        return sorted(
            instances,
            key=lambda item: (
                item["z_position"] is None,
                item["z_position"] if item["z_position"] is not None else 0.0,
            ),
        )
    return sorted(
        instances,
        key=lambda item: (
            item["instance_number"] is None,
            item["instance_number"] if item["instance_number"] is not None else 0,
        ),
    )


def select_series(
    summaries: list[dict[str, Any]], requested: str | None, include_phi: bool
) -> dict[str, Any]:
    if not summaries:
        die("no readable DICOM series found")
    if requested:
        for summary in summaries:
            if requested in (summary.get("series_uid"), summary.get("series_uid_raw")):
                return summary
        die("requested series was not found; run without --series to list available series")
    return summaries[0]


def apply_slice_limit(
    instances: list[dict[str, Any]], max_slices: int, warnings: list[str]
) -> list[dict[str, Any]]:
    if max_slices <= 0 or len(instances) <= max_slices:
        return instances
    step = math.ceil(len(instances) / max_slices)
    warnings.append(
        f"Series has {len(instances)} slices; sampling every {step} slices to limit memory."
    )
    return instances[::step]


def load_volume(
    pydicom: Any,
    np: Any,
    instances: list[dict[str, Any]],
    warnings: list[str],
) -> tuple[Any, list[float | None]]:
    arrays: list[Any] = []
    z_values: list[float | None] = []
    expected_shape: tuple[int, int] | None = None

    for item in instances:
        path = item["path"]
        try:
            ds = pydicom.dcmread(str(path), force=True)
            pixel = ds.pixel_array.astype("float32")
        except Exception as exc:
            warnings.append(
                f"Could not decode pixels for one instance ({path.name}): {exc}"
            )
            continue

        slope = to_float(getattr(ds, "RescaleSlope", None)) or 1.0
        intercept = to_float(getattr(ds, "RescaleIntercept", None)) or 0.0
        hu = pixel * slope + intercept
        if hu.ndim != 2:
            warnings.append(f"Skipping non-2D instance: {path.name}")
            continue
        if expected_shape is None:
            expected_shape = tuple(hu.shape)
        elif tuple(hu.shape) != expected_shape:
            warnings.append(f"Skipping instance with inconsistent shape: {path.name}")
            continue
        arrays.append(hu.astype("float32"))
        z_values.append(z_position(ds))

    if not arrays:
        die("no DICOM pixel data could be decoded")
    return np.stack(arrays, axis=0), z_values


def window_to_uint8(np: Any, image: Any, center: float, width: float) -> Any:
    low = center - width / 2.0
    high = center + width / 2.0
    clipped = np.clip(image, low, high)
    return ((clipped - low) / (high - low) * 255.0).astype("uint8")


def make_sheet(
    Image: Any,
    ImageDraw: Any,
    images: list[Any],
    labels: list[str],
    out_path: Path,
    tile_width: int = 220,
    columns: int = 6,
) -> None:
    if not images:
        return
    resized = []
    for image in images:
        pil = Image.fromarray(image)
        ratio = tile_width / pil.width
        tile_height = max(1, int(pil.height * ratio))
        resized.append(pil.resize((tile_width, tile_height)))
    tile_height = max(image.height for image in resized)
    rows = math.ceil(len(resized) / columns)
    sheet = Image.new("L", (columns * tile_width, rows * tile_height), color=0)
    draw = ImageDraw.Draw(sheet)
    for index, image in enumerate(resized):
        x = (index % columns) * tile_width
        y = (index // columns) * tile_height
        sheet.paste(image, (x, y))
        draw.rectangle((x, y, x + tile_width - 1, y + 16), fill=0)
        draw.text((x + 4, y + 2), labels[index], fill=255)
    sheet.save(out_path)


def create_contact_sheet(
    np: Any,
    Image: Any,
    ImageDraw: Any,
    volume: Any,
    z_values: list[float | None],
    out_path: Path,
    count: int,
) -> None:
    n_slices = volume.shape[0]
    take = min(max(1, count), n_slices)
    indexes = np.linspace(0, n_slices - 1, take).round().astype(int).tolist()
    images: list[Any] = []
    labels: list[str] = []
    for idx in indexes:
        images.append(window_to_uint8(np, volume[idx], center=-600, width=1500))
        z = z_values[idx] if idx < len(z_values) else None
        labels.append(f"#{idx}" + (f" z={z:.1f}" if z is not None else ""))
    make_sheet(Image, ImageDraw, images, labels, out_path)


def create_mip_sheet(
    np: Any,
    Image: Any,
    ImageDraw: Any,
    volume: Any,
    z_values: list[float | None],
    out_path: Path,
    slab_slices: int,
    tile_count: int,
) -> None:
    n_slices = volume.shape[0]
    slab = max(1, min(slab_slices, n_slices))
    half = max(0, slab // 2)
    count = min(max(1, tile_count), n_slices)
    centers = np.linspace(0, n_slices - 1, count).round().astype(int).tolist()
    images: list[Any] = []
    labels: list[str] = []
    for center in centers:
        start = max(0, center - half)
        end = min(n_slices, center + half + 1)
        mip = np.max(volume[start:end], axis=0)
        images.append(window_to_uint8(np, mip, center=-600, width=1500))
        z = z_values[center] if center < len(z_values) else None
        labels.append(f"{start}-{end - 1}" + (f" z={z:.1f}" if z is not None else ""))
    make_sheet(Image, ImageDraw, images, labels, out_path)


def estimate_z_spacing(z_values: list[float | None], fallback: float | None) -> float:
    numeric = [z for z in z_values if z is not None]
    if len(numeric) >= 2:
        diffs = [abs(numeric[i + 1] - numeric[i]) for i in range(len(numeric) - 1)]
        diffs = [value for value in diffs if value > 0]
        if diffs:
            diffs.sort()
            return float(diffs[len(diffs) // 2])
    return float(fallback or 1.0)


def detect_candidates(
    np: Any,
    volume: Any,
    z_values: list[float | None],
    pixel_spacing: list[float] | None,
    z_spacing: float,
    args: argparse.Namespace,
    warnings: list[str],
) -> list[Candidate]:
    try:
        from scipy import ndimage as ndi  # type: ignore
    except Exception:
        warnings.append(
            "SciPy is not installed; skipping rough automated candidate detection."
        )
        return []

    if pixel_spacing is None or len(pixel_spacing) < 2:
        spacing_y = spacing_x = 1.0
        warnings.append("Pixel spacing missing; candidate measurements use 1 mm pixels.")
    else:
        spacing_y, spacing_x = float(pixel_spacing[0]), float(pixel_spacing[1])

    raw: list[dict[str, Any]] = []
    min_area_mm2 = math.pi * (args.min_candidate_mm / 2.0) ** 2
    max_area_mm2 = math.pi * (args.max_candidate_mm / 2.0) ** 2
    min_lung_area_px = max(1000, int(volume.shape[1] * volume.shape[2] * 0.015))

    for slice_index, image in enumerate(volume):
        air = image < -450
        labels, label_count = ndi.label(air)
        if label_count == 0:
            continue

        border_ids = set(labels[0, :].tolist())
        border_ids.update(labels[-1, :].tolist())
        border_ids.update(labels[:, 0].tolist())
        border_ids.update(labels[:, -1].tolist())
        border_ids.discard(0)
        outside_air = np.isin(labels, list(border_ids)) if border_ids else np.zeros_like(air)
        internal_air = air & ~outside_air

        lung_labels, lung_count = ndi.label(internal_air)
        if lung_count == 0:
            continue
        sizes = np.bincount(lung_labels.ravel())
        keep = [
            idx
            for idx, size in enumerate(sizes)
            if idx != 0 and size >= min_lung_area_px
        ]
        keep = sorted(keep, key=lambda idx: sizes[idx], reverse=True)[:4]
        if not keep:
            continue

        lung_air = np.isin(lung_labels, keep)
        lung_field = ndi.binary_fill_holes(lung_air)
        soft_tissue = (
            lung_field
            & (image >= args.candidate_min_hu)
            & (image <= args.candidate_max_hu)
        )
        soft_tissue = ndi.binary_opening(soft_tissue, structure=np.ones((2, 2)))
        component_labels, component_count = ndi.label(soft_tissue)
        if component_count == 0:
            continue

        objects = ndi.find_objects(component_labels)
        for comp_idx, slices in enumerate(objects, start=1):
            if slices is None:
                continue
            mask = component_labels[slices] == comp_idx
            area_px = int(mask.sum())
            area_mm2 = area_px * spacing_x * spacing_y
            if area_mm2 < min_area_mm2 or area_mm2 > max_area_mm2:
                continue
            ys, xs = np.nonzero(mask)
            y0 = int(slices[0].start)
            x0 = int(slices[1].start)
            y_abs = ys + y0
            x_abs = xs + x0
            height = max(1, int(slices[0].stop - slices[0].start))
            width = max(1, int(slices[1].stop - slices[1].start))
            elongation = max(width / height, height / width)
            if elongation > 4.0:
                continue
            values = image[y_abs, x_abs]
            equivalent_diameter = math.sqrt(4.0 * area_mm2 / math.pi)
            x_center = float(x_abs.mean())
            y_center = float(y_abs.mean())
            raw.append(
                {
                    "slice_index": slice_index,
                    "z_mm": z_values[slice_index] if slice_index < len(z_values) else None,
                    "x_px": x_center,
                    "y_px": y_center,
                    "x_mm": x_center * spacing_x,
                    "y_mm": y_center * spacing_y,
                    "equivalent_diameter_mm": equivalent_diameter,
                    "area_mm2": area_mm2,
                    "mean_hu": float(values.mean()),
                    "max_hu": float(values.max()),
                    "bbox_px": [
                        int(slices[1].start),
                        int(slices[0].start),
                        int(slices[1].stop),
                        int(slices[0].stop),
                    ],
                    "score": equivalent_diameter + max(0.0, float(values.mean()) + 500.0) / 200.0,
                }
            )

    raw.sort(key=lambda item: item["score"], reverse=True)
    selected: list[Candidate] = []
    for item in raw:
        duplicate = False
        for existing in selected:
            dz = 0.0
            if item["z_mm"] is not None and existing.z_mm is not None:
                dz = abs(float(item["z_mm"]) - float(existing.z_mm))
            else:
                dz = abs(item["slice_index"] - existing.slice_index) * z_spacing
            dx = abs(item["x_mm"] - existing.x_mm)
            dy = abs(item["y_mm"] - existing.y_mm)
            distance = math.sqrt(dx * dx + dy * dy + dz * dz)
            suppress_radius = max(
                8.0,
                (item["equivalent_diameter_mm"] + existing.equivalent_diameter_mm)
                / 2.0,
            )
            if distance < suppress_radius:
                duplicate = True
                break
        if duplicate:
            continue
        selected.append(
            Candidate(
                candidate_id=len(selected) + 1,
                slice_index=int(item["slice_index"]),
                z_mm=item["z_mm"],
                x_px=float(item["x_px"]),
                y_px=float(item["y_px"]),
                x_mm=float(item["x_mm"]),
                y_mm=float(item["y_mm"]),
                equivalent_diameter_mm=float(item["equivalent_diameter_mm"]),
                area_mm2=float(item["area_mm2"]),
                mean_hu=float(item["mean_hu"]),
                max_hu=float(item["max_hu"]),
                bbox_px=list(item["bbox_px"]),
                crop_file="",
                note="Rough soft-tissue candidate inside lung mask; verify manually.",
            )
        )
        if len(selected) >= args.max_candidates:
            break

    if not selected:
        warnings.append(
            "No automated solid-soft-tissue candidates passed the rough detector thresholds."
        )
    return selected


def save_candidate_crops(
    np: Any,
    Image: Any,
    ImageDraw: Any,
    volume: Any,
    candidates: list[Candidate],
    out_dir: Path,
) -> None:
    crop_dir = out_dir / "candidate_crops"
    crop_dir.mkdir(parents=True, exist_ok=True)
    for candidate in candidates:
        image = window_to_uint8(
            np, volume[candidate.slice_index], center=-600, width=1500
        )
        x1, y1, x2, y2 = candidate.bbox_px
        pad = 36
        x1p = max(0, x1 - pad)
        y1p = max(0, y1 - pad)
        x2p = min(image.shape[1], x2 + pad)
        y2p = min(image.shape[0], y2 + pad)
        crop = Image.fromarray(image[y1p:y2p, x1p:x2p]).convert("RGB")
        draw = ImageDraw.Draw(crop)
        ellipse = (x1 - x1p, y1 - y1p, x2 - x1p, y2 - y1p)
        draw.ellipse(ellipse, outline=(255, 64, 64), width=2)
        draw.text((4, 4), f"C{candidate.candidate_id} #{candidate.slice_index}", fill=(255, 255, 0))
        filename = f"candidate_{candidate.candidate_id:02d}_slice_{candidate.slice_index}.png"
        crop.save(crop_dir / filename)
        candidate.crop_file = str(Path("candidate_crops") / filename)


def write_candidates_csv(path: Path, candidates: list[Candidate]) -> None:
    fields = list(Candidate.__dataclass_fields__.keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for candidate in candidates:
            row = asdict(candidate)
            row["bbox_px"] = json.dumps(row["bbox_px"])
            writer.writerow(row)


def write_hospital_report_findings_json(
    path: Path, findings: list[HospitalReportFinding]
) -> None:
    path.write_text(
        json.dumps(
            [asdict(finding) for finding in findings],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def choose_candidate_match(
    finding: HospitalReportFinding, candidates: list[Candidate]
) -> tuple[Candidate | None, str]:
    if not candidates:
        return None, "No automated DICOM candidates were generated."
    if finding.reported_absent:
        return None, "Hospital report states no relevant nodule/mass finding."
    if finding.max_diameter_mm is None:
        return None, "Report finding has no extracted size for candidate matching."

    best = min(
        candidates,
        key=lambda candidate: abs(
            candidate.equivalent_diameter_mm - float(finding.max_diameter_mm)
        ),
    )
    difference = abs(best.equivalent_diameter_mm - float(finding.max_diameter_mm))
    tolerance = max(3.0, float(finding.max_diameter_mm) * 0.5)
    if difference <= tolerance:
        return (
            best,
            "Possible size match only; confirm location and slice manually.",
        )
    return best, "Nearest candidate is not a reliable size match; manual review needed."


def write_report_reconciliation(
    path: Path,
    selected: dict[str, Any],
    candidates: list[Candidate],
    report_findings: list[HospitalReportFinding],
    warnings: list[str],
) -> None:
    lines: list[str] = []
    lines.append("# Hospital Report Reconciliation")
    lines.append("")
    lines.append(
        "This comparison treats the hospital radiology report as the primary clinical "
        "source. AI candidates are search aids, not confirmed nodules."
    )
    lines.append("")
    lines.append("## Inputs")
    lines.append(f"- Selected DICOM series: {selected.get('description') or '[unlabeled]'}")
    lines.append(f"- Slice thickness: {selected.get('slice_thickness_mm') or '[unknown]'} mm")
    lines.append(f"- Automated candidate count: {len(candidates)}")
    lines.append(f"- Extracted hospital report finding count: {len(report_findings)}")
    lines.append("")

    if warnings:
        lines.append("## Warnings")
        for warning in warnings:
            lines.append(f"- {warning}")
        lines.append("")

    lines.append("## Finding-Level Comparison")
    lines.append(
        "| Report finding | Report location/type/size | AI candidate | Assessment |"
    )
    lines.append("|---|---|---|---|")
    if report_findings:
        for finding in report_findings:
            candidate, assessment = choose_candidate_match(finding, candidates)
            report_summary = "<br>".join(
                part
                for part in (
                    f"location: {finding.location}" if finding.location else "",
                    f"type: {finding.nodule_type}" if finding.nodule_type else "",
                    f"size: {finding.size_text}" if finding.size_text else "",
                    f"change: {finding.change}" if finding.change else "",
                )
                if part
            )
            if not report_summary:
                report_summary = "[not extracted]"
            if candidate is None:
                candidate_summary = "-"
            else:
                candidate_summary = (
                    f"C{candidate.candidate_id} slice #{candidate.slice_index}, "
                    f"{candidate.equivalent_diameter_mm:.1f} mm, "
                    f"{candidate.crop_file or '[no crop]'}"
                )
            source = finding.source_text.replace("|", "/")
            lines.append(
                f"| {source} | {report_summary} | {candidate_summary} | {assessment} |"
            )
    else:
        lines.append(
            "| No report nodule finding extracted | - | - | "
            "Transcribe the report image manually or provide structured JSON. |"
        )
    lines.append("")

    if candidates:
        lines.append("## AI-Only Candidate Review Queue")
        lines.append(
            "These candidates may be vessels, scars, airway walls, inflammation, or "
            "partial-volume artifacts. Review them only as possible explanations for "
            "differences from the hospital report."
        )
        lines.append("")
        lines.append("| Candidate | Slice | Diameter | Mean HU | Crop |")
        lines.append("|---:|---:|---:|---:|---|")
        for candidate in sorted(
            candidates,
            key=lambda item: item.equivalent_diameter_mm,
            reverse=True,
        )[:12]:
            lines.append(
                "| C{cid} | #{sl} | {diam:.1f} mm | {hu:.0f} | {crop} |".format(
                    cid=candidate.candidate_id,
                    sl=candidate.slice_index,
                    diam=candidate.equivalent_diameter_mm,
                    hu=candidate.mean_hu,
                    crop=candidate.crop_file or "",
                )
            )
        lines.append("")

    lines.append("## Reconciliation Guidance")
    lines.append(
        "- If the hospital report describes a finding that AI did not match, inspect "
        "the original thin-section lung-window DICOM at the reported lobe/location."
    )
    lines.append(
        "- If AI candidates are not mentioned in the hospital report, assume false "
        "positive candidates until a radiologist confirms otherwise."
    )
    lines.append(
        "- For management decisions, use the formal radiology report and treating "
        "doctor/radiologist interpretation."
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_report_draft(
    path: Path,
    selected: dict[str, Any],
    warnings: list[str],
    candidates: list[Candidate],
) -> None:
    lines: list[str] = []
    lines.append("# 肺结节CT辅助分析草稿")
    lines.append("")
    lines.append("## 结论摘要")
    if candidates:
        largest = max(candidates, key=lambda item: item.equivalent_diameter_mm)
        lines.append(
            f"- 自动算法提示 {len(candidates)} 个需要人工复核的软组织密度候选灶；最大候选约 {largest.equivalent_diameter_mm:.1f} mm。"
        )
    else:
        lines.append("- 自动候选算法未给出可列出的实性/软组织密度候选灶，不能据此排除肺结节。")
    lines.append("- 需要结合原始薄层DICOM逐层阅片、既往CT和病史确认。")
    lines.append("")
    lines.append("## 检查质量")
    lines.append(f"- 选用序列: {selected.get('description') or '[未标注]'}")
    lines.append(f"- 图像类型: {selected.get('image_type') or '[未标注]'}")
    lines.append(f"- 层厚: {selected.get('slice_thickness_mm') or '[未知]'} mm")
    lines.append(f"- 像素间距: {selected.get('pixel_spacing_mm') or '[未知]'} mm")
    lines.append(f"- 重建核: {selected.get('convolution_kernel') or '[未知]'}")
    if warnings:
        lines.append("- 限制/警告:")
        for warning in warnings:
            lines.append(f"  - {warning}")
    lines.append("")
    lines.append("## 影像所见")
    lines.append("| 候选 | 层面 | 估计大小 | 平均HU | 截图 | 备注 |")
    lines.append("|---|---:|---:|---:|---|---|")
    if candidates:
        for candidate in candidates:
            lines.append(
                "| C{cid} | #{sl} | {diam:.1f} mm | {hu:.0f} | {crop} | 需人工确认是否为血管/瘢痕/气道壁/真实结节 |".format(
                    cid=candidate.candidate_id,
                    sl=candidate.slice_index,
                    diam=candidate.equivalent_diameter_mm,
                    hu=candidate.mean_hu,
                    crop=candidate.crop_file or "",
                )
            )
    else:
        lines.append("| - | - | - | - | - | 未生成候选表 |")
    lines.append("")
    lines.append("## 风险判断与建议")
    lines.append("- 尚不能根据自动候选结果判断良恶性。请由放射科医生复核薄层肺窗和纵隔窗。")
    lines.append("- 如果是筛查LDCT，可在确认结节类型和大小后参考最新ACR Lung-RADS。")
    lines.append("- 如果是成人偶发肺结节且无已知肿瘤/免疫抑制，可在确认背景后参考Fleischner随访思路。")
    lines.append("")
    lines.append("## 需要补充的信息")
    lines.append("- 年龄、吸烟史、肿瘤史、症状、感染史、既往CT及正式放射报告。")
    lines.append("")
    lines.append("> AI辅助分析不能替代放射科医生正式报告或临床诊断。")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze chest CT DICOM pixels for lung nodule decision support."
    )
    parser.add_argument("inputs", nargs="+", type=Path, help="DICOM file(s) or folders")
    parser.add_argument("--out", type=Path, default=Path("lung_ct_review"))
    parser.add_argument("--series", help="SeriesInstanceUID or displayed UID hash")
    parser.add_argument("--include-phi", action="store_true", help="Include PHI/UIDs in outputs")
    parser.add_argument("--max-slices", type=int, default=700)
    parser.add_argument("--contact-slices", type=int, default=36)
    parser.add_argument("--mip-slices", type=int, default=8)
    parser.add_argument("--mip-tiles", type=int, default=36)
    parser.add_argument("--no-candidates", action="store_true")
    parser.add_argument("--min-candidate-mm", type=float, default=3.0)
    parser.add_argument("--max-candidate-mm", type=float, default=30.0)
    parser.add_argument("--candidate-min-hu", type=float, default=-500.0)
    parser.add_argument("--candidate-max-hu", type=float, default=300.0)
    parser.add_argument("--max-candidates", type=int, default=30)
    parser.add_argument(
        "--hospital-report-text",
        type=Path,
        help="Transcribed hospital report text. PHI is redacted from derived outputs.",
    )
    parser.add_argument(
        "--hospital-report-json",
        type=Path,
        help="Structured report findings JSON, or JSON containing report_text/findings.",
    )
    parser.add_argument(
        "--hospital-report-image",
        action="append",
        type=Path,
        default=[],
        help="Hospital report image for optional OCR. Prefer verified transcription.",
    )
    parser.add_argument(
        "--ocr-language",
        default="chi_sim+eng",
        help="Tesseract language string for --hospital-report-image OCR.",
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    for item in args.inputs:
        if not item.exists():
            die(f"input does not exist: {item}")

    pydicom, np, Image, ImageDraw = load_modules()
    args.out.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    summaries, grouped = collect_series(pydicom, args.inputs, args.include_phi)
    selected = select_series(summaries, args.series, args.include_phi)
    selected_uid_display = selected["series_uid"]

    raw_uid = None
    for summary in summaries:
        if summary["series_uid"] == selected_uid_display:
            raw_uid = summary.get("series_uid_raw")
            break
    if raw_uid is None:
        raw_uid = selected_uid_display

    instances = sort_instances(grouped[raw_uid])
    warnings.extend(selected.get("warnings", []))
    instances = apply_slice_limit(instances, args.max_slices, warnings)
    volume, z_values = load_volume(pydicom, np, instances, warnings)

    contact_path = args.out / "series_selected_contact_sheet_lung.png"
    mip_path = args.out / "series_selected_mip_lung.png"
    create_contact_sheet(
        np, Image, ImageDraw, volume, z_values, contact_path, args.contact_slices
    )
    create_mip_sheet(
        np, Image, ImageDraw, volume, z_values, mip_path, args.mip_slices, args.mip_tiles
    )

    candidates: list[Candidate] = []
    if not args.no_candidates:
        z_spacing = estimate_z_spacing(z_values, selected.get("slice_thickness_mm"))
        candidates = detect_candidates(
            np,
            volume,
            z_values,
            selected.get("pixel_spacing_mm"),
            z_spacing,
            args,
            warnings,
        )
        save_candidate_crops(np, Image, ImageDraw, volume, candidates, args.out)
        write_candidates_csv(args.out / "candidates.csv", candidates)

    hospital_report_findings, hospital_report_input_requested = load_hospital_report_inputs(
        args, warnings
    )
    if hospital_report_input_requested:
        write_hospital_report_findings_json(
            args.out / "hospital_report_findings.json", hospital_report_findings
        )
        write_report_reconciliation(
            args.out / "report_reconciliation.md",
            selected,
            candidates,
            hospital_report_findings,
            warnings,
        )

    summary_json = {
        "selected_series_uid": selected_uid_display,
        "selected_series": {
            key: value
            for key, value in selected.items()
            if key not in {"series_uid_raw", "warnings"}
        },
        "series_inventory": [
            {key: value for key, value in item.items() if key != "series_uid_raw"}
            for item in summaries
        ],
        "loaded_slice_count": int(volume.shape[0]),
        "volume_shape": [int(value) for value in volume.shape],
        "artifacts": {
            "contact_sheet_lung": contact_path.name,
            "mip_lung": mip_path.name,
            "candidates_csv": "candidates.csv" if not args.no_candidates else None,
            "hospital_report_findings_json": (
                "hospital_report_findings.json"
                if hospital_report_input_requested
                else None
            ),
            "report_reconciliation": (
                "report_reconciliation.md" if hospital_report_input_requested else None
            ),
        },
        "candidate_count": len(candidates),
        "hospital_report": {
            "input_provided": hospital_report_input_requested,
            "finding_count": len(hospital_report_findings),
            "note": (
                "Hospital report findings are extracted/redacted for reconciliation; "
                "verify OCR or manual transcription against the original report image."
            ),
        },
        "warnings": warnings,
        "privacy": {
            "include_phi": bool(args.include_phi),
            "note": (
                "UIDs and patient identifiers are redacted or hashed unless "
                "--include-phi is used. Hospital report PHI patterns are redacted "
                "from derived reconciliation artifacts."
            ),
        },
    }
    (args.out / "study_summary.json").write_text(
        json.dumps(summary_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_report_draft(args.out / "report_draft.md", selected, warnings, candidates)

    print(json.dumps(summary_json, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
