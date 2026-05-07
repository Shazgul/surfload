# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added
- New host plugin: `rootz_so` (`rootz.so`) with direct upload and multipart upload support.
- New release notes file (`CHANGELOG.md`).

### Improved
- GitHub project setup for public-readiness (CI, templates, policy files, CODEOWNERS).
- Upload archive options: custom archive name, optional archive password, optional archive split size.
- Vikingfile account-linked upload behavior (`user` hash support).

## [0.1.0] - 2026-04-20

### Added
- Initial Surfload CLI release with multi-hoster upload support.
- Plugin architecture with bundled host plugins.
- Retry/backoff, chunked streaming, JSON/text result export.
- Optional zip/7z compression and account credential storage.
- Test suite for core streaming, credentials, compression, plugins, and resume behavior.
