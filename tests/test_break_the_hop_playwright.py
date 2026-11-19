"""Headless browser test of break_the_hop/index.html (skipped if Playwright/Chromium is unavailable).
Cross-checks the in-page generators against the Python schedules and drives the UI."""
import os, pathlib, pytest
pw = pytest.importorskip("playwright.sync_api")
from hopbench.schedules import LFSR16, LCG24, SipHash24

HTML = pathlib.Path(__file__).resolve().parents[1] / "break_the_hop" / "index.html"
SHOTS = os.environ.get("BTH_SHOTS")

@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:
            pytest.skip(f"chromium unavailable: {e}")
        yield b
        b.close()

def page_for(browser, w=1000, h=900, scheme="light"):
    ctx = browser.new_context(viewport={"width": w, "height": h}, color_scheme=scheme, accept_downloads=True)
    pg = ctx.new_page(); errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.goto(HTML.as_uri()); pg.errs = errs
    return pg

def shot(pg, name):
    if SHOTS: pg.screenshot(path=os.path.join(SHOTS, name), full_page=True)

def test_generators_match_python(browser):
    pg = page_for(browser)
    for seed in (1, 0xACE1, 65535):
        js = pg.evaluate("(s)=>{const k=HB.makeSchedule('lfsr',s);return Array.from({length:300},(_,i)=>k.hop(i))}", seed)
        assert js == _py(LFSR16, seed)
    for seed in (0, 12345, 0xFFFFFF):
        js = pg.evaluate("(s)=>{const k=HB.makeSchedule('lcg',s);return Array.from({length:300},(_,i)=>k.hop(i))}", seed)
        assert js == _py(LCG24, seed)
    key = bytes(range(16))
    js = pg.evaluate("(k)=>{const kk=new Uint8Array(k);return Array.from({length:100},(_,i)=>HB.sipHop(kk,i))}", list(key))
    s = SipHash24.__new__(SipHash24); s.M = 13; s.key = key
    assert js == [s.next_channel(i) for i in range(100)]
    assert not pg.errs

def _py(cls, seed):
    # reproduce the generator from its step function without depending on constructor seeding
    from hopbench import schedules as S
    x, out = seed, []
    for _ in range(300):
        if cls is LFSR16:
            out.append(x % 13); x = S._lfsr_step(x) if hasattr(S, "_lfsr_step") else _lfsr(x)
        else:
            out.append((x >> 8) % 13); x = (S.LCG_A * x + S.LCG_C) & S.LCG_MASK
    return out

def _lfsr(s):
    b = ((s >> 0) ^ (s >> 2) ^ (s >> 3) ^ (s >> 5)) & 1
    return (s >> 1) | (b << 15)

def test_ui_flow_desktop_and_attack(browser):
    pg = page_for(browser)
    assert pg.locator("#grid .ch").count() == 13
    shot(pg, "01_desktop_initial.png")
    for i in range(30): pg.locator("#grid .ch").nth(i % 13).click()
    assert pg.inner_text("#sN") == "30"
    shot(pg, "02_desktop_after_guesses.png")
    pg.click("#btnReveal")
    assert pg.is_visible("#reveal")
    txt = pg.inner_text("#revLead")
    assert "hops" in txt and "exactly one candidate" in txt
    assert "100.0 %" in pg.inner_text("#revTable")
    shot(pg, "03_desktop_reveal_lfsr.png")
    pg.click("#btnMany"); pg.wait_for_function("document.getElementById('manyOut').textContent.includes('median')")
    pg.click(".tab[data-mode=lcg]"); pg.click("#btnReveal")
    assert "100.0 %" in pg.inner_text("#revTable")
    pg.click(".tab[data-mode=keyed]"); pg.click("#btnReveal")
    assert "holds" in pg.inner_text("#revTitle")
    acc = float(pg.inner_text("#revTable").split("n-gram predictor")[1].split("%")[0].split()[-1])
    assert acc < 15
    shot(pg, "04_desktop_reveal_keyed.png")
    assert not pg.errs, pg.errs

def test_scoring_vs_chance(browser):
    pg = page_for(browser)
    # oracle player: cheat via the breaker's own knowledge -> peek next hop
    pg.evaluate("HB.setSecret('lfsr', 777)")
    for _ in range(40):
        nxt = pg.evaluate("(()=>{const s=HB.state().sessions.lfsr;return s.sched.hop(s.t)})()")
        pg.locator("#grid .ch").nth(nxt).click()
    assert pg.inner_text("#sAcc").startswith("100.0")
    assert "Far above chance" in pg.inner_text("#verdict")
    pg.click("#btnReset")
    for i in range(40): pg.locator("#grid .ch").nth(0).click()
    assert pg.inner_text("#sN") == "40"
    assert "Far above" not in pg.inner_text("#verdict")

def test_share_card_png(browser, tmp_path):
    pg = page_for(browser)
    for i in range(15): pg.locator("#grid .ch").nth(i % 13).click()
    pg.click("#btnReveal"); pg.click("#btnShare")
    assert pg.is_visible("#dlg")
    src = pg.get_attribute("#dlgImg", "src")
    assert src.startswith("data:image/png;base64,")
    import base64, struct
    raw = base64.b64decode(src.split(",", 1)[1])
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", raw[16:24]); assert (w, h) == (1200, 630)
    (tmp_path / "card.png").write_bytes(raw)
    if SHOTS: (pathlib.Path(SHOTS) / "05_share_card.png").write_bytes(raw)
    with pg.expect_download() as d: pg.click("#dlgDl")
    assert d.value.suggested_filename == "break-the-hop.png"

@pytest.mark.parametrize("w,h,scheme", [(375, 760, "light"), (360, 700, "dark")])
def test_mobile_no_horizontal_scroll(browser, w, h, scheme):
    pg = page_for(browser, w, h, scheme)
    for i in range(20): pg.locator("#grid .ch").nth(i % 13).click()
    pg.click("#btnReveal")
    over = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert over <= 0, over
    box = pg.locator("#grid .ch").first.bounding_box(); assert box["height"] >= 44
    shot(pg, f"06_mobile_{w}_{scheme}.png")
    assert not pg.errs
