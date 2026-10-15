# GitViz – see your Git repository live

GitViz opens a browser page that redraws itself while you type Git commands. It shows:

- **Working Directory → Staging Area → Repository**: where each of your files is right now
- **Commit graph**: your commits, branches, `HEAD` and `origin/master`
- **What just happened**: a plain-English line for every command you run

It only reads your repository and never changes anything.

## Install (once)

Open a Command Prompt and run:

```
pip install git+https://github.com/StanleySP-eng/gitviz
```

If `pip` is not recognised, use `py -m pip install git+https://github.com/StanleySP-eng/gitviz` instead.

## Use

1. Open a **second** Command Prompt next to the one you use for the lab.
2. Go to your lab folder and start GitViz:

   ```
   cd C:\Local_Git_Repository\lab1
   gitviz
   ```

   If `gitviz` is not recognised, run `py -m gitviz` instead.
3. A browser tab opens. Place it next to your first Command Prompt and carry on with the lab.
4. Press **Ctrl+C** in the GitViz window to stop it.

You can start GitViz before `git init`. The page tells you the folder is not a repository yet, and updates as soon as you run `git init`.

## Options

```
gitviz C:\path\to\repo     watch a different folder
gitviz --port 9000         use another port if 8765 is busy
gitviz --no-browser        do not open a browser tab automatically
```
