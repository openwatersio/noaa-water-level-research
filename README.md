# NOAA water-level research

Can NOAA's weather-aware water-level forecasts improve on Slackwater's tide predictions at Friday Harbor, Port Townsend and Seattle? This public research project collects forecasts before their target times, saves what was available when a prediction would have been made, and compares those predictions with NOAA gauge observations.

The intended outcome is a reproducible decision about whether a forecast method is accurate and dependable enough to justify further work on corrected water levels in Slackwater. The study can pass, fail or be inconclusive. No production accuracy claim has been accepted, and application integration requires a separate decision.

## What we are testing

Tide predictions describe the astronomical component of water level. This study tests whether NOAA's Extratropical Storm Surge model (ETSS), or a correction based on recent gauge readings, improves predictions of the total water level people will observe. NOAA's Center for Operational Oceanographic Products and Services (CO-OPS) supplies the gauge readings used for inputs and later verification.

Each daily decision at 02:00 UTC predicts water level 3, 6, 12 and 24 hours ahead at the three gauges. Heights use meters relative to Mean Lower Low Water (MLLW). Five fixed methods are compared:

| Method | Prediction |
| --- | --- |
| Unchanged tide | Slackwater's tide prediction, the baseline every correction must beat. |
| NOAA full water level | The original ETSS total-water-level forecast, which already includes tide and its bias adjustment. |
| Last-24-hour correction | Future tide plus the mean of the last 24 hourly differences between observed water level and Slackwater tide. |
| Persistence | Future tide plus the most recent hourly observed-minus-tide difference. |
| Fixed surge-change correction | Future tide plus the existing three-hour detided observation anchor and the change in issued ETSS surge. |

The [frozen protocol](protocols/prospective-v1.1.md) specifies the formulas, dates, observation handling and acceptance rules. Forecasts or inputs received after a decision cannot be used to improve that decision retrospectively. Missing or unusable inputs fall back to unchanged tide and still count in the evaluation; missing verification observations are reported separately.

## What the results will tell us

The final report will show which methods meet the preregistered requirements, where they fail, and where there is too little evidence to decide. Results are reported separately for each gauge and forecast horizon, including periods when water levels change rapidly.

Acceptance requires all of the following under the full protocol:

- Mean absolute error (MAE) and root mean square error (RMSE, which penalizes large misses more heavily) must be no worse than unchanged tide at every gauge and horizon, both overall and during rapid events. The overall 24-hour RMSE must be strictly lower at every gauge.
- Prediction intervals fixed using November data must cover at least 90% of confirmation observations overall and during rapid events at every gauge and horizon. The report shows interval width alongside coverage, so a very wide interval cannot be mistaken for a precise prediction.
- Each gauge and horizon needs at least 20 scorable confirmation dates and five rapid-event dates across at least three episodes. Insufficient samples make the evidence inconclusive.

The report also includes fallback rates and reasons, missing observations, unknown event labels, large errors, worst episodes, and sensitivity to leaving out individual dates. Comparisons with the simpler corrections show whether ETSS adds value beyond information already available from the gauges.

A passing result supports further product engineering for the tested stations and conditions. A failing result identifies requirements the method did not meet. An inconclusive result explains the missing coverage or sample size; the study does not extend its dates to obtain a better result. All outcomes retain the original evidence and instructions for reproducing the calculations.

## Research timeline

| Stage | Dates | Purpose |
| --- | --- | --- |
| Development and collection handoff | Through October 31, 2026 | Verify collection, archives and reproducibility. Development scores cannot establish acceptance. |
| Calibration | November 1 through November 30, 2026 | Estimate uncertainty widths using the fixed methods. |
| Freeze | After December 3 at 13:00 UTC and before December 4 at 02:00 UTC | Publish fixed uncertainty widths and code/input hashes before examining confirmation outcomes. |
| Confirmation | December 4, 2026 through January 31, 2027 | Test the frozen methods and intervals on the prospective cohort. |
| Final evaluation | Early February 2027, once truth and event-label support are available | Publish the confirmation results and supporting evidence. |

