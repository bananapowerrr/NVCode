# UPDATE-001F — GitHub Release pipeline

## Local build

```bash
python scripts/build_release.py --version 0.10.1 --out dist
```

Outputs:
- `dist/NVCode-0.10.1-src.zip`
- `dist/release.json` (UpdateChecker manifest)
- `dist/NVCode-0.10.1-src.zip.sha256`

## CI

Tag push `v0.10.1` → workflow `.github/workflows/release.yml`:
1. build zip + release.json
2. GitHub Release with assets

UpdateChecker default URL:
`https://github.com/bananapowerrr/NVCode/releases/latest/download/release.json`

## Notes

- Current package is **source-oriented** (src/ui/app), not a frozen Windows exe.
- PyInstaller / exe packaging can replace zip contents later without changing manifest schema.
