# Clinical Framework

## Safety Boundary

Use this skill as radiology decision support, not as an autonomous doctor. Do not diagnose cancer or benignity from DICOM images alone. Escalate urgent clinical symptoms such as hemoptysis, severe dyspnea, chest pain, fever/sepsis concern, or rapidly worsening condition to immediate clinical care.

Default Chinese phrasing:

> 这是基于DICOM影像的AI辅助分析，不能替代放射科医生正式报告或临床医生诊断。随访或治疗决策需要结合病史、既往影像和医生评估。

## Minimum Clinical Context

Ask for or note whether these are unknown:

- Age and sex
- Smoking history or occupational exposure
- Prior malignancy or immunosuppression
- Symptoms or infection/inflammation history
- Screening LDCT versus incidental CT
- Prior CT date and availability for comparison
- CT acquisition date, slice thickness, reconstruction kernel, contrast status

## DICOM Review Procedure

1. Load the study with `scripts/analyze_lung_ct_dicom.py`.
2. Pick the best axial chest CT series. Prefer thin-section lung reconstruction. Do not interpret scouts/localizers as diagnostic series.
3. Review lung-window contact sheets and MIP images for all lobes.
4. Check mediastinal window when available for calcification, fat, lymph nodes, pleural effusion, or chest-wall invasion.
5. Confirm automated candidates manually. Ignore candidates explained by vessels, fissures, airway walls, scars, atelectasis, motion, or partial volume.
6. Compare with prior CT. Document stability, growth, new nodules, and resolving inflammatory findings.

## Nodule Description Checklist

For each clinically relevant nodule, capture:

- Location: side, lobe, segment if visible; central/peripheral; pleural/fissural relation
- Type: solid, pure ground-glass, part-solid, calcified, fat-containing
- Size: long axis and short axis in mm on thin-section lung window; for part-solid, measure both total lesion and solid component
- Number: solitary, multiple, diffuse/miliary
- Morphology: smooth, lobulated, spiculated, irregular, perifissural/triangular/oval
- Density: calcification pattern, fat, cavitation, bubble lucency, air bronchogram
- Associated findings: lymphadenopathy, emphysema, fibrosis, infection, atelectasis, pleural effusion
- Change: new, stable, growing, shrinking; include prior CT date and measurement method

## Measurement Notes

- Prefer axial thin sections <= 1.5 mm and lung window.
- Use average diameter for many guideline thresholds: `(long axis + short axis) / 2`, rounded to nearest mm.
- For part-solid nodules, the solid component drives risk and follow-up more than total size.
- For multiple nodules, management usually follows the most suspicious nodule, not necessarily the largest.
- Very small differences can reflect slice thickness, kernel, inspiration, or measurement variability.

## Guideline Use

Verify current guidelines and cite sources when giving patient-specific follow-up advice.

Fleischner Society 2017 is commonly used for incidentally detected nodules in adults, but it generally does not apply to patients younger than 35, immunocompromised patients, or patients with known primary cancer. Use the incidental-nodule pathway only when that context fits.

ACR Lung-RADS is for lung cancer screening LDCT programs. Do not apply Lung-RADS to a routine diagnostic CT unless the user explicitly asks for a screening-style approximation and you label it as such.

Use clinical judgment language:

- "符合/接近 [guideline] 的随访思路" when context is incomplete.
- "需要结合既往片和病史确认" when prior imaging or risk history is missing.
- "建议由放射科医生复核原始薄层DICOM" for indeterminate or high-risk features.

## High-Risk Image Features

Mention these clearly when present or suspected:

- Solid component in a part-solid nodule
- Spiculation, lobulation, pleural retraction, vessel convergence
- Interval growth or new persistent nodule
- Upper-lobe location in a high-risk patient
- Associated lymphadenopathy or pleural disease
- Multiple random nodules in a known cancer patient

## Common Mimics

Avoid overcalling:

- Vessels seen in cross-section
- Fissural lymph nodes with smooth triangular/oval perifissural shape
- Scar, fibrosis, atelectasis, dependent opacity
- Infection/inflammation, especially transient ground-glass opacity
- Mucus plugging or airway wall thickening
- Motion artifact and partial-volume effects

## Report Drafting

Use concise clinical wording. Separate findings from recommendations.

Chinese structure:

```markdown
## 结论摘要
- [dominant finding and uncertainty]

## 检查质量
- [series, thickness, contrast, limitations]

## 影像所见
- [nodule-by-nodule facts]

## 风险判断与建议
- [applicable guideline reasoning, if context fits]

## 建议补充
- [prior CT, clinical history, formal radiology review]
```

English structure:

```markdown
## Impression
- [dominant finding and uncertainty]

## Technical Adequacy
- [series, slice thickness, contrast, limitations]

## Findings
- [nodule-by-nodule facts]

## Risk Context And Follow-Up
- [applicable guideline reasoning, if context fits]

## Needed Context
- [prior CT, clinical history, formal radiology review]
```
