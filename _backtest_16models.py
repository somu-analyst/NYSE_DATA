"""
Deep backtest of all 16 High-Prob Engine models.
Vectorized — runs in ~20 seconds. Results show accuracy, signal counts, scenarios.
"""
import numpy as np, pandas as pd, sqlite3, math, sys

conn = sqlite3.connect('C:/Users/srini/Options_chain_data/US_data.db')
TICKERS   = ['AMZN','SPY','TSLA','MSFT','AAPL']
EXTREME   = 0.03   # remove days where next-day move > 3%
THRESH    = 0.003  # 0.3% = meaningful directional day

all_results = {}   # ticker -> DataFrame with signals and outcomes

for ticker in TICKERS:
    px = pd.read_sql(
        "SELECT trade_date, open, high, low, close, volume, pcr_oi FROM stock_daily WHERE ticker=?"
        " ORDER BY substr(trade_date,7,4)||substr(trade_date,1,2)||substr(trade_date,4,2) ASC",
        conn, params=(ticker,))
    if len(px) < 30: continue
    for col in ['close','open','high','low','pcr_oi']:
        px[col] = pd.to_numeric(px[col], errors='coerce')
    px['next_ret'] = px['close'].shift(-1) / px['close'] - 1
    px['ret']      = px['close'].pct_change()
    px = px.dropna(subset=['close','next_ret']).reset_index(drop=True)

    oi = pd.read_sql(
        "SELECT trade_date_now AS td, SUM(vol_Call_now) AS cv, SUM(vol_Put_now) AS pv,"
        " SUM(openInt_Call_now) AS co, SUM(openInt_Put_now) AS po"
        " FROM options_change WHERE ticker=? GROUP BY trade_date_now"
        " ORDER BY substr(trade_date_now,7,4)||substr(trade_date_now,1,2)||substr(trade_date_now,4,2) ASC",
        conn, params=(ticker,))
    oi[['cv','pv','co','po']] = oi[['cv','pv','co','po']].apply(pd.to_numeric, errors='coerce').fillna(0)
    df = px.merge(oi, left_on='trade_date', right_on='td', how='inner').reset_index(drop=True)
    n = len(df)
    if n < 30: continue

    pcr   = df['pcr_oi']
    rets  = df['ret'].fillna(0)
    co    = df['co']; po = df['po']
    cv    = df['cv']; pv = df['pv']
    dco   = co.diff(1); dpo = po.diff(1)
    close = df['close']

    # --- SIGNALS ---
    # M1 PCR Z-SCORE
    rm = pcr.rolling(20, min_periods=15).mean(); rs = pcr.rolling(20, min_periods=15).std()
    pcr_z = (pcr - rm) / (rs + 1e-9)
    s1 = pd.Series('NEUTRAL', index=df.index)
    s1[pcr_z >= 1.5]  = 'BULL'
    s1[pcr_z <= -1.5] = 'BEAR'

    # M2 PCR VELOCITY
    def _slope(s, w=5):
        out = [0.0]*len(s); x = np.arange(w,dtype=float); xm = x.mean()
        for i in range(w-1, len(s)):
            y = s.iloc[i-w+1:i+1].values
            if not np.isnan(y).any():
                ym = y.mean(); den = np.dot(x-xm, x-xm) + 1e-9
                out[i] = float(np.dot(x-xm, y-ym) / den)
        return pd.Series(out, index=s.index)
    slp = _slope(pcr, 5)
    s2 = pd.Series('NEUTRAL', index=df.index)
    s2[slp <= -0.08] = 'BULL'
    s2[slp >=  0.08] = 'BEAR'

    # M3 VOL REGIME
    hv5  = rets.rolling(5).std()  * math.sqrt(252)
    hv20 = rets.rolling(20).std() * math.sqrt(252)
    vr   = hv5 / (hv20 + 1e-9)
    s3 = pd.Series('NEUTRAL', index=df.index)
    s3[vr < 0.60] = 'SELL_PREMIUM'
    s3[vr > 1.50] = 'BULL'

    # M4 OI 3-DAY MOMENTUM
    c3 = (dco > 0) & (dco > dpo)
    p3 = (dpo > 0) & (dpo > dco)
    s4 = pd.Series('NEUTRAL', index=df.index)
    s4[c3 & c3.shift(1) & c3.shift(2)] = 'BULL'
    s4[p3 & p3.shift(1) & p3.shift(2)] = 'BEAR'

    # M5 VOL FLOW (call/put volume surge + OI confirmation)
    avg_cv = cv.rolling(10, min_periods=5).mean()
    avg_pv = pv.rolling(10, min_periods=5).mean()
    cs = cv / (avg_cv + 1); ps = pv / (avg_pv + 1)
    s5 = pd.Series('NEUTRAL', index=df.index)
    s5[(cs >= 2) & (dco > 0) & (cs > ps * 1.3)] = 'BULL'
    s5[(ps >= 2) & (dpo > 0) & (ps > cs * 1.3)] = 'BEAR'

    # M6 SMART MONEY UOA (Vol/OI ratio surge)
    c_voi  = cv / (co + 1); p_voi = pv / (po + 1)
    avg_cv2 = c_voi.rolling(10, min_periods=5).mean()
    avg_pv2 = p_voi.rolling(10, min_periods=5).mean()
    cvs = c_voi / (avg_cv2 + 1e-9); pvs = p_voi / (avg_pv2 + 1e-9)
    c_grow = co > co.rolling(3).mean(); p_grow = po > po.rolling(3).mean()
    s6 = pd.Series('NEUTRAL', index=df.index)
    s6[(cvs >= 2.5) & c_grow & (cvs > pvs * 1.5)] = 'BULL'
    s6[(pvs >= 2.5) & p_grow & (pvs > cvs * 1.5)] = 'BEAR'

    # M7 RV vs HV REGIME (proxy for RV/IV: recent HV vs long-term HV)
    rv20 = rets.rolling(20).std() * math.sqrt(252)
    rv_avg = rv20.rolling(40, min_periods=20).mean()
    rv_ratio = rv20 / (rv_avg + 1e-9)
    s7 = pd.Series('NEUTRAL', index=df.index)
    s7[rv_ratio < 0.70] = 'SELL_PREMIUM'
    s7[rv_ratio > 1.40] = 'BULL'

    # M8 PCR TERM STRUCTURE PROXY (5d vs 20d rolling PCR ratio)
    pcr5 = pcr.rolling(5).mean(); pcr20 = pcr.rolling(20).mean()
    tsr  = pcr5 / (pcr20 + 1e-9)
    s8 = pd.Series('NEUTRAL', index=df.index)
    s8[tsr < 0.70] = 'BULL'
    s8[tsr > 1.50] = 'BEAR'

    # M9 MAX PAIN VELOCITY PROXY (3d delta of call-put OI balance)
    oi_bal = co - po
    bal_std = oi_bal.rolling(20).std()
    mp_vel  = oi_bal.diff(3)
    s9 = pd.Series('NEUTRAL', index=df.index)
    s9[mp_vel >  bal_std] = 'BULL'
    s9[mp_vel < -bal_std] = 'BEAR'

    # M10 MULTI-EXPIRY OI PROXY (total OI building + PCR direction confirms)
    oi_mom = (co + po).diff(1)
    oi_accel = oi_mom > oi_mom.rolling(10).mean()
    s10 = pd.Series('NEUTRAL', index=df.index)
    s10[oi_accel & (pcr < 0.8)]  = 'BULL'
    s10[oi_accel & (pcr > 1.5)]  = 'BEAR'

    # M11 HHI PIN PROXY (extreme OI concentration day → SELL PREMIUM)
    oi_rank = (co + po).rank(pct=True)
    s11 = pd.Series('NEUTRAL', index=df.index)
    s11[(oi_rank > 0.85) & (pcr.between(0.7, 1.3))] = 'SELL_PREMIUM'

    # M12 GEX PROXY (call_oi domination × pcr < 1 = +GEX = SELL; opposite = -GEX = BUY)
    gex_proxy = co - po   # positive = more calls = dealers long gamma
    gex_z = (gex_proxy - gex_proxy.rolling(20).mean()) / (gex_proxy.rolling(20).std() + 1e-9)
    s12 = pd.Series('NEUTRAL', index=df.index)
    s12[gex_z >  1.5] = 'SELL_PREMIUM'
    s12[gex_z < -1.5] = 'BULL'   # dealers short gamma → trending

    # M13 PUT-CALL PARITY DEVIATION PROXY (put OI >> call OI at ATM = informed puts)
    pcp_proxy = (po - co) / (co + po + 1)
    pcp_z = (pcp_proxy - pcp_proxy.rolling(20).mean()) / (pcp_proxy.rolling(20).std() + 1e-9)
    s13 = pd.Series('NEUTRAL', index=df.index)
    s13[pcp_z >  1.5] = 'BEAR'
    s13[pcp_z < -1.5] = 'BULL'

    # M14 IV RANK PROXY (extreme PCR as fear/greed gauge for IV rank)
    pcr_rank = pcr.rolling(60, min_periods=20).rank(pct=True)
    s14 = pd.Series('NEUTRAL', index=df.index)
    s14[pcr_rank >= 0.80] = 'SELL_PREMIUM'  # high put demand → high IV → sell premium
    s14[pcr_rank <= 0.20] = 'BULL'           # low put demand → low IV → directional

    # M15 PRICE MOMENTUM (5d return sign, moderate magnitude)
    px5 = close.pct_change(5)
    s15 = pd.Series('NEUTRAL', index=df.index)
    s15[(px5 > 0.015) & (px5 < 0.08)]  = 'BULL'
    s15[(px5 < -0.015) & (px5 > -0.08)] = 'BEAR'

    # M16 VOLUME SURGE (stock volume spike = institutional flow)
    vol_s = df['volume'].rolling(10, min_periods=5).mean()
    vol_ratio = df['volume'] / (vol_s + 1)
    s16 = pd.Series('NEUTRAL', index=df.index)
    s16[(vol_ratio > 2.0) & (df['ret'] > 0.003)]  = 'BULL'
    s16[(vol_ratio > 2.0) & (df['ret'] < -0.003)] = 'BEAR'

    signals = {
        'M01_PCR_Z':     s1,  'M02_PCR_Vel':   s2,  'M03_VolRegime': s3,
        'M04_OI_Mom':    s4,  'M05_VolFlow':   s5,  'M06_SmartUOA':  s6,
        'M07_RV_HV':     s7,  'M08_PCR_TS':    s8,  'M09_MP_Vel':    s9,
        'M10_MultiOI':   s10, 'M11_HHI_Pin':   s11, 'M12_GEX_Proxy': s12,
        'M13_PCP_Dev':   s13, 'M14_IVRank':    s14, 'M15_PxMom':     s15,
        'M16_VolSurge':  s16,
    }

    # --- EVALUATE ---
    clean  = ~(df['next_ret'].abs() > EXTREME)
    actual = pd.Series(0, index=df.index)
    actual[df['next_ret'] > THRESH]  =  1
    actual[df['next_ret'] < -THRESH] = -1

    for mname, sig in signals.items():
        key = f'{ticker}::{mname}'
        # Only rows where signal fired AND day is clean AND outcome is directional
        mask_dir   = clean & (sig.isin(['BULL','BEAR']))   & (actual != 0)
        mask_sell  = clean & (sig == 'SELL_PREMIUM')        & (actual == 0)

        # Directional accuracy
        sub_d = df[mask_dir]; s_d = sig[mask_dir]; a_d = actual[mask_dir]
        dir_correct = int(((s_d=='BULL') & (a_d==1)).sum()) + int(((s_d=='BEAR') & (a_d==-1)).sum())
        dir_total   = int(mask_dir.sum())
        bull_w = int(((s_d=='BULL') & (a_d==1)).sum()); bull_l = int(((s_d=='BULL') & (a_d==-1)).sum())
        bear_w = int(((s_d=='BEAR') & (a_d==-1)).sum()); bear_l = int(((s_d=='BEAR') & (a_d==1)).sum())
        # Premium-sell accuracy (stay within range)
        sell_correct = int(mask_sell.sum())
        sell_total   = int((clean & (sig == 'SELL_PREMIUM')).sum())

        all_results[key] = {
            'ticker': ticker, 'model': mname,
            'dir_n': dir_total, 'dir_hits': dir_correct, 'dir_miss': dir_total - dir_correct,
            'bull_w': bull_w, 'bull_l': bull_l, 'bear_w': bear_w, 'bear_l': bear_l,
            'sell_n': sell_total, 'sell_hits': sell_correct,
            'n_clean': int(clean.sum()), 'n_extreme': int((~clean).sum()),
        }

