# GhostRing setup

**Just want to try it?** Use the hosted demo linked in the submission. Or run it locally with no keys: install the requirements, copy `.env.example` to `.env`, set `LLM_MODE=mock`, and start the app (see below). Live PayPal refunds need sandbox credentials; judges get any test credentials from the Devpost testing instructions, never from this repository.

## Run it locally

```bash
pip install -r requirements.txt
python prepare_demo_data.py --csv data/creditcard.csv   # builds data/demo_tx.csv
cp .env.example .env                                    # then fill in keys (on Windows: copy)
python seed_sandbox.py create 10                        # each payment can be refunded once, so seed enough for testers
python seed_sandbox.py capture
python -m uvicorn app:app --reload
```

Open <http://127.0.0.1:8000>. With `LLM_MODE=mock` the app runs with no API keys.

> **Windows:** create `.env` in Notepad and make sure it is not saved as `.env.txt`.

`creditcard.csv` comes from the [Kaggle Credit Card Fraud dataset](https://www.kaggle.com/datasets/mlg-ulb/creditcardfraud) (free Kaggle login required).

### `.env` settings

Each label appears once, with no quotes or spaces. Never commit this file.

```env
LLM_MODE=gemini                  # or "mock" for no API key
GEMINI_API_KEY=...
GEMINI_MODEL=...                 # a model your key lists, e.g. gemini-flash-latest
PAYPAL_CLIENT_ID=...
PAYPAL_SECRET=...
PAYPAL_BASE=https://api-m.sandbox.paypal.com
PAYPAL_CURRENCY=...              # a currency your sandbox accounts accept (e.g. USD or INR)
REFUND_MIN_RISK=60
REFUND_MIN_SIM=0.70
```

### List the models your Gemini key can use

```bash
python -c "from dotenv import load_dotenv; load_dotenv(); import os,requests; r=requests.get('https://generativelanguage.googleapis.com/v1beta/models', headers={'x-goog-api-key':os.getenv('GEMINI_API_KEY')}, params={'pageSize':200}); print(r.status_code); [print(m['name']) for m in r.json().get('models',[]) if 'generateContent' in m.get('supportedGenerationMethods',[])]"
```

## Seed live sandbox payments

A refund needs a captured payment, and a capture needs a buyer to approve the order, so this step is manual.

1. Run `python seed_sandbox.py create 5`. It deletes any old `data/captures.json` and `data/refunds.json`, creates sandbox orders for the top flagged rows that meet the refund rules, and prints approval links.
2. Open each link in a private window and approve it as a sandbox **Personal (buyer)** account. The seller and the buyer must be able to transact in the configured currency. Use only the links from the latest `create` run.
3. Run `python seed_sandbox.py capture`. It captures the approved orders and writes `data/captures.json`. It is safe to run again if you approved some links late.
4. Restart the app.

> Each linked payment can be fully refunded only once. If you plan to deploy these payments, do not click refund on them while testing locally. Seed fresh payments right before you deploy.

## Sandbox setup notes

- Use sandbox keys (the **Sandbox** toggle in the PayPal developer dashboard), not Live keys.
- The sandbox Business account, the Personal buyer account, and `PAYPAL_CURRENCY` must be compatible. A **US Business + US Personal + USD** setup works. India-based sandbox accounts reject USD orders ("This seller doesn't accept payments in your currency").
- Orders are locked to the currency they were created in. After changing `PAYPAL_CURRENCY`, create new orders.

## Deploy on Render

1. Make the repository **public** and add a `LICENSE` file (MIT) at its root so GitHub shows the licence in the About box. Push it to GitHub, including `data/demo_tx.csv` and `data/captures.json`. Do **not** commit `.env` or `data/refunds.json`: check that `.gitignore` lists both. Make sure `requirements.txt`, `render.yaml`, `LICENSE`, `.env.example` and `static/index.html` are included.
2. In Render, create a **Web Service** from the repository (the included `render.yaml` sets the build and start commands and the Python version).
3. In the Render dashboard, set `GEMINI_API_KEY`, `GEMINI_MODEL`, `PAYPAL_CLIENT_ID`, `PAYPAL_SECRET` and `PAYPAL_CURRENCY`, using the same values as your working local `.env`. A wrong `PAYPAL_CURRENCY` makes refunds fail. The free instance sleeps when idle, so the first request can take up to a minute. Keep the demo running, with fresh seeded payments, through the end of judging (15 December 2026).
4. Open the live link in a private window and check that the flags load, an explanation appears, and the "PayPal record" box shows the payment as `COMPLETED`.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Page is blank | `static/index.html` is empty or missing |
| `Directory 'static' does not exist` | The `static` folder is not next to `app.py` |
| Gemini 404 | The model name is not on your key's list |
| Gemini 400 `unexpected model name format` | A duplicated label or stray character in `.env` |
| Gemini timeout | Slow model or network; try a `-lite` model |
| PayPal `ORDER_NOT_APPROVED` or "buyer has not approved yet" | Capture ran before every link was approved; approve the rest and run `capture` again |
| "seller doesn't accept payments in your currency" | Currency not supported by the seller and buyer sandbox accounts; set `PAYPAL_CURRENCY` |
| Every row says "Review only" | `/api/flags` is missing `similar_confirmed_frauds`, or risk and similarity are below the refund thresholds |
| Page shows "Demo mode" with simulated data | No backend answered; start the app and open <http://127.0.0.1:8000> instead of the file |
| Decision is `review` on a high-risk row | Closest confirmed-fraud similarity is below `REFUND_MIN_SIM` |

## Tests

```bash
python test_verifier.py
```

Runs the safety tests (no keys or data needed).

## Evaluation

```bash
python evaluate.py --csv data/creditcard.csv
```

Compares Graph Ghost with Logistic Regression and Random Forest over several seeds and writes `results.json`.

## Files

| File | Purpose |
|---|---|
| `graph_ghost.py` | kNN graph and label spreading (the scorer) |
| `evaluate.py` | Graph Ghost vs Logistic Regression and Random Forest |
| `prepare_demo_data.py` | Builds the small demo sample from the public dataset |
| `core.py` | Scoring pipeline, evidence, demo backtest |
| `llm.py` | Gemini explainer (and offline mock mode) |
| `verifier.py` | Code checks on AI output and the final-action rule |
| `test_verifier.py` | Safety tests |
| `paypal_client.py` | PayPal sandbox REST client |
| `seed_sandbox.py` | Creates and captures sandbox payments for the demo |
| `app.py`, `static/index.html` | API and web page |
| `render.yaml` | Render deployment config |

## API

| Method | Endpoint |
|---|---|
| GET | `/api/flags` |
| GET | `/api/backtest` |
| POST | `/api/explain/{tx_id}` |
| POST | `/api/refund/{tx_id}` |
| GET | `/api/health` |
