# Setting Up Shannon on a New Mac Mini

**Written for:** Ron's son  
**Time to complete:** ~45 minutes (most of that is waiting for downloads)  
**What you'll have at the end:** A local AI agent with persistent memory, running entirely on your machine. No cloud. No subscriptions. Yours.

---

## What You're Building

- **Shannon** — an AI that remembers everything you tell it, stored on your own drive
- **Ollama** — runs AI models locally on your Mac (no internet needed after setup)
- **DigitalID** — your project (separate repo, same foundation)

---

## Step 1 — Open Terminal

Press `Cmd + Space`, type `Terminal`, hit Enter.

You'll use this a lot. Get comfortable with it.

---

## Step 2 — Install Homebrew (Mac's package manager)

Paste this into Terminal and hit Enter:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Follow the prompts. When it asks for your password, type it (you won't see the letters — that's normal).

When it's done, run:
```bash
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
source ~/.zprofile
brew --version
```

You should see a version number. If you do, Homebrew is installed. ✅

---

## Step 3 — Install Python and Git

```bash
brew install python git
python3 --version
git --version
```

Both should show version numbers. ✅

---

## Step 4 — Install Ollama (runs AI models locally)

Go to **https://ollama.com** and download the Mac version, or run:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Then start it:
```bash
ollama serve &
```

Pull the AI model (this is the big download — ~20GB, takes 10-20 min on fast internet):
```bash
ollama pull qwen2.5:32b
```

Go get a snack. Come back when it says `success`.

Test it works:
```bash
ollama run qwen2.5:32b "Say hello in one sentence."
```

If it responds, Ollama is working. ✅

---

## Step 5 — Set Up Shannon

```bash
# Go to your home folder
cd ~

# Create a development folder
mkdir development
cd development

# Clone Shannon
git clone https://github.com/SAMLLC-PROD/Shannon.git
cd Shannon

# Create a virtual environment (keeps Shannon's dependencies separate)
python3 -m venv .venv
source .venv/bin/activate

# Install Shannon
pip install -e .

# Run the tests — should say "37 passed"
pip install pytest
pytest
```

If you see `37 passed` — Shannon is installed correctly. ✅

---

## Step 6 — Run Your First Shannon Agent Session

```bash
cd ~/development/Shannon
source .venv/bin/activate
python -m shannon.agent
```

You'll see something like:
```
=== Shannon Agent ===
Backend: ollama / qwen2.5:32b
Shannon: 0 entries in LTM
Type 'quit' to exit
```

Try typing: `Tell me about the Zeckendorf theorem`

The AI responds locally. Nothing left your machine. ✅

---

## Step 7 — Set Up Your Workspace (your soul file)

Shannon gets smarter about *you* the more you tell it. Create your workspace:

```bash
mkdir -p ~/.openclaw/workspace/memory
```

Create your user file — tell Shannon who you are:
```bash
cat > ~/.openclaw/workspace/USER.md << 'USEREOF'
# USER.md

- **Name:** [your name]
- **Project:** DigitalID — federal digital identity on Lattice infrastructure
- **Background:** [a few sentences about yourself]
- **What I need from my agent:** [what do you want it to help with?]
USEREOF
```

Edit it to fill in the blanks:
```bash
open -a TextEdit ~/.openclaw/workspace/USER.md
```

Next time you run `python -m shannon.agent`, it will know who you are.

---

## Step 8 — Clone DigitalID

```bash
cd ~/development
git clone git@github.com:SAMLLC-PROD/DigitalID.git
cd DigitalID
```

Read the README first:
```bash
open README.md
```

That's your project. Shannon is the AI foundation under it.

---

## Step 9 — Make Ollama Start Automatically

So you don't have to start it manually every time:

```bash
cat > ~/Library/LaunchAgents/com.ollama.plist << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ollama</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/ollama</string>
        <string>serve</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
PLIST

launchctl load ~/Library/LaunchAgents/com.ollama.plist
```

---

## Daily Use

**Start a Shannon session:**
```bash
cd ~/development/Shannon
source .venv/bin/activate
python -m shannon.agent
```

**Save something important to memory:**
```bash
python scripts/heartbeat.py --save "what you want to remember" --tags "tag1,tag2"
```

**Check what Shannon remembers:**
```bash
python -c "from shannon.store import stats; print(stats())"
```

---

## Useful Commands Cheat Sheet

```bash
ollama list              # see what models you have
ollama pull mistral:7b   # pull a smaller/faster model
ollama run mistral:7b    # quick chat without Shannon

python -m shannon.agent  # Shannon agent with full LTM
python -m shannon.llm    # check LLM backend status
pytest                   # run all tests
```

---

## If Something Goes Wrong

**"command not found: python3"**
→ Run `brew install python` again

**"could not connect to ollama"**
→ Run `ollama serve &` in a separate terminal window

**"No module named shannon"**
→ Make sure you ran `source .venv/bin/activate` first

**Tests failing**
→ Run `pip install -e .` again from the Shannon folder

---

## What's Next

Once you're comfortable with Shannon running locally, read:
- `ARCHITECTURE.md` — how Shannon's memory system works
- `~/development/DigitalID/README.md` — your project brief
- `~/development/DigitalID/ARCHITECTURE.md` — technical design

Ask your agent questions. Ask it to explain the code. Ask it to help you build.

That's what it's there for.

---

*Setup guide by Guy Shannon — built for Ron's son, March 2026*