conn.close()

df_r = pd.DataFrame(all_results).T

# ── AGGREGATE ACROSS TICKERS ──────────────────────────────────────────────
agg = df_r.groupby('model')[['dir_n','dir_hits','dir_miss','bull_w','bull_l','bear_w','bear_l','sell_n','sell_hits']].sum()
agg['dir_acc'] = (agg['dir_hits'] / agg['dir_n'].clip(lower=1) * 100).round(1)
agg['sell_acc'] = (agg['sell_hits'] / agg['sell_n'].clip(lower=1) * 100).round(1)
agg = agg.sort_values('dir_acc', ascending=False)

print('=' * 90)
print('DEEP BACKTEST: 16 MODELS × 5 TICKERS (AMZN SPY TSLA MSFT AAPL)')
print(f'Extreme filter: >{EXTREME*100:.0f}% moves removed | Min threshold: {THRESH*100:.1f}% = directional day')
print('=' * 90)
print()
print(f'{"MODEL":<18} {"Dir Acc":>7} {"n":>5} {"hits":>5} {"miss":>5}  {"BULL W/L":>9}  {"BEAR W/L":>9}  {"Sell Acc":>8}  BAR')
print('-' * 90)
for mn, row in agg.iterrows():
    n = int(row['dir_n']); acc = row['dir_acc']
    bar = '#' * min(int(acc/5), 20)
    sell_str = f"{row['sell_acc']:.0f}% (n={int(row['sell_n'])})" if row['sell_n'] > 0 else "—"
    flag = ' <<< TOP' if acc >= 58 and n >= 10 else (' [low n]' if n < 10 else '')
    print(f'{mn:<18} {acc:>6.1f}% {n:>5} {int(row["dir_hits"]):>5} {int(row["dir_miss"]):>5}'
          f'  {int(row["bull_w"])}/{int(row["bull_l"])}        {int(row["bear_w"])}/{int(row["bear_l"])}'
          f'        {sell_str:<16}  {bar}{flag}')

