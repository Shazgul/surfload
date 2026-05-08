# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added
- New host plugin: `rootz_so` (`rootz.so`) with direct upload and multipart upload support.
- New release notes file (`CHANGELOG.md`).
- New upload flag `--keep-temp` to retain generated temporary archive/split files for inspection and debugging.

### Improved
- GitHub project setup for public-readiness (CI, templates, policy files, CODEOWNERS).
- Upload archive options: custom archive name, optional archive password, optional archive split size.
- Vikingfile account-linked upload behavior (`user` hash support).

### Changed
- Deactivated `send_now` host in active runtime setup (plugin registry, CLI aliases, default/example config, docs, and tests).

### Fixed
- Fixed FileQ uploads that could be truncated to chunk size (~1MB) by switching to streaming multipart upload with explicit content length.
- Fixed DailyUploads uploads that could be truncated to chunk size (~1MB) by switching to streaming multipart upload with explicit content length.

## [0.1.0] - 2026-04-20

### Added
- Initial Surfload CLI release with multi-hoster upload support.
- Plugin architecture with bundled host plugins.
- Retry/backoff, chunked streaming, JSON/text result export.
- Optional zip/7z compression and account credential storage.
- Test suite for core streaming, credentials, compression, plugins, and resume behavior.