The final date depends on complete verification observations and the protocol's maturity checks. Missing prerequisites are disclosed as unavailable or inconclusive evidence; they do not silently move the research dates.

## How to read the current status

The project is in development and migration. Manual hosted acquisition, full archive verification and replay from reviewed code have passed. Automatic scheduled operation and the 48-hour overlap acceptance remain unverified; follow [handoff issue #1](https://github.com/openwatersio/noaa-water-level-research/issues/1) for the dated evidence and remaining checks. The [source boundary](protocols/collector-transition.json) is unset, so local receipts remain primary and hosted receipts are retained as shadow evidence.

Use these views to answer different questions:

| Question | Where to look | What to check |
| --- | --- | --- |
| Is collection starting automatically? | [Scheduled collection runs](https://github.com/openwatersio/noaa-water-level-research/actions/workflows/collect.yml?query=event%3Aschedule) | An actual scheduled run, its completion time and conclusion. A manual run does not prove the schedule works. |
| Is the archive current and intact? | [Health reports](https://github.com/openwatersio/noaa-water-level-research/tree/reports/health), [audit runs](https://github.com/openwatersio/noaa-water-level-research/actions/workflows/audit.yml), and [health issues](https://github.com/openwatersio/noaa-water-level-research/issues?q=is%3Aissue+is%3Aopen+%22Collection+health%22) | Open the newest dated JSON report. Check its audit time, latest receipt and gauge sample times, reported problems and missed collection windows. |
| Are the forecasts accurate? | [Evaluation reports](https://github.com/openwatersio/noaa-water-level-research/tree/reports/evaluation) | Read the phase, dates, sample counts, fallback rates and research verdicts in `report.md`. Development results are preliminary. |
| Can someone verify the result? | [Evidence and report Releases](https://github.com/openwatersio/noaa-water-level-research/releases) and [reproduction instructions](docs/operations.md) | Each report identifies its code, data snapshot, inputs and original receipt hashes. |

In a health report, `asOf` is the audit time, `latestArchivedObservationRunAt` is the last archived tick with all three gauge requests complete, and `samples` gives the newest observed sample time at each gauge. `healthy` describes the checks at that audit time. `action_required`, `problems` and `missedSlots` expose failures or gaps. A stale report or an old green workflow does not establish current health; the monitor itself depends on GitHub Actions.

A newest-cycle HTTP 404 classified as `expected_unpublished` records that NOAA had not published that cycle at the request time. It is retained acquisition evidence. Export contract diagnostics are also retained and must be interpreted using the provider-confirmed mapping. A successful collection job establishes neither decision-time eligibility nor forecast accuracy.

## How the evidence is kept

GitHub-hosted Actions is configured to collect hourly at minute 50 UTC and audit at minute 25. Immutable GitHub Releases hold the original response bodies, including errors and revisions. The [`data` branch](https://github.com/openwatersio/noaa-water-level-research/tree/data) holds manifests and archive catalogs; the [`reports` branch](https://github.com/openwatersio/noaa-water-level-research/tree/reports) holds small health and evaluation summaries. Large report bundles also live in Releases, keeping the code checkout small.

The hosted pipeline runs without a Mac Studio, Codex or self-hosted runner. During migration, the local collector continues until verified overlap, a published future source boundary and final import checks allow it to stop. Original local files and shadow receipts are preserved. GitHub schedules can be delayed or missed; actual receipt times and every acquisition gap remain part of the evidence.

See the [operations runbook](docs/operations.md) for recovery and handoff, and the [archive format](docs/archive-format.md) for storage and hash verification.

## Reproduce and contribute

Run offline checks with Python 3.12+:

```sh
python -B -m unittest discover -s tools/etss -p 'test_*.py'
python -B -m unittest discover -s tests
```

For archive restoration and a pinned report replay, follow [Operations and reproduction](docs/operations.md). Changes to scientific formulas, eligibility or acceptance rules need prospective review before their outcomes are inspected.

Science source and frozen predictor inputs are pinned to the original Slackwater research baseline. See [NOTICE](NOTICE) and [source provenance](protocols/source-provenance.json) for attribution. Source code is [MIT licensed](LICENSE); NOAA data attribution and limitations are separate.
