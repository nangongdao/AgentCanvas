# Publishing to PyPI

## Prerequisites

1. Create PyPI account at https://pypi.org/account/register/
2. Generate API token at https://pypi.org/manage/account/token/
3. Configure credentials (choose one):

### Option A: Using keyring (recommended)

```bash
pip install keyring
keyring set https://upload.pypi.org/legacy/ __token__
# Enter your token when prompted: pypi-...
```

### Option B: Using .pypirc file

Create `~/.pypirc`:

```ini
[pypi]
username = __token__
password = pypi-AgEIcHlwaS5vcmcC...your-token-here
```

## Build and Test

```bash
# Install build tools
pip install build twine

# Build distributions
python -m build

# Check distributions
twine check dist/*

# Test upload to TestPyPI (optional)
twine upload --repository testpypi dist/*
pip install --index-url https://test.pypi.org/simple/ agentcanvas
```

## Publish to PyPI

```bash
# Upload to PyPI
twine upload dist/*

# Verify installation
pip install agentcanvas
python -c "from agentcanvas import AgentCanvasClient; print('Success!')"
```

## Version Bumping

Before releasing a new version:

1. Update version in `pyproject.toml`
2. Update `CHANGELOG.md` with changes
3. Create git tag:

```bash
git tag -a v0.1.0 -m "Release v0.1.0"
git push origin v0.1.0
```

4. Clean old distributions:

```bash
rm -rf dist/
python -m build
twine upload dist/*
```

## Automated Publishing with GitHub Actions

Create `.github/workflows/publish.yml`:

```yaml
name: Publish to PyPI

on:
  release:
    types: [published]

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install build twine
      - name: Build package
        run: python -m build
      - name: Publish to PyPI
        env:
          TWINE_USERNAME: __token__
          TWINE_PASSWORD: ${{ secrets.PYPI_API_TOKEN }}
        run: twine upload dist/*
```

Add `PYPI_API_TOKEN` to repository secrets.

## Post-Release Checklist

- [ ] Package appears on https://pypi.org/project/agentcanvas/
- [ ] Installation works: `pip install agentcanvas`
- [ ] Import works: `from agentcanvas import AgentCanvasClient`
- [ ] Version matches: `python -c "import agentcanvas; print(agentcanvas.__version__)"`
- [ ] Documentation is up-to-date
- [ ] GitHub release created with changelog
