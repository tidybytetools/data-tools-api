# Tidy Byte Data Tools API

A small HTTPS API that cleans messy data. Rapid (RapidAPI Hub) can proxy to it.

It wraps the same code as our free lite tools on PyPI:

- PDF tables to JSON or CSV
- PDF or HTML to Markdown
- Link check
- Markdown chunking for RAG
- JSON shape and flatten

Text PDFs only. No OCR. Uploads can be up to 8 MB. Files are processed and then discarded.

Website: [https://tidybytetools.github.io](https://tidybytetools.github.io)

## Endpoints

| Method | Path | What it does |
|---|---|---|
| GET | `/health` | Liveness. No secret needed. |
| GET | `/ready` | Readiness. No secret needed. |
| POST | `/v1/pdf-tables` | Pull tables out of a text PDF |
| POST | `/v1/to-markdown` | Turn a text PDF or HTML into Markdown |
| POST | `/v1/link-check` | Check up to 25 links |
| POST | `/v1/chunk-markdown` | Split Markdown by heading |
| POST | `/v1/shape` | Turn notes, CSV, or a Markdown table into JSON |
| POST | `/v1/flatten-json` | Flatten nested JSON into dotted keys |

When `RAPIDAPI_PROXY_SECRET` is set, every `/v1` call must send that value in the `X-RapidAPI-Proxy-Secret` header. Rapid adds this header on its own. Direct public calls are rejected. Health checks stay open so the host can see the service is up.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8080
```

Open [http://127.0.0.1:8080/docs](http://127.0.0.1:8080/docs). Locally, if the secret env var is not set, `/v1` is open so you can test. Do not leave it open on a public host.

```bash
pytest -q
```

## Host choice

The cheapest reliable host we can use without a card is **Render's free web service**.

| Host | Decision |
|---|---|
| **Render free** | **Use this.** No card. Python and Docker both work. Public HTTPS. Outbound HTTP works (needed for link checks). Sleeps after 15 minutes idle. Spin-up takes about one minute. 512 MB RAM. 750 free hours a month. |
| Apify Standby | Skip. Every call needs an Apify token, and idle time burns compute. On the free $5 plan a 512 MB Standby actor would use up the month in a couple of days. |
| Hugging Face Docker Spaces | Skip. In 2026 Docker and Gradio Spaces need a paid plan. |
| Cloudflare Workers / Deno Deploy | Skip. They do not run our Python PDF tools. |
| Fly.io | Often needs a card. Keep as a later always-on option. |
| Koyeb | New accounts need a paid plan and a card. |
| Vercel | Serverless time limits are tight for PDF work. |

**Cold start warning:** after 15 minutes with no traffic, Render puts the service to sleep. The next request can take about a minute to wake it. Rapid may time out on that first call. This is fine for a listing and first testers. After the first paid subscriber, upgrade to Render's cheapest paid plan so the service stays awake.

Render's free plan is meant for previews, not heavy production. Do not use an external pinger just to keep it awake. Link checks are capped at 25 URLs so we do not trip Render's outbound-traffic limits.

## Deploy to Render (needs a browser)

The service is ready to deploy. There is no Render account on this computer yet, so this last step needs a browser.

1. Sign up at [https://render.com](https://render.com) with the Tidy Byte Tools GitHub account (best) or the approved bot email. No card is needed for the free plan.
2. Push this folder to a GitHub repo, or connect the repo if it already exists.
3. Click **New > Web Service**. Pick the repo. Runtime: Python. Instance: **Free**.
4. Build: `pip install -r requirements.txt`
5. Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
6. Health check path: `/health`
7. Deploy. Copy the `https://….onrender.com` URL.
8. After you create the Rapid listing, paste Rapid's proxy secret into a Render env var named `RAPIDAPI_PROXY_SECRET` and redeploy. Until that is set, `/v1` returns 503 on Render so the public URL cannot be used as an open API.

`render.yaml` in this folder matches those settings.

## OpenAPI

- `openapi.yaml` and `openapi.json` are the spec to upload in Rapid Studio.
- Replace the placeholder server URL with the Render URL before you upload, or set the base URL in Rapid's Gateway tab.

## Honest limits

- Text PDFs only. Scanned pages are skipped and listed in `scanned_pages`.
- No OCR.
- 8 MB upload cap.
- 25 links per call.
- JSON flatten is a simple dotted-key flatten. It does not guess types beyond what you send.
