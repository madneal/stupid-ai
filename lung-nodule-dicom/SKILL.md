---
name: lung-nodule-dicom
description: Analyze chest CT DICOM studies for pulmonary nodules when users provide DICOM files, CT series folders, LDCT screening studies, incidental lung nodules, hospital report images, or Chinese requests such as 肺结节诊断/肺部CT/胸部CT/DICOM分析. Use the bundled script to inspect DICOM pixels directly, generate lung-window/MIP review images, summarize acquisition metadata, reconcile findings against hospital radiology reports, draft structured bilingual reports, and keep management advice framed as AI-assisted decision support requiring radiologist or treating-physician confirmation.
---

# Lung Nodule DICOM

## Purpose And Safety Boundary

Use this skill to review chest CT DICOM data for pulmonary nodule decision support. The workflow parses DICOM series, creates lung-window contact sheets and MIP images, extracts CT acquisition metadata, produces rough candidate crops, reconciles findings against hospital radiology reports, and drafts a structured report.

This skill is not a medical device. Do not present automated findings as a diagnosis, cancer exclusion, or management order. Separate image facts from inference, state uncertainty, and direct patient-specific decisions to a qualified radiologist or treating physician.

Read these references when the task needs them:

- [references/clinical_framework.md](references/clinical_framework.md): clinical context, measurement, guideline caveats, report language.
- [references/hospital_report_reconciliation.md](references/hospital_report_reconciliation.md): report-image transcription, PHI redaction, disagreement analysis.

## Input Triage

Before analysis, classify the user input and collect only context that changes interpretation.

| Input | Required action | Output |
|---|---|---|
| DICOM file or folder | Run the analyzer and inspect generated images before answering. | `study_summary.json`, review PNGs, draft report. |
| Hospital report image only | Explain that DICOM pixels are needed for image review; extract/redact report findings if useful. | Redacted report summary, no independent image claims. |
| DICOM plus report image/text | Analyze DICOM, transcribe only nodule-relevant report text, then reconcile. | Image findings plus `report_reconciliation.md`. |
| Prior and current CT studies | Analyze current study, compare against prior images/reports when available. | Change assessment with dates and measurement limits. |

Ask for missing context only when it affects interpretation: age, smoking history, prior cancer, immunosuppression, symptoms/infection, CT date, screening versus incidental study, and prior CT availability. Do not expose patient name, ID, accession number, address, phone, or hospital identifiers unless the user explicitly needs them.

🔴 CHECKPOINT · Stop before management advice when any of these are true: suspicious morphology, interval growth, part-solid nodule, known cancer, immunosuppression, severe symptoms, missing thin-section CT, or disagreement with the hospital report. In that case, frame the output as review questions for a clinician rather than a follow-up plan.

## Core Workflow

1. **Confirm usable CT data.**
   - Prefer diagnostic chest CT or LDCT axial thin sections, ideally slice thickness `<= 1.5 mm`.
   - Reject or down-weight scouts/localizers, incomplete lung coverage, thick-slice-only reconstructions, severe motion, reformats without axial source images, and non-chest CT series.
   - If the input is not usable for nodule review, say why and stop independent interpretation.

2. **Run the analyzer.**

```bash
python3 scripts/analyze_lung_ct_dicom.py /path/to/dicom-or-folder --out ./lung_ct_review
```

   For hospital report images, inspect the image first, transcribe only nodule-relevant text, redact PHI, then pass verified text or JSON:

```bash
python3 scripts/analyze_lung_ct_dicom.py /path/to/dicom-or-folder --out ./lung_ct_review --hospital-report-text ./hospital_report_text.md
python3 scripts/analyze_lung_ct_dicom.py /path/to/dicom-or-folder --out ./lung_ct_review --hospital-report-json ./hospital_report_findings.json
```

   Optional OCR is only a bootstrap; verify it against the report image before relying on it:

```bash
python3 scripts/analyze_lung_ct_dicom.py /path/to/dicom-or-folder --out ./lung_ct_review --hospital-report-image ./report_page1.png --ocr-language chi_sim+eng
```

3. **Inspect script outputs as evidence, not as final truth.**
   - `study_summary.json`: series inventory, selected series, warnings, DICOM acquisition metadata.
   - `series_*_contact_sheet_lung.png`: axial lung-window review sheets.
   - `series_*_mip_lung.png`: thin-slab maximum-intensity projections for search support.
   - `candidates.csv` and `candidate_*.png`: rough candidate detections when optional SciPy detection runs.
   - `hospital_report_findings.json`: redacted report findings when report input is provided.
   - `report_reconciliation.md`: comparison between report findings and AI candidate evidence.
   - `report_draft.md`: starting point for a structured answer.

4. **Manually validate image findings.**
   - Review contact sheets and MIPs before trusting `candidates.csv`.
   - Record location, type, long/short axis in mm, solid component if present, margins, calcification/fat, perifissural morphology, pleural relation, multiplicity, associated findings, and confidence limits.
   - Treat vessels, airway walls, fissural lymph nodes, scars, atelectasis, infection, motion, and partial volume as common mimics.
   - Compare with prior CT when available; persistence and growth often matter more than a single measurement.

