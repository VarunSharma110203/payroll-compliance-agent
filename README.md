# 🌍 Payroll Compliance Agent

An AI-powered agent that automatically tracks government payroll regulatory updates across 27 countries and delivers daily digests to the team via Telegram.

## 🧩 The Problem

Payroll compliance is a moving target. Governments across Africa, UAE, and Southeast Asia regularly release updates to tax rules, statutory deductions, and labour regulations. Manually tracking these changes across 27 countries was:
- Time-consuming (hours per week per specialist)
- Error-prone (easy to miss critical updates)
- Unscalable as we expanded to new geographies

## 🚀 The Solution

An automated compliance agent that:
1. Fetches country-wise regulatory changes daily using the **Gemini API**
2. Runs automatically every day via **GitHub Actions** — zero manual effort
3. Pushes a structured digest to a **Telegram group bot** used by payroll specialists and PMs

## 🏗️ Architecture
GitHub Actions (Daily Trigger)
↓
agent.py — Gemini API call per country
↓
countries.py — 27 country configurations
↓
telegram_reporter.py — formats & sends digest
↓
Telegram Group (Payroll + Product Team)

## 🌐 Countries Covered

27 countries across:
- **Africa** — Nigeria, Kenya, South Africa, Ghana, and more
- **UAE & Middle East**
- **Southeast Asia** — India, Philippines, Malaysia, and more

## 📦 Tech Stack

- **Python** — core agent logic
- **Gemini API** — AI-powered regulatory research
- **GitHub Actions** — daily scheduled automation
- **Telegram Bot API** — team notifications

## 💥 Impact

- ✅ Eliminated manual compliance tracking across 27 countries
- ✅ Used daily by payroll specialists and product managers
- ✅ Zero missed compliance updates since deployment
- ✅ Scales to any new country by adding a config entry in `countries.py`

## ⚙️ Setup

```bash
pip install -r requirements.txt
```

Set the following as GitHub Secrets:
- `GEMINI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## 🔄 How It Runs

The GitHub Actions workflow triggers daily at a scheduled time, runs `agent.py`, and automatically posts updates to the Telegram group. No manual intervention needed.

---

*Built by Varun Sharma — APM at PeopleHum*
