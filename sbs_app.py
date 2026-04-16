import streamlit as st
import pandas as pd
import calendar
import re
from datetime import date, timedelta, datetime
from playwright.sync_api import sync_playwright

BASE_URL = "https://www.sbs.gob.pe/app/pp/SISTIP_PORTAL/Paginas/Publicacion/TipoCambioPromedio.aspx"

CURRENCIES = {
    "USD — US Dollar":         ("lar de N.A",      "USD"),
    "AUD — Australian Dollar": ("lar Australiano",  "AUD"),
    "CAD — Canadian Dollar":   ("lar Canadiense",   "CAD"),
    "EUR — Euro":              ("Euro",             "EUR"),
    "GBP — British Pound":     ("Libra Esterlina",  "GBP"),
    "JPY — Japanese Yen":      ("Yen Japon",        "JPY"),
    "MXN — Mexican Peso":      ("Peso Mexicano",    "MXN"),
    "CHF — Swiss Franc":       ("Franco Suizo",     "CHF"),
    "CLP — Chilean Peso":      ("Peso Chileno",     "CLP"),
}

MONTH_NAMES = ["January","February","March","April","May","June",
               "July","August","September","October","November","December"]

# ── Scraper helpers ───────────────────────────────────────────────────────────

def extract_rate(page, currency_key):
    for row in page.query_selector_all("table tr"):
        cells = row.query_selector_all("td")
        if len(cells) >= 3 and currency_key in cells[0].inner_text():
            c = cells[1].inner_text().strip()
            v = cells[2].inner_text().strip()
            if c or v:
                return c, v
    return None, None

def extract_mercado_profesional(page):
    """Return the Tipo de Cambio Mercado Profesional value for Dólar de N.A."""
    try:
        body = page.evaluate("() => document.body.innerText")
        # The value sits just before "Fuente: BCRP" in the page text
        m = re.search(r'([0-9]+\.[0-9]+)\s*\n?\s*Fuente\s*:\s*BCRP', body)
        if m:
            return m.group(1).strip()
        # Fallback: last numeric value after "Dólar de N.A." on the page
        m2 = re.findall(r'Dólar de N\.A\.[\s\t]+([0-9]+\.[0-9]+)', body)
        if m2:
            return m2[-1].strip()
        return ''
    except:
        return ''

def get_current_page_date(page):
    try:
        return page.evaluate("""() => {
            var els = document.querySelectorAll('*');
            for (var el of els) {
                var t = el.innerText || '';
                var m = t.match(/Tipo de Cambio al (\\d{2}\\/\\d{2}\\/\\d{4})/);
                if (m) return m[1];
            }
            return '';
        }""")
    except:
        return ''

def load_and_query(page, date_str):
    parts = date_str.split('/')
    day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
    try:
        page.goto(BASE_URL, wait_until="load", timeout=30000)
        page.wait_for_timeout(500)
        if '404' in page.url or 'error' in page.url.lower():
            return False, 'page_404'
        result = page.evaluate(f"""() => {{
            try {{
                var d = {day}, m = {month}, y = {year};
                var inputs = document.querySelectorAll('input[id*="dateInput"]');
                for (var i = 0; i < inputs.length; i++) {{
                    var inputId = inputs[i].id;
                    var pickerId = inputId.replace(/_dateInput$/, '');
                    if (pickerId === inputId) continue;
                    var picker = (typeof $find !== 'undefined') ? $find(pickerId) : null;
                    if (picker && picker.set_selectedDate) {{
                        picker.set_selectedDate(new Date(y, m-1, d));
                        return 'ok';
                    }}
                }}
                return 'no_telerik';
            }} catch(e) {{ return 'err:' + e.message; }}
        }}""")
        if result != 'ok':
            try:
                inp = page.locator("input[id*='dateInput']").first
                inp.triple_click()
                inp.fill(date_str)
                inp.press("Tab")
                page.wait_for_timeout(150)
            except:
                pass
        page.wait_for_timeout(200)
        try:
            page.locator("input[value='Consultar']").first.click()
        except:
            page.evaluate("""() => {
                var all = Array.from(document.querySelectorAll('input,button'));
                for (var el of all) {
                    if ((el.value||el.textContent||'').indexOf('Consultar') >= 0) { el.click(); return; }
                }
            }""")
        try:
            page.wait_for_load_state("load", timeout=10000)
        except:
            pass
        page.wait_for_timeout(800)
        return True, result
    except Exception as e:
        return False, str(e)[:60]

