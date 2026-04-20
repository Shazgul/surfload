# Contributing

## Branch strategy

- `main`: stable branch, release-ready.
- `develop`: integration branch for completed features/fixes.
- Feature branches should branch off `develop` and merge back into `develop`.

Recommended branch naming:

- `feature/<topic>`
- `fix/<topic>`
- `chore/<topic>`

## Commit style

Use small, logical commits with clear prefixes:

- `feat:` new feature
- `fix:` bug fix
- `docs:` documentation change
- `test:` tests only
- `chore:` maintenance/refactor/tooling

Examples:

- `feat: add archive part size option`
- `fix: send vikingfile user hash in upload form`
- `docs: document archive password flags`

## Merge policy

- Do not commit directly to `main`.
- Merge features/fixes into `develop` first.
- Merge `develop` into `main` for releases.

## Basic local flow

```bash
# from updated develop
git checkout develop
git pull

git checkout -b feature/my-change
# ... work ...
git add .
git commit -m "feat: describe change"

git push -u origin feature/my-change
```