5. **Reconcile hospital reports carefully.**
   - Treat the formal hospital radiology report as the primary clinical interpretation.
   - Present AI differences as possible review questions, not as proof the report is wrong.
   - State whether the analyzer found a possible candidate match, likely reasons for mismatch, and whether original thin-section DICOM needs radiologist review.

6. **Draft the response.**
   - Start with technical adequacy and the most clinically relevant findings.
   - Separate observed image facts from guideline-based inference and follow-up discussion.
   - Use Chinese when the user writes in Chinese.
   - Include a safety line that AI-assisted review cannot replace radiologist/doctor confirmation.

## Failure Handling

| Trigger | First action | If still unresolved |
|---|---|---|
| Dependencies missing | Run `python3 -m pip install -r scripts/requirements.txt`. | Report missing package names and continue with any already generated images only if adequate. |
| Compressed DICOM cannot decode | Install a pixel handler such as `pylibjpeg[all]` or `gdcm`. | Stop pixel interpretation; summarize metadata and ask for uncompressed DICOM or compatible export. |
| Analyzer selects wrong series | Re-run with `--series <SeriesInstanceUID-or-displayed-hash>`. | Explain series ambiguity and avoid nodule claims. |
| Study too large or memory-limited | Re-run with `--max-slices 450` or a selected series. | Produce a limitation statement; do not infer absence of nodules from partial review. |
| OCR unavailable or noisy | Manually transcribe report findings from the image. | Use report-image visual review only; label unverified text as uncertain. |
| Candidate detector produces obvious false positives | Treat candidates as search hints and rely on manual image review. | Use `--no-candidates` and draft from contact sheets/MIPs. |
| Thin-section CT missing | Down-weight size/type confidence and document the limitation. | Ask for original thin-section axial DICOM before management-level advice. |
| Report and DICOM appear to be different dates | Stop reconciliation until dates are clarified. | Provide separate summaries without claiming disagreement. |

## Script Options

```bash
# Analyze a specific SeriesInstanceUID or displayed UID hash
python3 scripts/analyze_lung_ct_dicom.py ./DICOM --series 1.2.840... --out ./review

# Include patient identifiers in local output only when explicitly needed
python3 scripts/analyze_lung_ct_dicom.py ./DICOM --include-phi --out ./review

# Skip rough automated candidate detection
python3 scripts/analyze_lung_ct_dicom.py ./DICOM --no-candidates --out ./review

# Limit memory on large studies
python3 scripts/analyze_lung_ct_dicom.py ./DICOM --max-slices 450 --out ./review

# Tune candidate size/HU ranges when detector output is too broad or too narrow
python3 scripts/analyze_lung_ct_dicom.py ./DICOM --min-candidate-mm 3 --max-candidate-mm 30 --candidate-min-hu -700 --candidate-max-hu 200 --out ./review
```

The detector is intentionally conservative and unvalidated. It can help find soft-tissue-density objects inside a rough lung mask, but it cannot classify malignancy, reliably separate vessels/scars from nodules, or exclude subtle ground-glass nodules.

## Output Pattern

Use this structure unless the user requests a different format:

```markdown
# 肺结节CT辅助分析

## 结论摘要
- [1-3 bullets: dominant finding, whether nodules are present/suspected, and confidence limits.]

## 检查质量
- [Series used, CT type, slice thickness, contrast status, limitations.]

## 影像所见
| 结节 | 部位 | 类型 | 大小 | 形态/密度 | 对比既往 | 备注 |
|---|---|---|---|---|---|---|

## 与医院报告的关系
- [Use only when report text/image is provided. State matches, mismatches, and likely technical reasons.]

## 风险判断与随访讨论
- [Use only applicable guidelines. Explain why Fleischner or Lung-RADS does or does not apply.]

## 需要补充的信息
- [Prior CT, smoking history, cancer history, symptoms, original thin-section DICOM, etc.]

> AI辅助分析不能替代放射科医生阅片或临床诊断；如有症状、已知肿瘤病史或医生要求，请以专科医生意见为准。
```

## Do Not Do

- Do not diagnose cancer, benignity, infection, or treatment need from DICOM images alone.
- Do not state that no nodules exist when series quality, slice thickness, coverage, or decoding is inadequate.
- Do not treat automated `candidate_*.png` crops as confirmed nodules without manual review.
- Do not apply Fleischner guidance to patients under 35, immunocompromised patients, or known primary cancer cases without clearly stating it may not apply.
- Do not apply Lung-RADS to routine diagnostic CT unless the user asks for a screening-style approximation and the limitation is labeled.
- Do not copy PHI from report images into outputs unless explicitly required.
- Do not claim the hospital report is wrong; phrase discrepancies as items for radiologist review.
