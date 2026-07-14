# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.8.5] - 2026-07-14

### Changed

- Updated `pytrain-ogr` dependency to improve compatibility and pick up upstream fixes.
  ([5e0fe8f](https://github.com/cdswindell/PyTrainApi/commit/5e0fe8f), [a876c25](https://github.com/cdswindell/PyTrainApi/commit/a876c25), [67dcc17](https://github.com/cdswindell/PyTrainApi/commit/67dcc17), [45c8c82](https://github.com/cdswindell/PyTrainApi/commit/45c8c82))
- Updated FastAPI constraints to handle post-`0.136.3` routing compatibility issues while preserving API stability.
  ([6b33c66](https://github.com/cdswindell/PyTrainApi/commit/6b33c66), [a876c25](https://github.com/cdswindell/PyTrainApi/commit/a876c25), [67dcc17](https://github.com/cdswindell/PyTrainApi/commit/67dcc17))
- Refreshed supporting dependencies (`python_multipart`, `ruff`, `pytest`, `tox`, `uvicorn`, `zeroconf`) to current
  patch/minor versions.
  ([67dcc17](https://github.com/cdswindell/PyTrainApi/commit/67dcc17), [45c8c82](https://github.com/cdswindell/PyTrainApi/commit/45c8c82))

[2.8.5]: https://github.com/cdswindell/PyTrainApi/releases/tag/v2.8.5
