# Pushing this to GitHub

Step-by-step from the zip file to a live repo.

## 1. Unzip somewhere sensible

On your Pi or dev machine:

```bash
cd ~
unzip Tado-Heating-Control.zip
cd Tado-Heating-Control
```

## 2. Create an empty repo on GitHub

1. Go to https://github.com/new
2. Name it `Tado-Heating-Control` (or whatever you like — but remember to update URLs in README.md)
3. Leave it **empty** — no README, no .gitignore, no license (we already have those)
4. Click *Create repository*
5. Copy the SSH or HTTPS URL it gives you (looks like `git@github.com:YOUR-USERNAME/Tado-Heating-Control.git`)

## 3. Initialise git and push

```bash
# If you haven't set your git identity globally, do that first:
git config --global user.name "Your Name"
git config --global user.email "you@example.com"

# Inside the unzipped folder:
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin git@github.com:YOUR-USERNAME/Tado-Heating-Control.git
git push -u origin main
```

If using HTTPS instead of SSH, swap the remote URL for the `https://...` one GitHub gave you. You'll be prompted for your username and a [personal access token](https://github.com/settings/tokens) (not your password).

## 4. Update the README and LICENSE

Open `README.md` and replace `YOUR-USERNAME` with your actual GitHub username in the clone URL.

Open `LICENSE` and replace `B` on line 3 with your real name (or GitHub handle) — this is the copyright holder line.

Commit and push:

```bash
git add README.md LICENSE
git commit -m "Set author and clone URL"
git push
```

## 5. Verify nothing sensitive slipped in

```bash
# Make sure config.yaml and any refresh tokens are NOT tracked
git ls-files | grep -E '(config\.yaml|refresh_token|\.token)'
```

That should return nothing. If it returns anything, your `.gitignore` is being bypassed — stop and fix before making the repo public.

## 6. (Optional) Make it public

By default the repo is private. If you want to share it, Settings → General → Danger Zone → Change visibility.

## Future workflow

```bash
# Make a change, test it, then:
git add .
git commit -m "Describe what changed"
git push
```

On the Pi, to pull updates:

```bash
cd /opt/heating-brain
sudo -u heating-brain git pull
sudo -u heating-brain ./venv/bin/pip install -r app/requirements.txt  # if deps changed
sudo systemctl restart heating-brain
```
