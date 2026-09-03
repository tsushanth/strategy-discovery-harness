# Strategy Literature Survey — Event-Window Liquidity Harness

> **Verify before you trust.** Every citation below should be independently re-checked (author, title, year, venue/DOI/id) before you build on it. Citations I could not confirm via web search are tagged `[UNVERIFIED]`. Effect sizes are quoted as the source states them; treat all of them as *in-sample, gross-of-your-costs, and probably decayed* until you reproduce them yourself.

Audience: solo retail quant, own capital, daily-bar / event data, no HFT. The lens throughout is: *can I actually test this with yfinance daily bars + FRED + earnings dates, and does it survive my costs?*

---

## 0. What the literature says a solo daily-bar trader can realistically test

- **Scheduled-event calendar effects are the retail sweet spot.** Effects tied to *known-in-advance dates* (earnings dates, FOMC meetings, CPI/NFP releases) are testable with daily bars because the signal is the calendar, not the tick. Pre-FOMC drift (Lucca–Moench 2015), the macro-announcement premium (Savor–Wilson), and the earnings-announcement premium (Frazzini–Lamont) are all daily-frequency, publicly-dated phenomena. This is where your unblocked data sources (yfinance + FRED + earnings calendars) line up with real published effects.
- **Anything whose alpha lives inside the day is mostly not yours.** Patell–Wolfson (1984) showed the *price* reaction to earnings is essentially complete within 5–10 minutes. The post-announcement *drift* (PEAD) plays out over days/weeks and is daily-bar-testable; the announcement *jump* is not capturable at daily resolution.
- **Published cross-sectional anomalies decay — a lot.** McLean & Pontiff (2016): returns are ~26% lower out-of-sample and ~58% lower post-publication across 97 predictors. Assume any textbook edge you read about is already crowded; budget for it in your priors, not just your backtest.
- **Costs, not signal, kill most retail versions.** Pairs trading, short-term reversal, and PEAD are all measured in tens of bps per event/leg. Do & Faff (2010, 2012) document pairs-trading profits collapsing toward zero once realistic costs + the strategy's own post-2000 decay are applied. If your backtest ignores spread, borrow cost, and slippage, it is measuring a fantasy.
- **Order-flow / microstructure alpha is data-gated out of reach.** The order-flow-imbalance literature (Kyle 1985; Cont–Kukanov–Stoikov 2014) needs L2/message-book or at least signed-trade data. yfinance daily OHLCV cannot reconstruct OFI. Treat this family as "read to understand market impact of your own orders," not "trade."
- **Small-sample self-deception is the main enemy.** Event studies give you one observation per firm-quarter. A single-name earnings strategy over a few years is a handful of dozen events — far too few to trust a Sharpe. Pool across names, use walk-forward, and be honest that most "edges" at this sample size are noise.

---

## 1. Event-driven around earnings (harness flagship: "get paid to provide liquidity through earnings")

**Key references**

