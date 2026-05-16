# Hospital Report Image Reconciliation

## Purpose

Use this workflow when the user provides hospital radiology report images and asks why AI analysis differs from the official report. The hospital report is the primary clinical source; AI output is a review aid for locating possible reasons for disagreement.

## Image Extraction Workflow

1. Inspect each report image directly.
2. Extract only clinically relevant report content:
   - exam date and modality if visible
   - findings/impression about nodules, masses, ground-glass opacity, scars, inflammation, lymph nodes, pleura
   - location, type, size, change versus prior scans, and recommendations
3. Do not copy PHI into the working report text unless explicitly needed:
   - patient name
   - patient ID, accession number, visit number, inpatient/outpatient number
   - phone, address, identity number
   - hospital or department identifiers when not clinically needed
4. Save or pass the extracted content as text or structured JSON for the analyzer.

Prefer structured JSON when possible:

```json
{
  "report_text": "",
  "findings": [
    {
      "source_text": "右肺上叶见约9.5mm磨玻璃结节，较前略增大，建议随访。",
      "location": "右肺上叶",
      "nodule_type": "ground-glass",
      "size_text": "9.5mm",
      "dimensions_mm": [9.5],
      "change": "increased",
      "recommendation": "建议随访"
    }
  ]
}
```

## Commands

Run with manually verified report text:

```bash
python3 scripts/analyze_lung_ct_dicom.py ./DICOM --hospital-report-text ./hospital_report_text.md --out ./lung_ct_review
```

Run with structured findings:

```bash
python3 scripts/analyze_lung_ct_dicom.py ./DICOM --hospital-report-json ./hospital_report_findings.json --out ./lung_ct_review
```

Optional OCR for local image files:

```bash
python3 scripts/analyze_lung_ct_dicom.py ./DICOM --hospital-report-image ./report_page1.png --out ./lung_ct_review
```

OCR is only a bootstrap. Verify OCR output against the report image before using it for clinical comparison.

## How To Explain Differences

Use this order:

1. Confirm the compared CT date and DICOM series.
2. Quote or summarize the hospital report finding in redacted form.
3. State whether the analyzer found a possible candidate match and its slice/crop.
4. Explain likely reasons for mismatch:
   - AI candidate is a vessel, scar, airway wall, inflammation, or partial-volume artifact
   - report finding is ground-glass and the rough detector focuses on soft-tissue density
   - hospital used prior-study comparison or clinical context not available to AI
   - different series, slice thickness, reconstruction kernel, or CT date
   - tiny measurement differences from slice selection/windowing
5. End with a radiologist-confirmation recommendation for management decisions.

## Output Language

If the user writes in Chinese, answer in Chinese. Use terms such as:

- "医院放射科报告应作为主要临床依据"
- "AI候选灶仅作为复核线索，不能等同于确诊结节"
- "建议携带原始薄层DICOM和报告请放射科医生复核"