def scrape_range(start_dt, end_dt, currency_key, iso_code, on_progress=None):
    results = []
    total = (end_dt - start_dt).days + 1

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()

        cur = start_dt
        n = 0
        while cur <= end_dt:
            n += 1
            ts       = cur.strftime("%d/%m/%Y")
            iso_date = cur.strftime("%Y-%m-%d")
            comp, vent, page_date, mercado = None, None, "", ""
            lk = cur

            if on_progress:
                on_progress(n, total, ts)

            for attempt in range(4):
                ls = lk.strftime("%d/%m/%Y")
                ok, _ = load_and_query(page, ls)
                if not ok:
                    page.wait_for_timeout(500)
                    continue
                page_date = get_current_page_date(page)
                comp, vent = extract_rate(page, currency_key)
                mercado    = extract_mercado_profesional(page)
                if page_date == ls and (comp or vent):
                    break
                elif comp or vent:
                    if attempt >= 2:
                        break
                else:
                    lk -= timedelta(days=1)

            # Clean rate = 1 / mercado_profesional
            clean_rate = ""
            if mercado:
                try:
                    clean_rate = f"{1 / float(mercado):.6f}"
                except:
                    pass

            results.append({
                "date":                 iso_date,
                "from_currency":        "PEN",
                "to_currency":          iso_code,
                "compra_s":             comp    or "",
                "venta_s":              vent    or "",
                "mercado_profesional":  mercado or "",
                "clean_rate":           clean_rate,
            })
            cur += timedelta(days=1)

        browser.close()
    return results

# ── Streamlit UI ──────────────────────────────────────────────────────────────

st.set_page_config(page_title="Bespoken Rate — Peru Voes",
                   page_icon="💱", layout="centered")

st.title("💱 Bespoken Rate — Peru Voes")
st.markdown(
    f"Source: [Superintendencia de Banca, Seguros y AFP del Perú]({BASE_URL})"
)
st.divider()

c1, c2, c3 = st.columns(3)
with c1:
    sel_currency = st.selectbox("Currency", list(CURRENCIES.keys()), index=0)
with c2:
    sel_month = st.selectbox("Month", MONTH_NAMES, index=datetime.now().month - 2)
with c3:
    sel_year = st.number_input("Year", min_value=2000, max_value=2030, value=2026, step=1)

month_num = MONTH_NAMES.index(sel_month) + 1
last_day  = calendar.monthrange(int(sel_year), month_num)[1]
start_dt  = date(int(sel_year), month_num, 1)
end_dt    = date(int(sel_year), month_num, last_day)
cur_key, iso_code = CURRENCIES[sel_currency]

st.info(f"📅 **{sel_currency}** · {sel_month} {int(sel_year)} · {last_day} days")

if st.button("🔍 Fetch Data", type="primary", use_container_width=True):
    prog  = st.progress(0.0, text="Starting browser…")
    stato = st.empty()

    def cb(n, total, ts):
        prog.progress(n / total, text=f"Querying {ts}  ({n}/{total})")
        stato.info(f"⏳ {ts}")

    try:
        rows = scrape_range(start_dt, end_dt, cur_key, iso_code, on_progress=cb)
        prog.progress(1.0, text="✅ Done!")
        stato.success(f"Fetched {len(rows)} dates.")
        st.session_state["rows"]        = rows
        st.session_state["label"]       = f"{sel_currency} — {sel_month} {int(sel_year)}"
        st.session_state["iso"]         = iso_code
        st.session_state["month_label"] = f"{sel_month}{int(sel_year)}"
    except Exception as exc:
        stato.error(f"Error: {exc}")

if "rows" in st.session_state:
    rows        = st.session_state["rows"]
    iso_s       = st.session_state.get("iso", "USD")
    mlabel      = st.session_state.get("month_label", "")

    st.subheader(st.session_state.get("label", "Results"))

    # ── Raw DataFrame (original columns) ────────────────────────
    raw_df = pd.DataFrame([{
        "date":                r["date"],
        "from_currency":       r["from_currency"],
        "to_currency":         r["to_currency"],
        "compra (S/)":         r["compra_s"],
        "venta (S/)":          r["venta_s"],
        "mercado_profesional": r["mercado_profesional"],
    } for r in rows])

    # ── Clean DataFrame (formatted output) ──────────────────────
    clean_df = pd.DataFrame([{
        "from_currency":    r["from_currency"],
        "to_currency":      r["to_currency"],
        "date":             r["date"],
        "rate_period_type": "DAILY",
        "rate":             r["clean_rate"],
        "effective_from":   "",
        "effective_to":     "",
    } for r in rows])

    # Stats
    valid = clean_df[clean_df["rate"] != ""]
    if not valid.empty:
        m1, m2, m3 = st.columns(3)
        rn = pd.to_numeric(valid["rate"], errors="coerce").dropna()
        m1.metric("Days with data", len(valid))
        if len(rn):
            m2.metric("Avg rate (1/Mercado Prof.)", f"{rn.mean():.6f}")
            m3.metric("Range", f"{rn.min():.6f} – {rn.max():.6f}")

    # Display clean table
    st.write("**Clean data** — rate = 1 ÷ Mercado Profesional")
    st.dataframe(clean_df, use_container_width=True, hide_index=True, height=380)

    # Two download buttons side by side
    dl1, dl2 = st.columns(2)
    with dl1:
        raw_csv = raw_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        st.download_button("⬇️ Download Raw CSV", data=raw_csv,
                           file_name=f"SBS_raw_{iso_s}_PEN_{mlabel}.csv",
                           mime="text/csv", use_container_width=True)
    with dl2:
        clean_csv = clean_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        st.download_button("⬇️ Download Clean CSV", data=clean_csv,
                           file_name=f"SBS_clean_{iso_s}_PEN_{mlabel}.csv",
                           mime="text/csv", use_container_width=True)