- **Ball, R. & Brown, P. (1968), "An Empirical Evaluation of Accounting Income Numbers," *Journal of Accounting Research* 6(2), 159–178.** The founding event study: stock prices move in the direction of the earnings surprise, and — critically — a portion of the drift *continues after* the announcement. Origin of the whole PEAD literature; direction, not a clean tradable effect size.
- **Bernard, V. & Thomas, J. (1989), "Post-Earnings-Announcement Drift: Delayed Price Response or Risk Premium?," *Journal of Accounting Research* 27 (Supplement), 1–36.** The canonical PEAD result. A long-top-decile / short-bottom-decile portfolio on standardized unexpected earnings (SUE) earned a positive spread in 41 of 48 quarters (1974–1985), including 11 of 16 quarters when the NYSE index fell. Direction: buy positive surprises, short negative ones; drift persists ~60 trading days.
- **Chan, L., Jegadeesh, N. & Lakonishok, J. (1996), "Momentum Strategies," *Journal of Finance* 51(5), 1681–1713.** Past return *and* past earnings surprise each independently predict future drift; little subsequent reversal for high price/earnings-momentum stocks. Establishes PEAD and price momentum as related-but-distinct underreaction effects. Daily/monthly frequency — testable.
- **Frazzini, A. & Lamont, O. (2007), "The Earnings Announcement Premium and Trading Volume," NBER Working Paper 13090.** Stocks earn abnormally *positive* returns in the month of their scheduled earnings announcement — a premium the authors tie to attention-driven small-investor buying. Reported monthly strategy excess returns of ~7–18%/yr with high Sharpe. This is the closest published cousin of a "hold through the event and get paid" thesis, though the mechanism is attention/volume, not liquidity provision per se.
- **Nagel, S. (2012), "Evaporating Liquidity," *Review of Financial Studies* 25(7), 2005–2039.** Short-term reversal returns are a proxy for the *return to providing liquidity*; that return is strongly time-varying and highly predictable by VIX, spiking in turmoil. This is the theoretical backbone for "get paid to provide liquidity": the premium exists but is state-dependent and largest exactly when it is most dangerous to hold inventory.
- **Patell, J. & Wolfson, M. (1984), "The Intraday Speed of Adjustment of Stock Prices to Earnings and Dividend Announcements," *Journal of Financial Economics* 13(2), 223–252.** The initial price reaction to earnings shows up in the first few minutes; simple intraday trading-rule profits dissipate within 5–10 minutes. The blunt caveat for the flagship thesis: the *jump* is not daily-bar-capturable — only the multi-day drift/overreaction-reversal is.
- **Dubinsky, A., Johannes, M., Kaeck, A. & Seeger, N. (2019), "Option Pricing of Earnings Announcement Risks," *Review of Financial Studies* 32(2), 646–687.** Options embed a large, time-varying, quantitatively significant *anticipated* price-uncertainty component for earnings dates — i.e., implied vol systematically prices in an earnings jump, and realized announcement vol carries a risk premium. Supports the "sell overpriced earnings vol / provide insurance" version of the thesis — but this is an **options** result, not equity daily bars.

