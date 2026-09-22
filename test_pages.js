/**
 * 无浏览器验证新增页面（持仓盈亏 / 信号复盘）的渲染逻辑。
 * 用 vm 加载 app.js，注入模拟 DOM 与真实接口的样例数据，检查生成的 HTML。
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const src = fs.readFileSync(path.join(__dirname, 'app', 'web', 'app.js'), 'utf8');

const nodes = new Map();
function node(sel) {
    if (!nodes.has(sel)) {
        nodes.set(sel, {
            _html: '',
            style: {},
            dataset: {},
            classList: { add() {}, remove() {}, toggle() {} },
            setAttribute() {}, getAttribute() { return null; },
            addEventListener() {}, appendChild() {}, remove() {},
            insertAdjacentHTML(pos, html) { this._html += html; },
            getBoundingClientRect: () => ({ left: 0, top: 0, width: 1200, height: 430 }),
        });
    }
    const n = nodes.get(sel);
    Object.defineProperty(n, 'innerHTML', {
        configurable: true,
        get() { return this._html; },
        set(v) { this._html = v; },
    });
    return n;
}

const document = {
    querySelector: (s) => node(s),
    querySelectorAll: () => [],
    createElement: () => node('created-' + Math.random()),
    addEventListener() {},
};

const sandbox = {
    document,
    window: { addEventListener() {}, scrollTo() {} },
    fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
    console,
    setTimeout, clearTimeout, setInterval, clearInterval,
    alert: () => {}, confirm: () => true,
    Math, Date, JSON, Number, String, Object, Array, Boolean, isNaN, parseFloat, parseInt,
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

let mod;
try {
    mod = vm.runInContext(
        src + '\n; ({ renderPositions, renderReview, renderBacktest, renderOptimize,'
            + ' renderVolprice, vpCard, renderSourceCompare, renderChanTheory,'
            + ' renderOptHorizons, optStrategyCard,'
            + ' renderHotspots, amtCNY, signedAmt, loadDashboard,'
            + ' renderEssence, mdB, lvLabel, LEVELS, LEVEL_LABEL, renderDetail,'
            + ' MKT, isHK, mktOf, symDisp, symBadge, moneyM, applyMarket, setMarket, STATE,'
            + ' drawEquityCurve, fmtPct, money, cls })',
        sandbox
    );
} catch (e) {
    console.error('❌ 加载 app.js 失败:', e.message);
    process.exit(1);
}

const { renderPositions, renderReview, renderBacktest, renderOptimize,
    renderVolprice, vpCard, renderSourceCompare, renderChanTheory,
    renderOptHorizons, optStrategyCard,
    renderHotspots, amtCNY, signedAmt, loadDashboard,
    renderEssence, mdB, lvLabel, LEVELS, LEVEL_LABEL, renderDetail,
    MKT, isHK, mktOf, symDisp, symBadge, moneyM, applyMarket, setMarket, STATE,
    drawEquityCurve } = mod;

// ---------------- 持仓盈亏 ----------------
const posSample = {
    ok: true,
    summary: { count: 3, cost: 51000, market_value: 55302.84, pnl: 4302.84, pnl_pct: 8.44 },
    items: [
        { symbol: 'AAPL', name: '苹果', qty: 100, cost: 300, price: 334.15,
          market_value: 33415, pnl: 3415, pnl_pct: 11.38, signals: [], advice: '持有' },
        { symbol: 'TSLA', name: '特斯拉', qty: 30, cost: 400, price: 364.31,
          market_value: 10929.3, pnl: -1070.66, pnl_pct: -8.92,
          signals: [{ code: 'S3', side: 'sell' }], advice: '缠论出现卖点，考虑减仓或备兑看涨' },
    ],
};

let ok = true;
try {
    renderPositions(posSample);
} catch (e) {
    console.error('❌ renderPositions 抛异常:', e.message);
    ok = false;
}
const posHtml = node('#posTable').innerHTML;
const posStats = node('#posStats').innerHTML;
const posChecks = [
    ['汇总卡渲染', /浮动盈亏/.test(posStats) && /\+?\$4,?302\.84/.test(posStats)],
    ['表格含标的', /AAPL/.test(posHtml) && /TSLA/.test(posHtml)],
    ['盈亏百分比', /\+11\.38%/.test(posHtml) && /-8\.92%/.test(posHtml)],
    ['涨跌配色', /class="num (up|down)"/.test(posHtml)],
    ['操作建议', /备兑看涨/.test(posHtml)],
    ['无 undefined', !/undefined/.test(posHtml) && !/undefined/.test(posStats)],
    ['无 NaN', !/NaN/.test(posHtml)],
];

console.log('持仓盈亏页面：');
posChecks.forEach(([n, v]) => { console.log(`  ${v ? '✓' : '✗'} ${n}`); if (!v) ok = false; });

// ---------------- 信号复盘 ----------------
const revSample = {
    ok: true, total: 11, evaluated: 10, pending: 1, horizons: [5, 10, 20],
    stats: {
        overall: { '全部': { count: 10, win_rate: { 5: 80, 10: 60, 20: 30 },
                   avg_ret: { 5: 2.29, 10: 2.48, 20: 7.79 },
                   avg_mfe: { 5: 5.2, 10: 5.6, 20: 6.1 },
                   avg_mae: { 5: -1.7, 10: -2.9, 20: -4.0 } } },
        by_code: {
            B3: { count: 5, win_rate: { 5: 100, 10: 60, 20: 20 }, avg_ret: { 5: 3.17, 10: 2.8, 20: 7.49 },
                  avg_mfe: { 5: 4.9, 10: 5.3, 20: 6.0 }, avg_mae: { 5: -2.1, 10: -4.3, 20: -5.0 } },
            S3: { count: 3, win_rate: { 5: 33.3, 10: 33.3, 20: 33.3 }, avg_ret: { 5: 0.24, 10: -0.68, 20: 2.33 },
                  avg_mfe: { 5: 4.3, 10: 4.3, 20: 4.3 }, avg_mae: { 5: -2.0, 10: -6.6, 20: -11.9 } },
        },
    },
    items: [
        { symbol: 'TSLA', sig_date: '2026-08-04', code: 'S3', side: 'sell', price: 329.57, confidence: 0.75,
          outcome: { d5: 0.98, win5: false, d10: 2.22, win10: false, d20: 8.05, win20: false } },
        { symbol: 'MSFT', sig_date: '2026-08-18', code: 'B3', side: 'buy', price: 477.15, confidence: 0.7,
          outcome: { d5: 3.05, win5: true, d10: 5.0, win10: true, d20: null } },
    ],
};

try {
    renderReview(revSample);
} catch (e) {
    console.error('❌ renderReview 抛异常:', e.message);
    ok = false;
}
const revStats = node('#reviewStats').innerHTML;
const revTable = node('#reviewTable').innerHTML;
const revChecks = [
    ['胜率卡片', /80%/.test(revStats) && /个交易日后/.test(revStats)],
    ['MFE/MAE 展示', /最大有利/.test(revStats) && /最大不利/.test(revStats)],
    ['分类型统计表', /分信号类型统计/.test(revStats) && /B3/.test(revStats) && /S3/.test(revStats)],
    ['明细表', /TSLA/.test(revTable) && /2026-08-04/.test(revTable)],
    ['胜负标记', /[✓✗]/.test(revTable)],
    ['观察期说明', /观察期|已评估/.test(revStats)],
    ['无 undefined', !/undefined/.test(revStats) && !/undefined/.test(revTable)],
    ['无 NaN', !/NaN/.test(revStats) && !/NaN/.test(revTable)],
];

console.log('\n信号复盘页面：');
revChecks.forEach(([n, v]) => { console.log(`  ${v ? '✓' : '✗'} ${n}`); if (!v) ok = false; });

// ---------------- 策略回测 ----------------
const btSample = {
    ok: true,
    n_concurrent: 1.4,
    symbols: ['AAPL', 'NVDA', 'TSLA'],
    stats: {
        n_trades: 190, win_rate: 63.2, avg_pnl: 1.573, profit_factor: 1.48,
        max_drawdown: 21.7, sharpe: 0.89, total_return: 140.67,
        avg_bars: 12.4, avg_mfe: 4.2, avg_mae: -2.1,
        best: 30.1, worst: -10.2,
    },
    equity: [1.0, 1.02, 0.98, 1.05, 1.12, 1.09, 1.18, 1.25, 1.31, 1.28, 1.35],
    trades: [
        { symbol: 'AAPL', code: 'B1', side: 'long', entry_date: '2025-03-01',
          entry_price: 220.5, exit_date: '2025-03-20', exit_price: 245.1,
          pnl_pct: 11.16, exit_reason: 'take_profit', mfe: 12.4, mae: -1.2, bars_held: 19 },
        { symbol: 'TSLA', code: 'B3', side: 'long', entry_date: '2025-04-02',
          entry_price: 250.0, exit_date: '2025-04-10', exit_price: 240.0,
          pnl_pct: -4.0, exit_reason: 'stop_loss', mfe: 1.5, mae: -4.4, bars_held: 8 },
    ],
};

try {
    renderBacktest(btSample);
} catch (e) {
    console.error('❌ renderBacktest 抛异常:', e.message);
    ok = false;
}
const btHtml = node('#btResult').innerHTML;
const eqHtml = node('#equityBox').innerHTML;
const btChecks = [
    ['统计卡片', /交易笔数/.test(btHtml) && /63\.2%/.test(btHtml)],
    ['盈亏指标', /盈亏比/.test(btHtml) && /1\.48/.test(btHtml)],
    ['回撤与夏普', /21\.7%/.test(btHtml) && /0\.89/.test(btHtml)],
    ['权益曲线 SVG', /<svg/.test(eqHtml) && /<path/.test(eqHtml)],
    ['权益曲线无 NaN', !/NaN/.test(eqHtml)],
    ['出场原因分布', /止盈/.test(btHtml) && /止损/.test(btHtml)],
    ['信号分布', /B1/.test(btHtml) && /买点/.test(btHtml)],
    ['交易明细', /2025-03-01/.test(btHtml) && /take_profit|止盈/.test(btHtml)],
    ['无 undefined', !/undefined/.test(btHtml)],
];

console.log('\n策略回测页面：');
btChecks.forEach(([n, v]) => { console.log(`  ${v ? '✓' : '✗'} ${n}`); if (!v) ok = false; });

// ---------------- 量价提醒 ----------------
const vpSample = {
    ok: true,
    scanned: 14,
    threshold: 55,
    date: '2026-09-11',
    buys: [
        {
            symbol: 'MSFT', name: '微软', price: 495.63, change_pct: 0.82,
            date: '2026-09-11', has_position: true,
            relation: { label: '价涨量增', tone: 'good', chg: 0.82, vr: 1.35 },
            signal_name: '缩量回踩', code: 'VP_B2', side: 'buy',
            confidence: 56.1, level: '低', stop: 479.64, target: 527.63,
            resonance: false,
            reasons: ['缩量至 0.68 倍均量', '回踩 MA20 附近但仍在均线上方，上升结构未破'],
            metrics: { vr: 0.68, cmf: 0.05, mfi: 52.0 },
            desc: '上升趋势中缩量回调至均线附近，抛压枯竭，洗盘结束',
        },
        {
            symbol: 'AAPL', name: '苹果', price: 326.57, change_pct: 2.14,
            date: '2026-09-11', has_position: false,
            relation: { label: '价涨量增', tone: 'good', chg: 2.14, vr: 1.67 },
            signal_name: '放量突破', code: 'VP_B1', side: 'buy',
            confidence: 78.4, level: '高', stop: 313.2, target: 353.31,
            resonance: true,
            reasons: ['放量 1.67 倍突破 20 日新高', 'CMF=+0.13，资金净流入', '与缠论买卖点同向共振，可信度提升'],
            metrics: { vr: 1.67, cmf: 0.134, mfi: 57.1 },
            desc: '放量突破近期新高，量价齐升，主力资金推动趋势启动',
        },
    ],
    sells: [
        {
            symbol: 'NVDA', name: '英伟达', price: 218.36, change_pct: -1.2,
            date: '2026-09-11', has_position: false,
            relation: { label: '价跌量缩', tone: 'neutral', chg: -1.2, vr: 0.82 },
            signal_name: '量价顶背离', code: 'VP_S2', side: 'sell',
            confidence: 92.0, level: '高', stop: 232.77, target: 189.32,
            resonance: true,
            reasons: [
                '价格自 2026-08-17 的 227.92 推高至 2026-08-27 的 230.47（+1.1%），创出更高的高点',
                '但 OBV 未同步创新高，增量资金不足',
                '与缠论买卖点同向共振，可信度提升',
            ],
            metrics: { mfi: 47.7, cmf: -0.32 },
            desc: '价格创新高但 OBV/MFI 未跟随创新高，上涨失去量能支撑',
        },
        {
            symbol: 'PLTR', name: '帕兰提尔', price: 167.23, change_pct: 0.5,
            date: '2026-09-11', has_position: false,
            relation: { label: '价涨量缩', tone: 'warn', chg: 0.5, vr: 0.54 },
            signal_name: '量价顶背离', code: 'VP_S2', side: 'sell',
            confidence: 68.0, level: '中', stop: 190.25, target: 121.19,
            resonance: false,
            reasons: ['价格创出更高的高点', 'MFI 自 71 回落至 38，资金流与价格背离'],
            metrics: { mfi: 38.2, cmf: -0.098 },
            desc: '价格创新高但 OBV/MFI 未跟随创新高，上涨失去量能支撑',
        },
    ],
    errors: ['TENCENT: 获取 TENCENT 数据失败'],
};

try {
    renderVolprice(vpSample);
} catch (e) {
    console.error('❌ renderVolprice 抛异常:', e.message);
    ok = false;
}
const vpStatsHtml = node('#vpStats').innerHTML;
const vpBuyHtml = node('#vpBuys').innerHTML;
const vpSellHtml = node('#vpSells').innerHTML;
const vpChecks = [
    ['统计卡四项', /买入提醒/.test(vpStatsHtml) && /卖出提醒/.test(vpStatsHtml)
        && /高置信信号/.test(vpStatsHtml) && /已扫描标的/.test(vpStatsHtml)],
    ['扫描标的数', /14/.test(vpStatsHtml)],
    ['买入卡片', /vp-card buy/.test(vpBuyHtml) && /MSFT/.test(vpBuyHtml) && /AAPL/.test(vpBuyHtml)],
    ['卖出卡片', /vp-card sell/.test(vpSellHtml) && /NVDA/.test(vpSellHtml) && /PLTR/.test(vpSellHtml)],
    ['信号名渲染', /缩量回踩/.test(vpBuyHtml) && /量价顶背离/.test(vpSellHtml)],
    ['股票名渲染', /微软/.test(vpBuyHtml) && /英伟达/.test(vpSellHtml)],
    ['置信度条', /conf-bar/.test(vpBuyHtml) && /width:78/.test(vpBuyHtml)],
    ['共振标记', /缠论共振/.test(vpBuyHtml) && /缠论共振/.test(vpSellHtml)],
    ['持仓标记', /持仓中/.test(vpBuyHtml)],
    ['量价状态标签', /价涨量增/.test(vpBuyHtml) && /价跌量缩/.test(vpSellHtml)],
    ['止损与目标', /止损/.test(vpBuyHtml) && /目标/.test(vpBuyHtml) && /479\.64/.test(vpBuyHtml)],
    ['理由列表', /vp-reasons/.test(vpBuyHtml) && /回踩 MA20/.test(vpBuyHtml)],
    ['取数失败提示', /TENCENT/.test(vpBuyHtml)],
    ['无 undefined', !/undefined/.test(vpBuyHtml + vpSellHtml + vpStatsHtml)],
    ['无 NaN', !/NaN/.test(vpBuyHtml + vpSellHtml + vpStatsHtml)],
];

console.log('\n量价提醒页面：');
vpChecks.forEach(([n, v]) => { console.log(`  ${v ? '✓' : '✗'} ${n}`); if (!v) ok = false; });

// 空状态
try {
    renderVolprice({ ok: true, scanned: 14, threshold: 55, buys: [], sells: [], errors: [] });
    const emptyBuy = node('#vpBuys').innerHTML;
    const emptyOk = /暂无买入提醒/.test(emptyBuy) && /暂无卖出提醒/.test(node('#vpSells').innerHTML);
    console.log(`  ${emptyOk ? '✓' : '✗'} 空状态提示`);
    if (!emptyOk) ok = false;
} catch (e) {
    console.log('  ✗ 空状态渲染异常: ' + e.message);
    ok = false;
}

// 信号源对比回测
const srcSample = {
    ok: true, oos_ratio: 0.7,
    symbols: ['AAPL', 'NVDA', 'TSLA', 'AMD', 'MSFT'],
    rows: [
        {
            source: 'chan', name: '缠论买卖点', desc: '分型-笔-中枢-背驰体系下的三类买卖点',
            in_sample: { n_trades: 155, win_rate: 60.6, avg_pnl: 1.2, profit_factor: 1.4 },
            out_sample: { n_trades: 51, win_rate: 51.0, avg_pnl: -0.953, profit_factor: 0.9 },
        },
        {
            source: 'volprice', name: '量价关系', desc: '量能配合与背离形态识别出的买卖点',
            in_sample: { n_trades: 371, win_rate: 64.7, avg_pnl: 2.088, profit_factor: 1.7 },
            out_sample: { n_trades: 108, win_rate: 50.0, avg_pnl: -0.16, profit_factor: 1.0 },
        },
        {
            source: 'resonance', name: '缠论+量价共振', desc: '两套体系同向时才入场',
            in_sample: { n_trades: 56, win_rate: 76.8, avg_pnl: 3.775, profit_factor: 3.1 },
            out_sample: { n_trades: 13, win_rate: 69.2, avg_pnl: 1.435, profit_factor: 2.2 },
        },
    ],
    verdict: {
        best_by_expectancy: 'resonance', best_name: '缠论+量价共振',
        volprice_enough_samples: true,
        note: '共振信号样本外单笔期望 +1.44%（缠论 -0.95% / 量价 -0.16%），两套体系互相验证后质量最高。',
    },
    exit: { stop_pct: 0.1, take_pct: 0.08, max_hold: 25 },
};

try {
    renderSourceCompare(srcSample);
} catch (e) {
    console.error('❌ renderSourceCompare 抛异常:', e.message);
    ok = false;
}
const srcHtml = node('#srcCompareResult').innerHTML;
const srcChecks = [
    ['三种信号源', /缠论买卖点/.test(srcHtml) && /量价关系/.test(srcHtml) && /缠论\+量价共振/.test(srcHtml)],
    ['样本内外表头', /样本内/.test(srcHtml) && /样本外/.test(srcHtml)],
    ['样本内数字', /155/.test(srcHtml) && /60\.6%/.test(srcHtml)],
    ['样本外数字', /-0\.95%/.test(srcHtml) && /-0\.16%/.test(srcHtml) && /1\.44%/.test(srcHtml)],
    ['结论', /样本外单笔期望最优/.test(srcHtml) && /缠论\+量价共振/.test(srcHtml)],
    ['结论说明', /两套体系互相验证/.test(srcHtml)],
    ['出场规则', /止损 10%/.test(srcHtml) && /5 只标的/.test(srcHtml)],
    ['无 undefined', !/undefined/.test(srcHtml)],
    ['无 NaN', !/NaN/.test(srcHtml)],
];

console.log('\n信号源对比页面：');
srcChecks.forEach(([n, v]) => { console.log(`  ${v ? '✓' : '✗'} ${n}`); if (!v) ok = false; });

// ---------------- 缠论图解 ----------------
try {
    renderChanTheory();
} catch (e) {
    console.error('❌ renderChanTheory 抛异常:', e.message);
    ok = false;
}
const cConcepts = node('#chanConcepts').innerHTML;
const cDiag = node('#chanDiagrams').innerHTML;
const cImpl = node('#chanImpl').innerHTML;
const cUp = node('#chanUpgrade').innerHTML;
const cLimit = node('#chanLimits').innerHTML;
const cLim = node('#chanLimits').innerHTML;
const cAll = cConcepts + cDiag + cImpl + cUp + cLim;

const svgCount = (cDiag.match(/<svg /g) || []).length;
const chanChecks = [
    ['六个核心思想', /走势必完美/.test(cConcepts) && /级别/.test(cConcepts)
        && /自同构性/.test(cConcepts) && /中枢/.test(cConcepts)
        && /背驰/.test(cConcepts) && /三类买卖点/.test(cConcepts)],
    ['七步图解齐全', /K线包含处理/.test(cDiag) && /分型/.test(cDiag) && /笔/.test(cDiag)
        && /线段/.test(cDiag) && /中枢/.test(cDiag) && /背驰/.test(cDiag) && /三类买卖点/.test(cDiag)],
    ['SVG 图数量为7', svgCount === 7],
    ['SVG 有实际图元', /<rect/.test(cDiag) && /<line/.test(cDiag) && /<polyline/.test(cDiag)],
    ['SVG 坐标无 NaN', !/NaN/.test(cDiag)],
    ['SVG 无 undefined', !/undefined/.test(cDiag)],
    ['中枢图标注 ZG/ZD', /ZG 上沿/.test(cDiag) && /ZD 下沿/.test(cDiag)],
    ['背驰图含MACD', /MACD/.test(cDiag) && /面积反而小/.test(cDiag)],
    ['买卖点标注B1B2B3', /B1/.test(cDiag) && /B2/.test(cDiag) && /B3/.test(cDiag)],
    ['实现对照表', /merge_klines/.test(cImpl) && /find_fractals/.test(cImpl)
        && /build_strokes/.test(cImpl) && /detect_divergence/.test(cImpl)],
    ['实现程度标记', /完整实现/.test(cImpl) && /实用简化/.test(cImpl) && /两级近似/.test(cImpl)
        && /已实现/.test(cImpl)],
    ['五档多周期对照', /get_intraday/.test(cImpl) && /30分钟 \/ 1小时 \/ 4小时 \/ 日线 \/ 周线/.test(cImpl)],
    ['精髓层条目', /走势必完美/.test(cImpl) && /中枢演化/.test(cImpl) && /chan_essence/.test(cImpl)],
    ['局限清单含数据限制', /分钟线历史长度/.test(cLimit) && /成笔阈值的级别差异/.test(cLimit)],
    ['七项升级', /线段构建去主观化/.test(cUp) && /无未来函数的回测/.test(cUp)
        && /次日开盘成交/.test(cUp) && /量价共振/.test(cUp) && /期权自动映射/.test(cUp)],
    ['升级含选择偏差说明', /选择偏差/.test(cUp)],
    ['局限性说明', /区间套/.test(cLim) && /特征序列/.test(cLim)],
    ['全页无 undefined', !/undefined/.test(cAll)],
    ['全页无 NaN', !/NaN/.test(cAll)],
];

console.log('\n缠论图解页面：');
chanChecks.forEach(([n, v]) => { console.log(`  ${v ? '✓' : '✗'} ${n}`); if (!v) ok = false; });

// ---------------- 参数自迭代 ----------------
const optSample = {
    ok: true, accepted: true, objective_name: '收益/回撤比（推荐）',
    n_samples: 40, split: 0.7, min_trades: 25, symbols: ['AAPL', 'NVDA'],
    verdict: '样本外表现优于默认参数，建议应用',
    best: {
        params: { min_stroke_bars: 3, power_ratio: 0.8, stop_pct: 0.1, take_pct: 0.08,
                  max_hold: 25, use_chan_stop: false, exit_on_reverse: false },
        in_sample: { n_trades: 142, win_rate: 65.5, avg_pnl: 2.052, profit_factor: 2.1,
                     max_drawdown: 15.2, sharpe: 1.4, avg_bars: 11.0 },
        out_sample: { n_trades: 48, win_rate: 56.2, avg_pnl: 0.156, profit_factor: 1.2,
                      max_drawdown: 8.1, sharpe: 0.4, avg_bars: 10.2 },
    },
    baseline: {
        params: { min_stroke_bars: 4, power_ratio: 1.0, stop_pct: 0.06, take_pct: 0.12,
                  max_hold: 20, use_chan_stop: true, exit_on_reverse: true },
        in_sample: { n_trades: 250, win_rate: 51.2, avg_pnl: 0.961, profit_factor: 1.3,
                     max_drawdown: 30.0, sharpe: 0.6, avg_bars: 6.0 },
        out_sample: { n_trades: 116, win_rate: 30.2, avg_pnl: -0.858, profit_factor: 0.8,
                      max_drawdown: 22.0, sharpe: -0.3, avg_bars: 5.0 },
    },
    top: [
        { params: { min_stroke_bars: 3, power_ratio: 0.8, stop_pct: 0.1, take_pct: 0.08,
                    max_hold: 25, use_chan_stop: false, exit_on_reverse: false },
          in_sample: { n_trades: 142, win_rate: 65.5, avg_pnl: 2.052 },
          out_sample: { n_trades: 48, avg_pnl: 0.156 } },
    ],
};

try {
    renderOptimize(optSample);
} catch (e) {
    console.error('❌ renderOptimize 抛异常:', e.message);
    ok = false;
}
const opHtml = node('#opResult').innerHTML;
const insightHtml = node('#optInsight').innerHTML;
const opChecks = [
    ['结论卡', /建议应用/.test(opHtml) && /样本外表现优于默认参数/.test(opHtml)],
    ['样本内vs样本外对比', /最优\(样本内\)/.test(opHtml) && /默认\(样本外\)/.test(opHtml)],
    ['关键数字', /65\.5%/.test(opHtml) && /56\.2%/.test(opHtml) && /-0\.858%/.test(opHtml)],
    ['参数对比表', /成笔K线数/.test(opHtml) && /缠论关键位止损/.test(opHtml)],
    ['候选排序', /候选参数排序/.test(opHtml)],
    ['洞察解释', /关闭「缠论关键位止损」/.test(insightHtml)],
    ['过拟合提示', /过拟合/.test(insightHtml)],
    ['应用按钮', /应用这组参数/.test(opHtml)],
    ['无 undefined', !/undefined/.test(opHtml)],
    ['无 NaN', !/NaN/.test(opHtml)],
];

console.log('\n参数自迭代页面：');
opChecks.forEach(([n, v]) => { console.log(`  ${v ? '✓' : '✗'} ${n}`); if (!v) ok = false; });


// ---------------- 期权策略矩阵（期限 × 方向） ----------------
const hzSample = {"context":{"direction":"bullish","buys":["B2"],"sells":[],"breakout":false,"label":"看多"},"iv_level":"normal","horizons":{"short":{"key":"short","name":"短期","label":"短期 · 约 30–45 天","target_dte":45,"traits":["时间价值衰减（Theta）最快，方向对了也可能被耗死","Gamma 大，标的的小幅波动就能放大成高杠杆收益","单笔资金占用小、最大亏损锁定，适合信号明确时快速兑现"],"best_for":"缠论买卖点刚确认、趋势即将启动或破位的时点","watch":"信号证伪要立刻走，不能像持股那样「扛一扛」","expiration":"2026-10-28","dte":45,"theoretical":true,"available":true,"call":[{"name":"牛市看涨价差","en":"Bull Call Spread","option_type":"CALL","role":"价差","stance":"bullish","score":82,"suit":"看多但不认为会暴涨，或 IV 偏高想降低成本","advice":"买入 1 张 325 Call，同时卖出 1 张同到期 358 Call，净成本约 $1314","reason":"卖腿收的权利金直接降低净成本与 Theta 损耗，IV 偏高时卖腿更值钱，性价比通常优于单腿买 Call。代价是收益封顶——适合『方向有把握、幅度没把握』的情形","legs":[{"action":"BUY","type":"CALL","strike":325,"last":19.59,"bid":19,"ask":20.18,"volume":0,"open_interest":764,"iv":0.3208,"delta":0.6176,"gamma":0.010219,"theta":-1.5669,"vega":0.4451,"moneyness":-0.0219,"theoretical":true},{"action":"SELL","type":"CALL","strike":357.5,"last":6.45,"bid":6.26,"ask":6.65,"volume":0,"open_interest":678,"iv":0.3229,"delta":0.2914,"gamma":0.009191,"theta":-1.4004,"vega":0.4003,"moneyness":0.0759,"theoretical":true}],"premium":1314,"max_profit":1936,"max_loss":1314,"breakeven":338.14,"capital":1314,"risk":"收益封顶，但最大亏损有限且已知"},{"name":"买入看涨期权","en":"Long Call","option_type":"CALL","role":"买方","stance":"bullish","score":78,"suit":"缠论买点确认、或三类买点突破成立，预期短期内上行","advice":"买入 1 张约 45 天到期、行权价 325 （Delta 0.62）的 Call；单笔投入控制在总资金的 2–5%","reason":"短期方向性工具里杠杆最高的一种，最大亏损就是权利金。Delta 0.55–0.65 附近时间价值占比适中，不需要大涨即可获利，又不像深度虚值那样几乎必然归零。代价是 Theta 衰减快——信号一旦证伪要立刻离场，不能像持股那样扛","legs":[{"action":"BUY","type":"CALL","strike":325,"last":19.59,"bid":19,"ask":20.18,"volume":0,"open_interest":764,"iv":0.3208,"delta":0.6176,"gamma":0.010219,"theta":-1.5669,"vega":0.4451,"moneyness":-0.0219,"theoretical":true}],"premium":1959,"max_profit":"不限（上涨空间越大赚越多）","max_loss":1959,"breakeven":344.59,"capital":1959,"risk":"需上涨 3.7% 才盈亏平衡；到期归零风险"}],"put":[{"name":"现金担保卖出看跌","en":"Cash-Secured Put","option_type":"PUT","role":"卖方","stance":"bullish","score":80,"suit":"缠论一/二类买点附近，愿意在更低价位接货","advice":"卖出 1 张 45 天、行权价 312 的 Put，同时预留 $31,200 现金以备被行权","reason":"上涨或横盘 → 权利金白赚；下跌 → 按低于现价的成本接货。缠论一/二类买点本就带有接货意愿，于是『等回调』这件事本身开始产生收益。实际接货成本为 305.98，比现价低 7.9%","legs":[{"action":"SELL","type":"PUT","strike":312,"last":6.02,"bid":5.84,"ask":6.2,"volume":0,"open_interest":702,"iv":0.3223,"delta":-0.2538,"gamma":0.008581,"theta":-1.2874,"vega":0.3737,"moneyness":-0.061,"theoretical":true}],"premium":602,"max_profit":602,"max_loss":30598,"breakeven":305.98,"capital":31200,"risk":"标的大跌时仍须按行权价买入，需预留全额资金"},{"name":"熊市看跌价差","en":"Bear Put Spread","option_type":"PUT","role":"价差","stance":"bearish","score":34,"suit":"看空且 IV 偏高，想降低买 Put 的成本","advice":"买入 1 张 338 Put，同时卖出 1 张同到期 312 Put，净成本约 $1103","reason":"IV 偏高时单腿买 Put 太贵，加一条卖腿可显著降低成本与时间损耗，且最大亏损在建仓时就已经确定。代价是收益封顶——适合『看空但认为跌幅有限』的判断","legs":[{"action":"BUY","type":"PUT","strike":338,"last":17.05,"bid":16.54,"ask":17.56,"volume":0,"open_interest":772,"iv":0.3206,"delta":-0.5199,"gamma":0.010674,"theta":-1.5924,"vega":0.4649,"moneyness":0.0172,"theoretical":true},{"action":"SELL","type":"PUT","strike":312,"last":6.02,"bid":5.84,"ask":6.2,"volume":0,"open_interest":702,"iv":0.3223,"delta":-0.2538,"gamma":0.008581,"theta":-1.2874,"vega":0.3737,"moneyness":-0.061,"theoretical":true}],"premium":1103,"max_profit":1497,"max_loss":1103,"breakeven":326.97,"capital":1103,"risk":"收益封顶；若跌幅超预期，超额部分与你无关"}]},"long":{"key":"long","name":"中长期","label":"中长期 · 约 6–12 个月（LEAPS）","target_dte":240,"traits":["Theta 衰减慢，容错时间长，不必押注「马上就涨」","深度实值 Call 可替代持股，资金占用显著降低","适合趋势持有、低位分批建仓、以及长期持仓的保险"],"best_for":"周线级别方向明确，或需要长期持有 / 保护已有持仓","watch":"权利金总额高，一旦判断错误，绝对亏损金额不小","expiration":"2027-05-11","dte":240,"theoretical":true,"available":true,"call":[{"name":"长期看涨期权（股票替代）","en":"LEAPS Long Call","option_type":"CALL","role":"买方","stance":"bullish","score":81,"suit":"周线级别方向向上，希望用更小资金获得长期上涨敞口","advice":"买入 1 张约 240 天（0.7 年）、行权价 286 的深度实值 Call（Delta 0.79）","reason":"Delta 接近 1，涨跌几乎同步跟随正股，但资金占用通常只有直接持股的 30–50%（本合约价格 65.58，其中内在价值 46.27）。Theta 极小，不必押注『马上就涨』，适合周线方向明确、但短期节奏不确定的长期布局","legs":[{"action":"BUY","type":"CALL","strike":286,"last":65.58,"bid":63.61,"ask":67.54,"volume":0,"open_interest":577,"iv":0.3253,"delta":0.7922,"gamma":0.003322,"theta":-0.5252,"vega":0.7717,"moneyness":-0.1393,"theoretical":true}],"premium":6558,"max_profit":"不限","max_loss":6558,"breakeven":351.58,"capital":6558,"risk":"无股息、无投票权；到期前若未上涨仍会损失时间价值部分"},{"name":"长期看涨 + 卖近月 Call","en":"Poor Man's Covered Call","option_type":"CALL","role":"组合","stance":"bullish","score":79,"suit":"长期看多，但判断中间会反复震荡","advice":"买入 1 张 240 天的深度实值 Call（行权价 273），再逐月卖出约 30–45 天、Delta 0.3 左右的虚值 Call","reason":"用长期 Call 替代正股来做备兑，资金效率高于直接持股——同样的看多敞口，占用的资金少得多，且下行最多亏掉权利金。靠反复卖近月 Call 持续摊薄长期 Call 的成本。风险是正股大跌时，长期 Call 同样承受亏损","legs":[{"action":"BUY","type":"CALL","strike":273,"last":74.93,"bid":72.69,"ask":77.18,"volume":0,"open_interest":514,"iv":0.3268,"delta":0.8397,"gamma":0.002825,"theta":-0.4508,"vega":0.6562,"moneyness":-0.1784,"theoretical":true}],"premium":7493,"max_profit":"长期 Call 上涨空间 - 净成本（卖 Call 会封顶）","max_loss":7493,"breakeven":347.93,"capital":7493,"risk":"结构复杂；股价大涨时被卖腿封顶，需滚动管理"}],"put":[{"name":"长期卖出看跌","en":"Long-Term Cash-Secured Put","option_type":"PUT","role":"卖方","stance":"bullish","score":78,"suit":"认可长期价值，想在更低价格分批建仓","advice":"卖出 1 张 240 天、行权价 299 的 Put，预留 $29,900 现金","reason":"把『挂单等回调』变成有收益的事——等到了按更低成本接货，等不到就白收权利金。期限越长，容错空间越大：即便短期被套，也有足够时间等缠论结构修复，而不像短周期那样被迫在最低点附近做决定","legs":[{"action":"SELL","type":"PUT","strike":299,"last":15.56,"bid":15.1,"ask":16.03,"volume":0,"open_interest":639,"iv":0.3238,"delta":-0.2602,"gamma":0.003764,"theta":-0.5575,"vega":0.8743,"moneyness":-0.1001,"theoretical":true}],"premium":1556,"max_profit":1556,"max_loss":28344,"breakeven":283.44,"capital":29900,"risk":"资金长期被占用；标的大跌时接货成本仍高于市价"},{"name":"保护性看跌","en":"Protective Put","option_type":"PUT","role":"买方","stance":"defensive","score":9,"suit":"长期持有正股，担心大幅回撤又不想卖出（避免踏空或税费）","advice":"每持有 100 股，买入 1 张 240 天、行权价 318 （Delta -0.34）的 Put","reason":"相当于给持仓设了一个『最坏情况』：无论跌到多少，都能按 318 卖出，而上涨空间仍然全部保留。相比反复买 1 个月 Put，长期 Put 的单次成本低、不用频繁滚动，也不会因为某次忘了续保而裸奔","legs":[{"action":"BUY","type":"PUT","strike":318.5,"last":23.1,"bid":22.41,"ask":23.8,"volume":0,"open_interest":733,"iv":0.3216,"delta":-0.3448,"gamma":0.004273,"theta":-0.6304,"vega":0.9925,"moneyness":-0.0414,"theoretical":true}],"premium":2310,"max_profit":"正股上涨空间 - 权利金","max_loss":3687,"breakeven":355.37,"capital":2310,"risk":"持续支出权利金；若不跌则这笔钱沉没"}]}}};

// 空合约分支（该期限拿不到报价）
const hzEmpty = {
    context: { direction: 'neutral', buys: [], sells: [], breakout: false, label: '无明确信号' },
    iv_level: 'normal',
    horizons: {
        short: {
            key: 'short', name: '短期', label: '短期 · 约 30–45 天', target_dte: 45,
            traits: ['时间价值衰减（Theta）最快'], best_for: '缠论买卖点刚确认',
            watch: '信号证伪要立刻走', expiration: '2026-10-28', dte: 45,
            theoretical: true, available: false, call: [], put: [],
            note: '未能获取该期限的期权报价（可尝试流动性更好的标的）',
        },
    },
};

let hzHtml = '';
try {
    hzHtml = renderOptHorizons(hzSample);
    renderOptHorizons(hzEmpty);          // 空列表不应抛异常
    renderOptHorizons(null);             // 缺数据应直接返回空串
} catch (e) {
    console.error('❌ renderOptHorizons 抛异常:', e.message);
    ok = false;
}
const hzEmptyHtml = renderOptHorizons(hzEmpty);
const hzChecks = [
    ['主标题', /操作建议矩阵 · 期限 × 方向/.test(hzHtml)],
    ['当前信号方向标签', /当前：缠论看多/.test(hzHtml)],
    ['短期块', /短期 · 约 30–45 天/.test(hzHtml) && /剩余 45 天/.test(hzHtml)],
    ['中长期块', /中长期 · 约 6–12 个月（LEAPS）/.test(hzHtml) && /剩余 240 天/.test(hzHtml)],
    ['理论报价角标', /理论报价/.test(hzHtml)],
    ['特征与警示', /Theta/.test(hzHtml) && /⚠/.test(hzHtml)],
    ['CALL 分组', /▲ CALL · 看涨与多头工具/.test(hzHtml)],
    ['PUT 分组', /▼ PUT · 看跌、防守与建仓工具/.test(hzHtml)],
    ['Call 卡片样式', /class="opt-strategy is-call"/.test(hzHtml)],
    ['Put 卡片样式', /class="opt-strategy is-put"/.test(hzHtml)],
    ['策略英文名', /Bull Call Spread/.test(hzHtml) && /Cash-Secured Put/.test(hzHtml)],
    ['操作建议文本', /操作建议/.test(hzHtml) && /净成本约 \$1314/.test(hzHtml)],
    ['操作理由文本', /操作理由/.test(hzHtml) && /Theta 损耗/.test(hzHtml)],
    ['中长期建议', /长期看涨期权（股票替代）/.test(hzHtml) && /深度实值 Call/.test(hzHtml)],
    ['角色标签', /买方/.test(hzHtml) && /卖方/.test(hzHtml) && /价差/.test(hzHtml)],
    ['打分着色', /class="opt-score" style="color:var\(--up\)"/.test(hzHtml)],
    ['合约腿', /class="opt-leg"/.test(hzHtml) && /Delta/.test(hzHtml)],
    ['风险提示', /到期归零风险|收益封顶|最大亏损/.test(hzHtml)],
    ['空列表不报错', /暂无可选合约/.test(hzEmptyHtml) && !/opt-strategy/.test(hzEmptyHtml)],
    ['空数据显示提示', /未能获取该期限的期权报价/.test(hzEmptyHtml)],
    ['无 undefined', !/undefined/.test(hzHtml) && !/undefined/.test(hzEmptyHtml)],
    ['无 NaN', !/NaN/.test(hzHtml)],
];

console.log('\n期权策略矩阵：');
hzChecks.forEach(([n, v]) => { console.log(`  ${v ? '✓' : '✗'} ${n}`); if (!v) ok = false; });


// ---------------- 市场热点板块 ----------------
const hotSample = {
    ok: true, cached: false, as_of: '2026-09-12 09:48:11', window: 5, mode: 'full',
    elapsed: 3.67,
    counts: { sectors: 38, stocks: 172 },
    price_breadth: { up: 145, down: 64, up_ratio: 69.0, label: '涨跌家数' },
    flow_breadth: { inflow: 8, outflow: 30, inflow_ratio: 21.1,
                    tone: 'risk_off', label: '资金整体撤离' },
    note: '流入榜中有 2 个板块净额仍为负，说明市场整体偏流出，它们是相对最抗跌的一批。',
    sectors: {
        active: [
            { symbol: 'SMH', name: '半导体', kind: 'theme', desc: '芯片全产业链',
              price: 568.53, change_pct: 2.88, net_flow: -69686035.24,
              turnover: 18783615931.54, flow_ratio: -0.37, vol_ratio: 0.84,
              act_score: 72.5, turnover_share: 11.2, source: 'history' },
            { symbol: 'XLK', name: '科技', kind: 'industry', desc: '半导体 + 软件',
              price: 187.67, change_pct: 0.91, net_flow: -931974670.39,
              turnover: 5923023614.83, flow_ratio: -15.73, vol_ratio: 1.02,
              act_score: 37.2, turnover_share: 5.1, source: 'history' },
        ],
        inflow: [
            { symbol: 'SOXX', name: '半导体(ICE)', kind: 'theme', desc: '',
              price: 527.07, change_pct: 4.95, net_flow: 3674874004.36,
              turnover: 19320377132.25, flow_ratio: 19.02, vol_ratio: 0.75, act_score: 71.2 },
            { symbol: 'KRE', name: '区域银行', kind: 'theme', desc: '',
              price: 73.9, change_pct: -1.3, net_flow: 254127322.94,
              turnover: 3997309243.56, flow_ratio: 6.36, vol_ratio: 0.87, act_score: 25.9 },
        ],
        outflow: [
            { symbol: 'XLV', name: '医疗', kind: 'industry', desc: '',
              price: 165.36, change_pct: -4.56, net_flow: -4986462164.04,
              turnover: 6619771650.11, flow_ratio: -75.33, vol_ratio: 0.88, act_score: 34.7 },
            { symbol: 'GDX', name: '金矿', kind: 'theme', desc: '',
              price: 97.1, change_pct: -4.33, net_flow: -3123661088.9,
              turnover: 8367908404.63, flow_ratio: -37.33, vol_ratio: 0.62, act_score: 31.5 },
        ],
        strength_inflow: [], strength_outflow: [],
    },
    stocks: {
        inflow: [
            { symbol: 'AMD', name: '超微半导体', industry: '科技', price: 516.13,
              change_pct: 13.15, net_flow: 18095511389.18, turnover: 53066614142.06,
              flow_ratio: 34.1, vol_ratio: 2.1, act_score: 88.0 },
            { symbol: 'AAPL', name: '苹果', industry: '科技', price: 332.27,
              change_pct: 1.24, net_flow: 15228772649.14, turnover: 84305916762.17,
              flow_ratio: 18.06, vol_ratio: 1.13, act_score: 95.0 },
        ],
        outflow: [
            { symbol: 'NVDA', name: '英伟达', industry: '科技', price: 218.29,
              change_pct: -4.45, net_flow: -87223699670.98, turnover: 120027813688.99,
              flow_ratio: -72.67, vol_ratio: 0.61, act_score: 99.0 },
            { symbol: 'MU', name: '美光', industry: '科技', price: 975.26,
              change_pct: 1.78, net_flow: -5515575355.38, turnover: 133759172520.41,
              flow_ratio: -4.12, vol_ratio: 1.05, act_score: 96.0 },
        ],
    },
    industry_agg: [
        { industry: '通信服务', net_flow: 2460553686, turnover: 154676570318,
          count: 16, up: 3, down: 13, breadth: 18.8, etf: 'XLC' },
        { industry: '科技', net_flow: -93888570511, turnover: 857263248719,
          count: 41, up: 21, down: 20, breadth: 51.2, etf: 'XLK' },
    ],
    method: { model: 'Chaikin A/D（累积派发线）' },
};

try {
    renderHotspots(hotSample);
} catch (e) {
    console.error('❌ renderHotspots 抛异常:', e.message);
    ok = false;
}
// 先读取正常渲染结果，再验证异常输入（异常渲染会覆盖同一批节点）
const hotStats = node('#hotStats').innerHTML;
const hotActive = node('#hotActive').innerHTML;
const hotNote = node('#hotNote').innerHTML;
const hotSecIn = node('#hotSecIn').innerHTML;
const hotSecOut = node('#hotSecOut').innerHTML;
const hotStkIn = node('#hotStkIn').innerHTML;
const hotStkOut = node('#hotStkOut').innerHTML;
const hotInd = node('#hotIndustry').innerHTML;

let hotErr = '';
try {
    renderHotspots({ ok: false, error: '热点数据获取失败：网络异常' });
    hotErr = node('#hotActive').innerHTML;
    renderHotspots(null);
    hotErr += node('#hotActive').innerHTML;
} catch (e) {
    console.error('❌ renderHotspots 异常输入抛错:', e.message);
    ok = false;
}
const hotChecks = [
    ['概览-市场情绪', /资金整体撤离/.test(hotStats) && /8 个板块流入/.test(hotStats)],
    ['概览-涨跌家数', /69%/.test(hotStats) && /涨 145/.test(hotStats)],
    ['概览-统计范围', /38 板块 \/ 172 股/.test(hotStats) && /近 5 日/.test(hotStats)],
    ['风险提示', /说明市场整体偏流出/.test(hotNote)],
    ['活跃榜-排名', /hot-rank">1</.test(hotActive) && /hot-rank">2</.test(hotActive)],
    ['活跃榜-标的', /SMH/.test(hotActive) && /半导体/.test(hotActive)],
    ['活跃榜-行业标签', /tag accent">行业/.test(hotActive) && /tag purple">主题/.test(hotActive)],
    ['活跃榜-指标', /活跃度/.test(hotActive) && /成交额/.test(hotActive) && /量比/.test(hotActive)],
    ['活跃榜-金额亿', /187.84亿/.test(hotActive)],
    ['活跃榜-占比', /11.2%/.test(hotActive)],
    ['板块流入卡', /SOXX/.test(hotSecIn) && /\+\$36\.75亿/.test(hotSecIn)],
    ['板块流入-强度', /19.02%/.test(hotSecIn)],
    ['板块流出卡', /XLV/.test(hotSecOut) && /-\$49\.86亿/.test(hotSecOut)],
    ['板块流出-负强度', /-75.33%/.test(hotSecOut)],
    ['个股流入卡', /AMD/.test(hotStkIn) && /\+\$180\.96亿/.test(hotStkIn)],
    ['个股流入-行业标签', /tag neutral">科技/.test(hotStkIn)],
    ['个股流出卡', /NVDA/.test(hotStkOut) && /-\$872\.24亿/.test(hotStkOut)],
    ['行业聚合-名称', /通信服务/.test(hotInd) && /科技/.test(hotInd)],
    ['行业聚合-双向条', /hot-agg-mid/.test(hotInd) && /left:50%/.test(hotInd) && /right:50%/.test(hotInd)],
    ['行业聚合-金额', /\+\$24\.61亿/.test(hotInd) && /-\$938\.89亿/.test(hotInd)],
    ['错误态不崩溃', /热点数据获取失败/.test(hotErr)],
    ['活跃榜可点击', /hot-row clickable/.test(hotActive) && /openDetail\('SMH'\)/.test(hotActive)],
    ['板块卡可点击', /hot-card in clickable/.test(hotSecIn) && /openDetail\('SOXX'\)/.test(hotSecIn)],
    ['板块流出卡可点击', /hot-card out clickable/.test(hotSecOut) && /openDetail\('XLV'\)/.test(hotSecOut)],
    ['个股流入卡可点击', /hot-card in clickable/.test(hotStkIn) && /openDetail\('AMD'\)/.test(hotStkIn)],
    ['个股流出卡可点击', /hot-card out clickable/.test(hotStkOut) && /openDetail\('NVDA'\)/.test(hotStkOut)],
    ['加入股票池按钮', /hot-add/.test(hotStkIn) && /stopPropagation/.test(hotStkIn)],
    ['按钮阻止冒泡', /event\.stopPropagation\(\);addToPool\('AMD'\)/.test(hotStkIn)],
    ['行业聚合含ETF标签', /hot-agg-etf">XLK/.test(hotInd)],
    ['行业聚合可跳转', /hot-agg-row clickable/.test(hotInd) && /openDetail\('XLK'\)/.test(hotInd)],
    ['无 undefined', !/undefined/.test(hotStats + hotActive + hotSecIn + hotStkIn + hotInd)],
    ['无 NaN', !/NaN/.test(hotStats + hotActive + hotSecIn + hotSecOut + hotStkIn + hotInd)],
];

console.log('\n市场热点板块：');
hotChecks.forEach(([n, v]) => { console.log(`  ${v ? '✓' : '✗'} ${n}`); if (!v) ok = false; });

// ---------------- 加载总览（回归：sres is not defined 修复） ----------------
(async () => {
    const dashChecks = [];
    let dashOk = true;
    try {
        // 1) 正常情况：scan 有数据（mock fetch 返回 items+date）
        await loadDashboard();
        dashChecks.push(['loadDashboard 有数据不抛错', true]);
        dashChecks.push(['stats 卡渲染成功', /股票池/.test(node('#stats').innerHTML)
            && /今日信号/.test(node('#stats').innerHTML)]);
        dashChecks.push(['stratStats 渲染成功', /超跌反弹/.test(node('#stratStats').innerHTML)]);

        // 2) scan 无数据：sres.date 应安全 fallback '—'，不报 ReferenceError
        const origFetch = sandbox.fetch;
        sandbox.fetch = (p) => {
            if (String(p).includes('scan/results')) {
                return Promise.resolve({ ok: true, json: () => ({ items: [] }) });
            }
            if (String(p).includes('watchlist')) {
                return Promise.resolve({ ok: true, json: () => ({ items: [] }) });
            }
            return Promise.resolve({ ok: true, json: () => ({}) });
        };
        await loadDashboard();
        sandbox.fetch = origFetch;
        dashChecks.push(['无扫描结果时不报 sres 未定义', /—/.test(node('#stats').innerHTML)]);
    } catch (e) {
        console.error('❌ loadDashboard 抛异常:', e.message);
        dashOk = false;
    }
    console.log('\n总览加载（回归）：');
    dashChecks.forEach(([n, v]) => { console.log(`  ${v ? '✓' : '✗'} ${n}`); if (!v) ok = false; });

    console.log(ok ? '\n✅ 全部通过' : '\n❌ 存在失败项');
    process.exit(ok ? 0 : 1);
})();

const mktChecks = [
    ['港股识别-5位数字', isHK('00700') && isHK('09988')],
    ['美股识别-字母', !isHK('AAPL') && !isHK('NVDA')],
    ['mktOf 分派', mktOf('00700') === 'HK' && mktOf('AAPL') === 'US'],
    ['展示代码-港股补HK', symDisp('00700') === '00700.HK'],
    ['展示代码-美股原样', symDisp('aapl') === 'AAPL'],
    ['角标-港股', /sym-hk">HK</.test(symBadge('00700'))],
    ['角标-美股', /sym-us">US</.test(symBadge('AAPL'))],
    ['货币-港币', moneyM(428.4, '00700') === 'HK$428.40'],
    ['货币-美元', moneyM(332.27, 'AAPL') === '$332.27'],
    ['货币-空值', moneyM(null, '00700') === '—'],
    ['MKT 配置完整', MKT.US.cur === '$' && MKT.HK.cur === 'HK$'
        && MKT.HK.quick.includes('00700') && MKT.US.quick.includes('AAPL')],
];

let mktOk = true;
try {
    STATE.market = 'US';
    applyMarket();
    mktChecks.push(['applyMarket 不抛错', true]);
    mktChecks.push(['美股提示写入', /字母代码/.test(node('#wlMarketHint').innerHTML)]);
    mktChecks.push(['美股扫描标签', /美股/.test(node('#scanMarketTag').textContent || '')]);

    // 切到港股：UI 应整体切换
    setMarket('HK');
    mktChecks.push(['切到港股状态', STATE.market === 'HK']);
    mktChecks.push(['港股提示写入', /5 位数字/.test(node('#wlMarketHint').innerHTML)
        && /港股通标的/.test(node('#wlMarketHint').innerHTML)]);
    mktChecks.push(['港股扫描标签', /港股/.test(node('#scanMarketTag').textContent || '')]);
    mktChecks.push(['港股扫描说明', /港股通池/.test(node('#scanMarketDesc').innerHTML)]);
    mktChecks.push(['股票池筛选同步', STATE.wlFilter === 'HK']);

    // 切回美股
    setMarket('US');
    mktChecks.push(['切回美股状态', STATE.market === 'US']);
    mktChecks.push(['美股扫描说明', /美股池/.test(node('#scanMarketDesc').innerHTML)]);
} catch (e) {
    console.error('❌ 市场切换抛异常:', e.message);
    mktOk = false;
}

console.log('\n市场区分（美股 / 港股通）：');
mktChecks.forEach(([n, v]) => { console.log(`  ${v ? '✓' : '✗'} ${n}`); if (!v) { ok = false; mktOk = false; } });

// ---------------- 缠论精髓面板 ----------------
// 样例结构取自真实接口 /api/symbol/{symbol} -> essence 字段
const essenceSample = {
    ok: true,
    verdict: '下跌走势已完成；当前无明确买卖点（下跌趋势背驰成立）',
    trend_type: { type: '下跌', code: 'down', center_count: 5,
        desc: '最近两个中枢依次降低', basis: '下跌 = 至少两个依次向下的同级别中枢。',
        center: { zg: 476.8, zd: 430.2 } },
    perfection: { completed: true, certainty: '高',
        evidence: '下跌走势中出现趋势背驰（底背驰），力度衰竭',
        expectation: '必然至少回到最后一个中枢（上沿 ZG = 476.8）',
        note: '走势必完美保证的是走势必须完成，不是目标价。' },
    divergence: { kind: '趋势背驰', side: 'bull', ratio: 0.31,
        guarantee: '必然至少回到最后一个中枢（476.8）',
        implication: '趋势背驰：可下注。', tradeable: true },
    center_evolution: { current: '新生', desc: '新中枢在更低位置形成',
        meaning: '趋势延续，顺势持仓为主', trend_upgrade: false, stroke_span: 3 },
    nested: { available: true, alignment: '中性',
        higher: { level: '周线', type: '盘整', trend: 'down' },
        lower: { level: '日线', type: '下跌', trend: 'oscillate' },
        desc: '周线盘整，日线下跌——两者中性。', note: '区间套的近似实现' },
    advices: [{
        code: 'S1', side: 'sell', name: '一类卖点（顶背驰）', date: '2026-09-18',
        price: 224.76, stop: 231.5, confidence: 0.78, certainty: '中',
        essence_score: 53.7, nested_alignment: '共振',
        invalidate: '升破该卖点即证伪',
        action: '一类卖点 = 趋势顶部转折。持股者应减仓。',
        reasons: ['**几何**：上涨中枢[176.76,183.30]后向上离开段出现顶背驰',
            '**走势必完美**：必然至少回到最后一个中枢（176.76）。',
            '**证伪与止损**：升破该卖点即证伪；参考止损 231.5。'],
    }],
    watchpoints: [{
        tag: '下跌趋势背驰成立', tone: 'buy',
        desc: '下跌走势已按走势必完美完成：必然至少回到最后一个中枢（476.8）。',
        wait: ['回调不创新低 → 形成**二类买点**', '突破中枢不跌回 ZG → 形成**三类买点**'],
        basis: '《缠论原理.md》3.2',
    }],
};

const essChecks = [
    ['mdB 渲染粗体', mdB('**几何**：abc') === '<b>几何</b>：abc'],
    ['mdB 先转义再渲染', mdB('<script>x</script>').includes('&lt;script&gt;')],
];
try {
    renderEssence({ essence: essenceSample });
    const h = node('#essenceBody').innerHTML;
    essChecks.push(['精髓面板渲染', h.length > 200]);
    essChecks.push(['结论条渲染', /下跌走势已完成/.test(h)]);
    essChecks.push(['走势类型卡', /走势类型/.test(h) && /下跌/.test(h)]);
    essChecks.push(['走势必完美卡', /走势已完成/.test(h) && /确定性 高/.test(h)]);
    essChecks.push(['背驰性质卡', /趋势背驰/.test(h) && /必然至少回到最后一个中枢/.test(h)]);
    essChecks.push(['中枢演化卡', /新生/.test(h)]);
    essChecks.push(['区间套块', /区间套/.test(h) && /周线/.test(h)]);
    essChecks.push(['买卖点提示卡', /S1/.test(h) && /一类卖点/.test(h)]);
    essChecks.push(['观察提示渲染', /下跌趋势背驰成立/.test(h) && /二类买点/.test(h)]);
    essChecks.push(['理由逐条渲染', (h.match(/<li>/g) || []).length >= 5]);
    essChecks.push(['理由粗体生效', /<b>走势必完美<\/b>/.test(h)]);
    essChecks.push(['证伪与止损', /升破该卖点即证伪/.test(h)]);
    essChecks.push(['无 undefined', !/undefined/.test(h)]);
    essChecks.push(['无 NaN', !/NaN/.test(h)]);

    // 空态不应抛错
    renderEssence({});
    essChecks.push(['空数据不抛错', /暂无精髓分析/.test(node('#essenceBody').innerHTML)]);
} catch (e) {
    console.error('❌ renderEssence 抛异常:', e.message);
    ok = false;
}
console.log('\n缠论精髓面板：');
essChecks.forEach(([n, v]) => { console.log(`  ${v ? '✓' : '✗'} ${n}`); if (!v) ok = false; });

// ---------------- 多周期（30m / 1h / 4h / day / week） ----------------
const lvChecks = [];
function lvCheck(name, cond) { lvChecks.push([name, !!cond]); }

lvCheck('级别清单五档', JSON.stringify(LEVELS) === JSON.stringify(['30m', '1h', '4h', 'day', 'week']));
lvCheck('级别中文名 30分钟', lvLabel('30m') === '30分钟');
lvCheck('级别中文名 1小时', lvLabel('1h') === '1小时');
lvCheck('级别中文名 4小时', lvLabel('4h') === '4小时');
lvCheck('级别中文名 日线', lvLabel('day') === '日线');
lvCheck('级别中文名 周线', lvLabel('week') === '周线');
lvCheck('未知级别回落日线', lvLabel('nope') === '日线');

// 用带 warning 的样本验证数据提示条
const lvSample = {
    ok: true, symbol: 'AAPL', market: 'US', name: '苹果', date: '2026-09-22',
    price: 339.01, change_pct: 0.42, level: '4h', level_label: '4小时',
    warning: '4小时仅识别出 2 笔 / 0 中枢，结构不完整，走势类型与买卖点不可靠',
    indicators: {}, chan: { trend: 'up' }, essence: null, hits: [],
    klines: [{ i: 0, d: '2026-09-22 04:00', o: 1, h: 2, l: 0.5, c: 1.5, v: 10, ma20: null, ma60: null }],
};
let lvHtml = '';
try {
    renderDetail(lvSample);
    lvHtml = node('#detailBody').innerHTML;
    lvCheck('renderDetail 不抛错', lvHtml.length > 0);
    lvCheck('数据提示条渲染', lvHtml.includes('数据提示'));
    lvCheck('提示内容含结构不完整', lvHtml.includes('结构不完整'));
    lvCheck('级别标签显示 4小时级别', node('#detailTitle').innerHTML.includes('4小时级别'));
    lvCheck('五档切换 chip 齐全',
        ['30分钟', '1小时', '4小时', '日线', '周线'].every(x => lvHtml.includes('>' + x + '</span>')));
    lvCheck('无 undefined', !lvHtml.includes('undefined'));
    lvCheck('无 NaN', !lvHtml.includes('NaN'));
} catch (e) {
    console.error('❌ renderDetail 抛异常:', e.message);
    lvCheck('renderDetail 不抛错', false);
}

// 无 warning 时不该出现提示条
const lvSample2 = Object.assign({}, lvSample, { warning: null, level: 'day' });
try {
    renderDetail(lvSample2);
    const h2 = node('#detailBody').innerHTML;
    lvCheck('无警告时不渲染提示条', !h2.includes('数据提示'));
    lvCheck('日线级别标签', node('#detailTitle').innerHTML.includes('日线级别'));
} catch (e) {
    lvCheck('无警告时不渲染提示条', false);
}

// 区间套标题随级别动态显示
const lvSample3 = Object.assign({}, lvSample, {
    warning: null, level: '30m',
    essence: {
        ok: true, verdict: '测试', trend_type: { type: '上涨' },
        perfection: { completed: true, certainty: '高', expectation: '必然回中枢', note: '' },
        divergence: { kind: '趋势背驰', implication: '力度衰竭' },
        center_evolution: { current: '新生', meaning: '趋势延续' },
        nested: { available: true, higher: { level: '1小时', type: '上涨', trend: 'up' },
                  lower: { level: '30分钟', type: '上涨', trend: 'up' },
                  alignment: '共振', score_delta: 12, desc: '两者共振。', note: '近似' },
        advices: [], watchpoints: [],
    },
});
try {
    renderDetail(lvSample3);
    const h3 = node('#essenceBody').innerHTML;
    lvCheck('区间套标题随级别动态', h3.includes('区间套（1小时 × 30分钟）'));
    lvCheck('区间套不再写死周线日线', !h3.includes('区间套（周线 × 日线）'));
    lvCheck('30分钟级别标签', node('#detailTitle').innerHTML.includes('30分钟级别'));
} catch (e) {
    console.error('❌ 区间套动态标题测试异常:', e.message);
    lvCheck('区间套标题随级别动态', false);
}

console.log('\n多周期（30m/1h/4h/day/week）：');
lvChecks.forEach(([n, v]) => { console.log(`  ${v ? '✓' : '✗'} ${n}`); if (!v) ok = false; });

