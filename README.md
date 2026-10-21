# NOAA water-level research

Public, reproducible evaluation of NOAA ETSS water-level forecasts against CO-OPS observations. The prospective protocol compares five fixed methods at Friday Harbor, Port Townsend and Seattle. This is research; no production accuracy claim has been accepted.

GitHub Actions acquires original receipts. Immutable GitHub Releases store raw evidence; the `data` branch stores original manifests and catalogs, and `reports` stores small health and evaluation summaries. Collection has no Mac Studio, Codex or self-hosted-runner dependency.

- [Prospective protocol](protocols/prospective-v1.1.md)
- [Operations and reproduction](docs/operations.md)
- [Archive format](docs/archive-format.md)
- [Implementation and handoff tracking](https://github.com/openwatersio/noaa-water-level-research/issues/1)
- [Actions](https://github.com/openwatersio/noaa-water-level-research/actions)
- [Evidence and report Releases](https://github.com/openwatersio/noaa-water-level-research/releases)

Migration starts with hosted shadow collection. The source boundary in `protocols/collector-transition.json` remains unset until a verified 48-hour overlap, remote restoration/replay and final handoff checks. The old local archive remains intact.

Run offline checks with Python 3.12+:

```sh
python -B -m unittest discover -s tools/etss -p 'test_*.py'
python -B -m unittest discover -s tests
```

Science source and frozen predictor inputs are pinned to the original Slackwater research baseline. See `NOTICE` and `protocols/source-provenance.json` for attribution. Source code is MIT licensed; NOAA data attribution and limitations are separate.
