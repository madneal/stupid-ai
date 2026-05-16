---
name: lung-nodule-dicom
description: Analyze chest CT DICOM studies for pulmonary nodules when users provide DICOM files, CT series folders, LDCT screening studies, incidental lung nodules, hospital report images, or Chinese requests such as 肺结节诊断/肺部CT/胸部CT/DICOM分析. Use the bundled script to inspect DICOM pixels directly, generate lung-window/MIP review images, summarize CT acquisition metadata, reconcile findings against transcribed hospital reports, draft a structured nodule report, and keep clinical recommendations framed as decision support requiring radiologist/doctor confirmation.
---

# Lung Nodule DICOM

## Overview

Use this skill to review chest CT DICOM data directly for pulmonary nodule decision support. It can parse DICOM series, create lung-window review images, extract acquisition parameters, generate rough candidate nodule crops, reconcile against a hospital radiology report, and draft a bilingual clinical-style report.

This skill is not a medical device and must not present automated image findings as a definitive diagnosis. For patient-specific management, state uncertainty, cite current guidelines when used, and recommend confirmation by a qualified radiologist or treating physician.

For clinical workflow details, reporting language, and guideline caveats, read [references/clinical_framework.md](references/clinical_framework.md). For hospital report images or disagreement analysis, read [references/hospital_report_reconciliation.md](references/hospital_report_reconciliation.md).

## Workflow

1. Confirm the study context.
   - Ask for missing clinical context only when it changes interpretation: age, smoking history, cancer history, symptoms, immunosuppression, CT date, screening versus incidental study, and prior CT availability.
   - If hospital report images are provided, inspect the images first and extract only clinically relevant report text. Redact patient name, ID, accession number, phone, address, and hospital identifiers unless the user explicitly needs them.
   - Treat the task as decision support, not a final diagnosis.
   - Avoid exposing patient identifiers unless the user explicitly needs them.

2. Run the DICOM analyzer on the file or folder.

```bash
python3 scripts/analyze_lung_ct_dicom.py /path/to/dicom-or-folder --out ./lung_ct_review
```

   If the hospital report was provided as images, transcribe the nodule-related findings into a local text or JSON file, then pass it into the analyzer:

```bash
python3 scripts/analyze_lung_ct_dicom.py /path/to/dicom-or-folder --out ./lung_ct_review --hospital-report-text ./hospital_report_text.md
```

   Optional OCR can bootstrap local image files, but verify OCR against the report image before relying on it:

```bash
python3 scripts/analyze_lung_ct_dicom.py /path/to/dicom-or-folder --out ./lung_ct_review --hospital-report-image ./report_page1.png
```

   Local OCR requires `pytesseract` plus the Tesseract system binary. If OCR is unavailable or noisy, use direct image review and manually create the transcribed text/JSON.

   If dependencies are missing:

```bash
python3 -m pip install -r scripts/requirements.txt
```

   For compressed DICOM transfer syntaxes, additional pixel handlers may be needed, such as `pylibjpeg[all]` or `gdcm`.

3. Use the script outputs as image evidence.
   - `study_summary.json`: CT series inventory, selected series, warnings, and DICOM acquisition metadata.
   - `series_*_contact_sheet_lung.png`: lung-window axial review sheet.
   - `series_*_mip_lung.png`: thin-slab maximum-intensity projections for nodule search support.
   - `candidates.csv` and `candidate_*.png`: rough automated candidates when the optional SciPy-based detector can run.
   - `hospital_report_findings.json`: redacted findings extracted from the hospital report when report input is provided.
   - `report_reconciliation.md`: line-by-line comparison between hospital report findings and AI candidate evidence.
   - `report_draft.md`: starting point for a structured report.

4. Validate the selected CT series before interpretation.
   - Prefer diagnostic chest CT or LDCT axial thin sections, ideally slice thickness <= 1.5 mm.
   - Reject or down-weight scouts/localizers, reformats only, thick-slice reconstructions, severe motion, incomplete lung coverage, or non-chest CT series.
   - Check whether contrast, post-operative changes, infection, atelectasis, or scars could mimic nodules.

5. Characterize nodules from the images and metadata.
   - Record lobe/location, size in mm, type (solid, pure ground-glass, part-solid), margins, calcification/fat, perifissural morphology, pleural relation, multiplicity, and associated findings.
   - Compare with prior CT when available. Growth and persistence are often more important than a single measurement.
   - Treat script-generated candidates as a search aid only; false positives and false negatives are expected.

6. Draft the answer.
   - Start with the technical adequacy and the most clinically relevant findings.
   - If a hospital report is provided, state that the hospital radiology report is the primary clinical source. Present AI differences as possible questions for radiologist review, not competing diagnoses.
   - Separate observed image facts from inference and guideline-based follow-up.
   - Use Chinese if the user asked in Chinese.
   - Include a safety line: this is AI-assisted review and should be confirmed by a radiologist/doctor, especially for management decisions.

## Script Options

Useful commands:

```bash
# Analyze a specific SeriesInstanceUID
python3 scripts/analyze_lung_ct_dicom.py ./DICOM --series 1.2.840... --out ./review

# Include patient identifiers in local output only when explicitly needed
python3 scripts/analyze_lung_ct_dicom.py ./DICOM --include-phi --out ./review

# Skip rough automated candidate detection and only create series/images/report
python3 scripts/analyze_lung_ct_dicom.py ./DICOM --no-candidates --out ./review

# Limit memory on large studies
python3 scripts/analyze_lung_ct_dicom.py ./DICOM --max-slices 450 --out ./review

# Reconcile DICOM findings with a manually transcribed hospital report
python3 scripts/analyze_lung_ct_dicom.py ./DICOM --hospital-report-text ./hospital_report_text.md --out ./review

# Use structured report findings extracted from report images
python3 scripts/analyze_lung_ct_dicom.py ./DICOM --hospital-report-json ./hospital_report_findings.json --out ./review
```

The detector is intentionally conservative and unvalidated. It helps find soft-tissue-density objects inside a rough lung mask, but it cannot classify malignancy, reliably separate vessels/scars from nodules, or exclude subtle ground-glass nodules.

## Output Pattern

Use this structure unless the user asks for something else:

```markdown
# 肺结节CT辅助分析

## 结论摘要
[1-3 bullet points. State whether nodules are present/suspected, the dominant nodule, and confidence limits.]

## 检查质量
[Series used, CT type, slice thickness, contrast if known, limitations.]

## 影像所见
| 结节 | 部位 | 类型 | 大小 | 形态/密度 | 对比既往 | 备注 |
|---|---|---|---|---|---|---|

## 风险判断与建议
[Use only applicable guidelines. Explain why Fleischner or Lung-RADS does/does not apply.]

## 需要补充的信息
[Prior CT, smoking history, cancer history, symptoms, etc.]

> AI辅助分析不能替代放射科医生阅片或临床诊断；如有症状、已知肿瘤病史或医生要求，请以专科医生意见为准。
```