**How it maps to a testable template in this harness**
- *Signal:* SUE / earnings-surprise sign and magnitude (PEAD); or "hold long/short through the event window" liquidity/overreaction-reversal (buy the loser leg into/through the event, exit on mean-reversion, à la Nagel's short-term-reversal proxy).
- *Instruments:* single-name US equities, pooled across many names to build sample size; optionally an equal-weight event portfolio.
- *Data:* yfinance daily OHLCV for returns; an earnings-date source (yfinance `get_earnings_dates`, or a vendor calendar) for event timing; consensus/estimate data for a real SUE is the hard part — a crude proxy is the historical earnings-day return sign, which risks circularity.
- *Validity traps:* **look-ahead on earnings dates** (dates get revised; use the date known *before* the event, and lag any consensus that was itself revised); **survivorship** (yfinance won't give you delisted names — your event set is biased toward survivors); **tiny sample** (one event per firm-quarter — pool hard); **cost under-modeling** (event-window spreads widen exactly when you trade; borrow costs on the short leg spike).

**What the retail / daily-bar constraint kills**
- The announcement *jump* itself (Patell–Wolfson: gone in minutes) — not capturable at daily bars.
- The clean *options* version (Dubinsky et al.) needs an options chain + reliable IV, not equity OHLCV. Retail options data and per-contract costs make the "sell earnings straddles" version far harder than the backtest suggests.
- A real SUE needs point-in-time analyst consensus; without it you either proxy crudely or introduce look-ahead.

---

## 2. Statistical arbitrage / pairs trading

**Key references**

- **Gatev, E., Goetzmann, W. & Rouwenhorst, K. (2006), "Pairs Trading: Performance of a Relative-Value Arbitrage Rule," *Review of Financial Studies* 19(3), 797–827.** The reference distance-method study (1962–2002): match pairs by minimum sum-of-squared-deviations of normalized prices, trade divergence beyond 2σ. Reported average annualized excess returns up to ~11% for self-financing pair portfolios, exceeding conservative cost estimates *in that sample period*.
- **Elliott, R., van der Hoek, J. & Malcolm, W. (2005), "Pairs Trading," *Quantitative Finance* 5(3), 271–276.** Models the spread as a mean-reverting (Ornstein–Uhlenbeck / Gaussian state-space) process observed in noise, giving a filtering-based entry/exit and a formal half-life of mean reversion. The theoretical template for OU-based spread trading.
- **Avellaneda, M. & Lee, J. (2010), "Statistical Arbitrage in the US Equities Market," *Quantitative Finance* 10(7), 761–782.** Generalizes pairs to systematic stat-arb: residuals from regressing returns on PCA factors or sector ETFs are modeled as mean-reverting; trade the standardized residual ("s-score"). Reported Sharpe ~1.51 for the ETF-with-volume variant, 2003–2007 — but note the sample ends before broad decay and includes the 2007 quant quake it also studies.
- **Do, B. & Faff, R. (2010), "Does Simple Pairs Trading Still Work?," *Financial Analysts Journal* 66(4), 83–95;** and **Do, B. & Faff, R. (2012), "Are Pairs Trading Profits Robust to Trading Costs?," *Journal of Financial Research* 35(2), 261–287.** The essential reality check: pairs-trading profitability *declined steadily after the late 1990s*, and after realistic commissions, market impact, and short-sale costs the strategy is only marginally profitable (~30 bps/month for well-matched, industry-refined pairs) — and unprofitable for many naive implementations.
- **Vidyamurthy, G. (2004), *Pairs Trading: Quantitative Methods and Analysis*, Wiley. [practitioner text]** Popularized the *cointegration* approach (Engle–Granger test on the price series, trade the stationary spread). Useful as a how-to; not peer-reviewed evidence, and its examples are illustrative, not out-of-sample proof.

**How it maps to a testable template in this harness**
- *Signal:* z-score of a cointegrated/OU spread (enter beyond ±2σ, exit near 0), or Avellaneda-style residual s-score against a sector-ETF factor.
- *Instruments:* liquid US equity pairs within the same industry, or a name vs. its sector ETF (cleaner cointegration, easier borrow).
- *Data:* yfinance daily adjusted closes for both legs; form the spread on a *rolling* estimation window, trade out-of-sample.
- *Validity traps:* **look-ahead in pair selection / cointegration test** (selecting pairs on the full sample then "testing" on it is the classic sin — split formation vs. trading windows); **survivorship** (delisted legs / merger targets vanish from yfinance, biasing pairs toward those that stayed cointegrated); **cost under-modeling** (two legs = double spread + short borrow; Do–Faff shows this is decisive); **regime breaks** (cointegration relationships break, and the break *is* the max-drawdown).

**What the retail / daily-bar constraint kills**
- Daily bars are actually fine for pairs — this is one of the more retail-viable families. The killer is not resolution but **crowding + costs + borrow**: the published edge has decayed (Do–Faff) and the short leg's borrow/recall risk is real and hard to model from free data.

---

## 3. Macro-event / scheduled-announcement effects (planned FRED-based macro-release engine)

**Key references**

- **Lucca, D. & Moench, E. (2015), "The Pre-FOMC Announcement Drift," *Journal of Finance* 70(1), 329–371.** US equities earn large average excess returns in the ~24 hours *before* scheduled FOMC announcements — a sizable fraction of the total annual equity return, concentrated pre-announcement. No comparable pre-drift in Treasuries or money-market futures, and no such pre-drift before other macro releases. Directly testable on a calendar of FOMC dates.
- **Savor, P. & Wilson, M. (2013), "How Much Do Investors Care About Macroeconomic Risk? Evidence from Scheduled Economic Announcements," *Journal of Financial and Quantitative Analysis* 48(2), 343–375.** Average equity excess returns and Sharpe ratios are markedly higher on scheduled CPI, employment (NFP), and FOMC announcement days than on non-announcement days — consistent with a compensation-for-macro-risk premium earned on those specific dates.
- **Savor, P. & Wilson, M. (2014), "Asset Pricing: A Tale of Two Days," *Journal of Financial Economics* 113(2), 171–201.** On announcement days average excess market return ≈ 11.4 bps vs. ≈ 1.1 bps on other days (1958–2009); the CAPM/beta-return relation also holds much better on announcement days. Roughly ~60% of the equity premium is earned on the handful of days with inflation/employment/rate news.
- **Cieslak, A., Morse, A. & Vissing-Jorgensen, A. (2019), "Stock Returns over the FOMC Cycle," *Journal of Finance* 74(5), 2201–2248.** Since 1994 the equity premium has been earned almost entirely in *even weeks* (0, 2, 4, 6) of the FOMC cycle measured from the last meeting; odd weeks are flat/negative. A purely calendar-driven, daily-bar-testable timing rule tied to the Fed meeting schedule.

**How it maps to a testable template in this harness**
- *Signal:* binary/positional calendar rules — long the index in the pre-FOMC window (Lucca–Moench), long on scheduled announcement days (Savor–Wilson), long even-week / flat odd-week in FOMC-cycle time (Cieslak et al.).
- *Instruments:* a broad US equity index or liquid ETF (SPY); the effects are documented at the index level, so no single-name selection needed.
- *Data:* FRED / Fed release calendars for FOMC dates; BLS/Census schedules (or FRED release dates) for CPI and NFP; yfinance daily bars for index returns. Building the *point-in-time scheduled-date table* is the core engineering task.
- *Validity traps:* **look-ahead on the schedule** (use the pre-announced date, and be careful FOMC meeting dates were not always 8/yr historically); **regime/decay** (pre-FOMC drift is a post-1994ish phenomenon and has been widely publicized since 2015 — expect decay; check post-publication sub-samples); **multiple-testing** (there are dozens of macro releases — testing all of them and reporting the winners is p-hacking); **tiny sample of high-impact days** (only ~8 FOMC/yr — decades of data still give few hundred events).

**What the retail / daily-bar constraint kills**
- Relatively little — this is the family *best* matched to daily bars + FRED, because the alpha is a scheduled-date overnight/multi-day effect, not an intraday one. The main honest caveat is decay and publicity: these are among the most famous scheduled-event results, so assume they are partly crowded (McLean–Pontiff logic).

---

## 4. Order-flow imbalance / microstructure (data-gated for retail)

**Key references**

- **Kyle, A. (1985), "Continuous Auctions and Insider Trading," *Econometrica* 53(6), 1315–1335.** The foundational price-impact model: a risk-neutral informed trader hidden among noise traders; market makers set prices linearly in net order flow. Defines *Kyle's lambda* (market depth) and shows information is impounded gradually — the theoretical basis for "order flow moves price."
- **Cont, R., Kukanov, A. & Stoikov, S. (2014), "The Price Impact of Order Book Events," *Journal of Financial Econometrics* 12(1), 47–88.** Empirically, over short horizons price changes are driven mainly by *order-flow imbalance* (OFI) at the best bid/ask, with a linear price–OFI relation whose slope is inversely proportional to market depth; robust across stocks and intraday. The empirical OFI signal the microstructure literature builds on.
- **Chordia, T. & Subrahmanyam, A. (2004), "Order Imbalance and Individual Stock Returns: Theory and Evidence," *Journal of Financial Economics* 72(3), 485–518. `[UNVERIFIED]`** (Real paper to my knowledge — signed order imbalance predicts short-horizon individual-stock returns — but I did not confirm exact volume/pages/DOI via search; verify before citing.) This is the closest-to-daily-frequency member of the family and the one worth checking if you ever get signed-trade data.

**How it maps to a testable template in this harness**
- *Signal:* daily-aggregated order-flow imbalance or a Kyle-lambda-style impact estimate → short-horizon return prediction.
- *Instruments:* liquid equities.
- *Data:* requires L2 / limit-order-book messages or at least *signed* trades (Lee–Ready or exchange-provided sign). **yfinance daily OHLCV cannot produce this.**
- *Validity traps:* even with the right data — **microstructure noise, bid-ask bounce, and the fact that the impact you measure is partly your own** make naive OFI backtests wildly optimistic; the horizon is seconds-to-minutes, so daily rebalancing throws the signal away.

**What the retail / daily-bar constraint kills**
- Essentially the whole family, as a *tradable* strategy. Daily bars destroy the signal (it lives at the tick), and the data is not in your feed. Correct use for this harness: understand your *own* order's market impact and slippage model (use Kyle-lambda intuition to size orders), not to generate alpha.

---

## 5. News-driven equity strategies (textual sentiment)

**Key references**

- **Tetlock, P. (2007), "Giving Content to Investor Sentiment: The Role of Media in the Stock Market," *Journal of Finance* 62(3), 1139–1168.** Quantifying pessimism in a daily WSJ column: high media pessimism predicts short-term *downward* price pressure followed by *reversion to fundamentals*, and extreme pessimism predicts high trading volume. First clean link from measured text tone to returns.
- **Loughran, T. & McDonald, B. (2011), "When Is a Liability Not a Liability? Textual Analysis, Dictionaries, and 10-Ks," *Journal of Finance* 66(1), 35–65.** General-purpose (Harvard) sentiment dictionaries badly misclassify finance text — ~¾ of "negative" Harvard words are not negative in a financial context. They build finance-specific word lists (the widely-used LM dictionary) and link tone to filing returns, volume, volatility, and unexpected earnings. Essential *methodology* reference for any DIY sentiment signal.
- **Tetlock, P., Saar-Tsechansky, M. & Macskassy, S. (2008), "More Than Words: Quantifying Language to Measure Firms' Fundamentals," *Journal of Finance* 63(3), 1437–1467. `[UNVERIFIED]`** (Real to my knowledge — negative words in firm-specific news forecast lower earnings and returns, with a delayed price response — but I did not confirm exact pages/DOI via search; verify before citing.)

**How it maps to a testable template in this harness**
- *Signal:* daily firm- or market-level sentiment score (e.g., LM-dictionary tone of headlines / 8-K / 10-K text) → next-day/next-week return or a fade-the-overreaction trade.
- *Instruments:* single names with news coverage, or a market-level index for aggregate-sentiment timing (Tetlock).
- *Data:* a news/text feed (headlines, filings via SEC EDGAR for 10-K/8-K), plus the LM dictionary; yfinance for returns. EDGAR filings are free and point-in-time-datable; a broad *news* feed usually is not.
- *Validity traps:* **look-ahead / point-in-time text** (use the publish timestamp, not the filing's period; EDGAR gives filing datetime — respect it); **coverage/survivorship bias** in whatever news source you use; **the signal is largely about reversal of overreaction**, so it is small and fast and easily eaten by costs; **dictionary overfitting** if you tune word lists on the same sample you test.

**What the retail / daily-bar constraint kills**
- Less about daily bars, more about **data access and latency**: the tradable news edge is fast (Tetlock's reversion is short-horizon), and a retail news feed arrives after the fast money. Free EDGAR-filing text is the one genuinely retail-accessible corner — slower, dated, and testable — but the alpha there is thin and competed.

---

## 6. Cross-cutting critical note: decay and cost realism

- **McLean, R. & Pontiff, J. (2016), "Does Academic Research Destroy Stock Return Predictability?," *Journal of Finance* 71(1), 5–32.** Across 97 published cross-sectional predictors, out-of-sample returns are ~26% lower than in-sample, and post-publication returns ~58% lower — consistent with investors trading away documented anomalies. **Practical prior for this harness:** discount any published effect size by roughly half before you even start, and treat a backtest that merely *reproduces* the paper's in-sample number as a red flag, not a success.
- **Cost realism checklist for every template above:** model (a) half-spread per leg at the *event-time* spread, not the calm-day spread; (b) short-borrow cost and recall risk on any short leg; (c) slippage/impact scaled to your size (Kyle-lambda intuition); (d) the strategy's *own* post-publication decay window. If the edge only survives with zero costs or with the paper's original (pre-2000, institutional) cost assumptions, it is not your edge.

---

## Sources consulted / search notes

All of the following were checked via web search during this survey (publisher pages, SSRN/NBER, RePEc/IDEAS, JSTOR, or author copies). Where a search surfaced the journal landing page or an author PDF with matching title/authors/year/volume, I treated the citation as verified.

- Bernard & Thomas (1989) PEAD — verified via RePEc/IDEAS (JAR 27, 1–36) and JSTOR; SUE 41/48-quarter figure from search summaries.
- Ball & Brown (1968) — verified via RePEc/IDEAS (JAR 6(2), 159–178).
- Chan, Jegadeesh & Lakonishok (1996) "Momentum Strategies" — verified via Wiley/JF (51(5), 1681–1713), NBER w5375.
- Frazzini & Lamont (2007) "Earnings Announcement Premium and Trading Volume" — verified via NBER w13090; 7–18%/yr figure from NBER digest / working-paper summaries.
- Patell & Wolfson (1984) — verified via ScienceDirect/RePEc (JFE 13, 223–252).
- Dubinsky, Johannes, Kaeck & Seeger (2019) — verified via Oxford Academic RFS (32(2), 646–687, DOI 10.1093/rfs/hhy060).
- Nagel (2012) "Evaporating Liquidity" — verified via Oxford Academic RFS (25(7), 2005–2039), NBER w17653.
- Gatev, Goetzmann & Rouwenhorst (2006) — verified via Oxford Academic RFS (19(3), 797–827), NBER w7032; ~11% figure from SSRN/RFS abstract.
- Elliott, van der Hoek & Malcolm (2005) — verified via Taylor & Francis Quantitative Finance (5(3), 271–276).
- Avellaneda & Lee (2010) — verified via Taylor & Francis Quantitative Finance (10(7), 761–782, DOI 10.1080/14697680903124632); Sharpe 1.51 from abstract.
- Do & Faff (2010, FAJ 66(4), 83–95) and (2012, Journal of Financial Research 35(2), 261–287) — verified via Monash/Wiley; note the trading-costs paper is in *Journal of Financial Research*, not FAJ (search corrected an initial assumption). ~30 bps/month figure from summaries.
- Vidyamurthy (2004) *Pairs Trading* (Wiley) — practitioner book, widely cited; not independently page-verified (it is a book, not an article).
- Lucca & Moench (2015) "Pre-FOMC Announcement Drift" — verified via Wiley JF (70(1), 329–371), NY Fed Staff Report 512, SSRN.
- Savor & Wilson (2013) "How Much Do Investors Care About Macroeconomic Risk?" — verified via Cambridge JFQA; note there are two related Savor–Wilson papers.
- Savor & Wilson (2014) "Asset Pricing: A Tale of Two Days" — verified via ScienceDirect JFE (113(2), 171–201), SSRN; 11.4 vs 1.1 bps figure from summaries.
- Cieslak, Morse & Vissing-Jorgensen (2019) "Stock Returns over the FOMC Cycle" — verified via Wiley JF (74(5), 2201–2248), SSRN.
- Kyle (1985) — verified via Econometric Society / RePEc (Econometrica 53(6), 1315–1335).
- Cont, Kukanov & Stoikov (2014) — verified via Oxford Academic *Journal of Financial Econometrics* (12(1), 47–88); note the exact title is "The Price Impact of Order Book Events."
- Chordia & Subrahmanyam (2004) — **not fully verified via search** (surfaced in related-work references only); tagged `[UNVERIFIED]`.
- Tetlock (2007) — verified via Wiley JF (62(3), 1139–1168), SSRN.
- Loughran & McDonald (2011) — verified via Wiley JF (66(1), 35–65), Notre Dame SRAF.
- Tetlock, Saar-Tsechansky & Macskassy (2008) "More Than Words" — **not verified via search this session**; tagged `[UNVERIFIED]`.
- McLean & Pontiff (2016) — verified via Wiley JF (71(1), 5–32), SSRN; 26%/58% figures from abstract/summaries.

*Two citations (`Chordia & Subrahmanyam 2004`, `Tetlock–Saar-Tsechansky–Macskassy 2008`) are believed real but were not independently confirmed in this session — confirm before use.*