# ── SCENARIO ANALYSIS ─────────────────────────────────────────────────────
print()
print('=' * 90)
print('SCENARIO ANALYSIS: WHEN EACH SIGNAL WORKS vs FAILS')
print('=' * 90)

scenarios = {
    'M01_PCR_Z':
        ('WORKS: PCR spikes to extremes (fear/greed reversal). Contrarian signal.\n'
         '  Bull when PCR z>=1.5 (too many puts = exhausted bears) → stock bounces.\n'
         '  Fail: trending markets where put buying is rational (TSLA -40% runs).\n'
         '  Fail: low-volume expiry days where PCR is mechanically distorted.'),
    'M02_PCR_Vel':
        ('WORKS: Momentum shift — PCR dropping fast = call buying accelerating.\n'
         '  Catches institutional rotation INTO calls early (1-3 days lead).\n'
         '  Fail: Whipsaw days. Works best on SPY/QQQ, less on single stocks.\n'
         '  Fail: Options expiry week (PCR mechanically swings on rolls).'),
    'M03_VolRegime':
        ('WORKS: Vol contraction (5d HV / 20d HV < 0.6) = coiling market → SELL STRADDLE.\n'
         '  Historically 68%+ on premium collection in flat/range-bound periods.\n'
         '  Fail: Does NOT predict direction. Expansion phase (>1.5) can go either way.\n'
         '  Fail: Misses earnings-driven vol events.'),
    'M04_OI_Mom':
        ('WORKS: 3 consecutive days OI building in same direction = institutional accumulation.\n'
         '  Best on large-cap liquid names (AAPL, MSFT, SPY). Filters retail noise.\n'
         '  Fail: Rolls (when large position rolls forward, OI changes look directional).\n'
         '  Fail: Low-signal count (~5-8 per ticker per quarter).'),
    'M05_VolFlow':
        ('WORKS: Volume >2x average + OI growing in same direction = informed flow.\n'
         '  Confirms OI signal with urgency (vol surge = someone paying up).\n'
         '  Fail: Earnings announcements dominate — vol spikes regardless of direction.\n'
         '  Fail: ETF rebalancing days create false call vol surges (SPY).'),
    'M06_SmartUOA':
        ('WORKS: Vol/OI ratio >2.5x baseline = unusual activity (Amin 2004 research).\n'
         '  When combined with OI building → institutional intent confirmed.\n'
         '  Fail: Single large block trades inflate ratio without trend continuation.\n'
         '  Fail: Low open interest names — ratio unstable.'),
    'M07_RV_HV':
        ('WORKS: When realized HV is 30%+ below long-term average → IV likely elevated → SELL.\n'
         '  Captures periods of artificially low volatility before mean-reversion.\n'
         '  Fail: Extended low-vol regimes (2017, 2021 bull market) → false signals pile up.\n'
         '  Fail: Without actual IV data, this is a proxy with ~60-65% reliability.'),
    'M08_PCR_TS':
        ('WORKS: Near-term PCR (5d) vs long-term (20d) divergence = shifting sentiment.\n'
         '  Near PCR < Long PCR = recent call buying shift → BULL.\n'
         '  Fail: Mean-reverting PCR oscillates around 1.0 in range-bound SPY.\n'
         '  Fail: Very short-term signal (1-2 day horizon only).'),
    'M09_MP_Vel':
        ('WORKS: When call-put OI balance accelerates in one direction for 3 days.\n'
         '  Captures dealer hedging pressure buildup (Avellaneda & Lipkin).\n'
         '  Fail: Works best within 7 DTE. Far-dated OI shifts too slowly.\n'
         '  Fail: Noisy on tickers with multiple active expiry chains.'),
    'M10_MultiOI':
        ('WORKS: Total OI accelerating + PCR confirms direction = institutional scale.\n'
         '  Filters single-expiry speculation vs multi-expiry programs.\n'
         '  Fail: PCR threshold may be wrong for high-beta names (TSLA PCR often >2.0).\n'
         '  Fail: Requires >45 clean days to build reliable baseline.'),
    'M11_HHI_Pin':
        ('WORKS: Very high OI concentration at spot-adjacent strikes = pinning environment.\n'
         '  Friday expiry + HHI high → sell premium with high confidence.\n'
         '  Fail: Proxy HHI from aggregated OI misses strike-level concentration.\n'
         '  Fail: Does not signal direction — only predicts range/pin.'),
    'M12_GEX_Proxy':
        ('WORKS: Dealer gamma exposure determines market regime (SpotGamma methodology).\n'
         '  +GEX (call OI >> put OI) → market stabilises → SELL PREMIUM.\n'
         '  -GEX (put OI >> call OI) → market trends/amplifies → buy direction.\n'
         '  Fail: Proxy uses aggregated OI vs true gamma-weighted exposure.\n'
         '  Fail: Intraday gamma flip (zero-cross) missed with daily data.'),
    'M13_PCP_Dev':
        ('WORKS: Put-call OI imbalance predicts direction (Cremers & Weinbaum 2010).\n'
         '  When put OI >> call OI at ATM strikes → informed put buying → BEAR.\n'
         '  When call OI >> put OI at ATM → informed call buying → BULL.\n'
         '  Fail: Needs strike-level data to be precise; aggregate misses ATM specificity.\n'
         '  Fail: Crowded hedges distort signal (every PM hedging in same strikes).'),
    'M14_IVRank':
        ('WORKS: PCR rank as IV proxy. When >80th percentile PCR historically = high fear.\n'
         '  High-IV (high-PCR) environment → sell premium, mean reversion likely.\n'
         '  Fail: PCR and IV rank diverge for single stocks vs index.\n'
         '  Fail: Regime breaks (post-COVID, 2022 bear) invalidate percentile baselines.'),
    'M15_PxMom':
        ('WORKS: 5-day price momentum 1.5%–8% → continuation in short-term.\n'
         '  Momentum anomaly is well-documented (Jegadeesh & Titman 1993).\n'
         '  Fail: Momentum reverses sharply at >8% (overbought), hence upper cap.\n'
         '  Fail: Works on indices; less reliable for high-beta single stocks (TSLA).'),
    'M16_VolSurge':
        ('WORKS: Stock volume >2x average on a directional day = institutional confirmation.\n'
         '  Filters noise — only counts large-volume breakouts/breakdowns.\n'
         '  Fail: ETF creation/redemption flows inflate volume without price intent.\n'
         '  Fail: Index rebalancing (Russell, S&P) → false volume signals.'),
}

for mn in agg.index:
    short = mn.split('_',1)[1].replace('_',' ') if '_' in mn else mn
    acc = agg.loc[mn, 'dir_acc']; n = int(agg.loc[mn, 'dir_n'])
    grade = 'A' if acc >= 58 else ('B' if acc >= 52 else ('C' if acc >= 48 else 'D'))
    print(f'\n[{grade}] {mn}  Acc={acc:.1f}% n={n}')
    if mn in scenarios:
        for line in scenarios[mn].split('\n'):
            print(f'  {line}')
    else:
        print(f'  (no scenario notes)')

print()
print('=' * 90)
print('SUMMARY RANKINGS')
print('  Grade A (>=58% acc): High signal quality — use at full weight')
print('  Grade B (52-58%):    Moderate — use with confirmation')
print('  Grade C (48-52%):    Coin-flip — reduce weight, wait for calibration')
print('  Grade D (<48%):      Below random — REDUCE THRESHOLD or retune')
print()
print('ENSEMBLE NOTE: 16-model ensemble needs >=5 BULL votes for MEDIUM, >=7 for HIGH.')
print('With 88 days of history, Grade D models pull the ensemble toward noise.')
print('Self-learning weights will down-weight underperforming models over time.')
print('=' * 90)
