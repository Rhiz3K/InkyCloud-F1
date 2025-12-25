# Public Repository Setup Checklist

Checklist for setting up the public GitHub repository `Rhiz3K/InkyCloud-F1`.

## 1. Branch Protection (Settings → Branches)

### Rule for `main` branch
- [ ] Add branch protection rule for `main`
- [ ] **Require a pull request before merging**
  - [ ] Require at least 1 approval
  - [ ] Dismiss stale pull request approvals when new commits are pushed
- [ ] **Require status checks to pass before merging**
  - [ ] Require branches to be up to date before merging
  - [ ] Add required status checks: `test`, `lint`
- [ ] **Do not allow bypassing the above settings**

## 2. Security Settings (Settings → Code security and analysis)

- [ ] Enable **Dependabot alerts**
- [ ] Enable **Dependabot security updates**
- [ ] Enable **Secret scanning**
- [ ] Enable **Push protection**

## 3. Repository Settings (Settings → General)

### Features
- [ ] Enable **Issues**
- [ ] Enable **Discussions**
- [ ] Disable **Wiki**

### Pull Requests
- [ ] Allow **squash merging**
- [ ] **Automatically delete head branches**

## 4. About Section (gear icon on repo page)

- [ ] Description: `F1 E-Ink Calendar - Generate 800x480 1-bit BMP images for E-Ink displays`
- [ ] Website: `https://f1.inkycloud.click`
- [ ] Topics: `f1`, `formula1`, `eink`, `esp32`, `fastapi`, `python`

## 5. Verify CI Workflows

- [ ] CI runs on pull requests
- [ ] CI runs on push to main
- [ ] Tests pass
- [ ] Linting passes

## 6. First Release

```bash
git tag -a v1.0.0 -m "Initial public release"
git push origin v1.0.0
```

- [ ] Create GitHub Release from tag
- [ ] Add release notes

## 7. Final Verification

```bash
# Fresh clone test
git clone https://github.com/Rhiz3K/InkyCloud-F1.git /tmp/test-clone
cd /tmp/test-clone
python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
pytest && ruff check .

# Docker test
docker build -t inkycloud-f1:test .
docker run -p 8000:8000 inkycloud-f1:test
# Verify http://localhost:8000 works
```

- [ ] All tests pass
- [ ] Docker build works
- [ ] App runs correctly
