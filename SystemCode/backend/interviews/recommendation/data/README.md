# Research job catalog

`experience-jobs.json` contains 100 research jobs from the same source as the local ranking model. These are not current vacancies.
The user explicitly selected the existing research data on 2026-10-01. Runtime use still requires an explicit catalog path; it is never selected as an implicit fallback.

The source is [Final_items.csv](https://raw.githubusercontent.com/brycekan123/DualOptimization_jobrec/c742feea3730e8d34ec2e8ae7e58c8c0ee8a53fd/dataset/Final_items.csv),
pinned to commit `c742feea3730e8d34ec2e8ae7e58c8c0ee8a53fd`, with SHA-256
`2207fff0d954f223496143f8a646d6314297e6b7c6be18fda4782bc292e1f352`.
The hash and 100-row count were checked against `research/kaggle_jobrec_v4/results/jobrec_v4/data_manifest.json` before import.

Import only converted CSV representations into the strict JSON contract: two lists were parsed with `ast.literal_eval` and numeric values used their original columns.
Work arrangements, skills, industries, academic levels and IDs were preserved. No synonym mapping, GPA scaling, requirement inference or retraining was performed.
`job_llm_output` is the unmodified description. The source has no job title, company or location, so titles identify only the industry and ID, such as `NLP · J0037`. The research-job prefix was removed on 2026-10-03 at the user's request.
Company and location remain empty; no real employer is invented. The original model requirements are stored in `requirements`.
The English source label is `DualOptimization research job catalog`; its language does not change the catalog's research status or ranking inputs.

This catalog does not establish effectiveness on real recruitment data. Model and dataset limitations are documented in `docs/recommendation.md`.
