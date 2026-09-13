/* 美股缠论交易系统 - 前端逻辑 */
'use strict';

// ---------------------------------------------------------------- 工具
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

const STATE = {
    page: 'dashboard',
    market: 'US',          // US / HK —— 全局市场入口
    watchlist: [],
    wlFilter: '',          // 股票池内的市场筛选：'' / US / HK
    scanResults: [],
    detail: null,
    detailSymbol: '',
    chartBars: 120,
    showStrokes: true,
    showCenters: true,
    showSignals: true,
    showMA: true,
    strategyFilter: 'all',
    pollTimer: null,
    opts: null,
};

function fmt(v, d = 2) {
    if (v === null || v === undefined || Number.isNaN(v)) return '—';
    return Number(v).toFixed(d);
}
function fmtPct(v, d = 2) {
    if (v === null || v === undefined || Number.isNaN(v)) return '—';
    const n = Number(v);
    return (n >= 0 ? '+' : '') + n.toFixed(d) + '%';
}
function cls(v) { return Number(v) >= 0 ? 'up' : 'down'; }
function esc(s) {
    return String(s === null || s === undefined ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}
function money(v) {
    if (v === null || v === undefined || Number.isNaN(v)) return '—';
    return '$' + Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// ---------------------------------------------------------------- 市场（美股 / 港股通）
const MKT = {
    US: {
        name: '美股', flag: '🇺🇸', cur: '$', code: 'USD', suffix: '',
        placeholder: '例如 AAPL, NVDA, TSLA',
        quick: ['AAPL', 'NVDA', 'TSLA', 'MSFT', 'SPY', 'QQQ'],
        hint: '<b>代码规则</b>：字母代码，如 <code>AAPL</code>。<br>'
            + '<b>交易时段</b>：美东 09:30–16:00（夏令时对应北京时间 21:30–04:00）。<br>'
            + '<b>数据源</b>：新浪财经（含 52 周高低 / PE / 市值）。',
    },
    HK: {
        name: '港股', flag: '🇭🇰', cur: 'HK$', code: 'HKD', suffix: '.HK',
        placeholder: '例如 00700, 09988, 01810',
        quick: ['00700', '09988', '03690', '01810', '00005', '01299'],
        hint: '<b>代码规则</b>：5 位数字，如 <code>00700</code>（腾讯）；'
            + '输入 <code>700</code> / <code>0700.HK</code> 会自动补齐。<br>'
            + '<b>交易时段</b>：香港 09:30–12:00 / 13:00–16:00，T+0、T+2 交收。<br>'
            + '<b>数据源</b>：腾讯日K（前复权，最长 4 年）+ 新浪实时报价。'
            + '已内置 <b>106 只主流港股通标的</b>，也支持自行添加任意港股代码。',
    },
};

/** 判断代码所属市场：5 位数字 = 港股 */
function isHK(sym) { return /^\d{5}$/.test(String(sym || '').trim()); }
function mktOf(sym) { return isHK(sym) ? 'HK' : 'US'; }

/** 展示用代码：港股补 .HK */
function symDisp(sym) {
    const s = String(sym || '').trim();
    if (!s) return '—';
    return isHK(s) ? s + '.HK' : s.toUpperCase();
}

/** 带市场角标的代码 HTML */
function symBadge(sym) {
    const hk = isHK(sym);
    return esc(symDisp(sym)) + `<span class="${hk ? 'sym-hk' : 'sym-us'}">${hk ? 'HK' : 'US'}</span>`;
}

/** 按市场货币符号格式化金额 */
function moneyM(v, sym) {
    if (v === null || v === undefined || Number.isNaN(v)) return '—';
    const cur = MKT[mktOf(sym)].cur;
    return cur + Number(v).toLocaleString('en-US',
        { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** 切换全局市场入口 */
function setMarket(m) {
    const mkt = (m === 'HK') ? 'HK' : 'US';
    if (STATE.market === mkt) return;
    STATE.market = mkt;
    STATE.wlFilter = mkt;
    applyMarket();
    // 已经在相关页面则立即刷新
    if (STATE.page === 'watchlist') loadWatchlist();
    if (STATE.page === 'hot') loadHotspots(true);
}

/** 把当前市场状态同步到各页面 UI */
function applyMarket() {
    const m = STATE.market;
    const meta = MKT[m];
    $$('#mktSwitch .mkt-tab').forEach(t =>
        t.classList.toggle('on', t.dataset.market === m));

    const inp = $('#newSymbol');
    if (inp) inp.placeholder = meta.placeholder;
    const tag = $('#wlMarketTag');
    if (tag) { tag.textContent = meta.flag + ' ' + meta.name; }
    const hint = $('#wlMarketHint');
    if (hint) hint.innerHTML = meta.hint;

    const stag = $('#scanMarketTag');
    if (stag) stag.textContent = meta.flag + ' ' + meta.name;
    const sdesc = $('#scanMarketDesc');
    if (sdesc) {
        sdesc.innerHTML = m === 'HK'
            ? '按「超跌反弹 / 趋势突破 / 底部反转」三类策略扫描内置<b>港股通池</b>'
              + '（约 106 只主流标的，覆盖互联网 / 金融 / 医药 / 消费 / 能源 / 地产），'
              + '并用缠论结构验证买卖点。扫描结果可直接加入股票池。'
            : '按「超跌反弹 / 趋势突破 / 底部反转」三类策略扫描内置美股池'
              + '（约 200 只主流标的与 ETF），并用缠论结构验证买卖点。扫描结果可直接加入股票池。';
    }
    const shint = $('#scanMarketHint');
    if (shint) shint.innerHTML = meta.hint;

    const htag = $('#hotMarketTag');
    if (htag) htag.textContent = meta.flag + ' ' + meta.name;
    const hdesc = $('#hotDesc');
    if (hdesc) {
        hdesc.innerHTML = m === 'HK'
            ? '追踪 <b>9 个港股 ETF</b>（盈富基金 / 恒生科技 / 沪深三百等）与 <b>106 只主流港股通标的</b>，'
              + '用 <b>Chaikin A/D 累积派发线</b> 估算资金流向，找出资金最集中、流入最猛、撤离最快的地方。'
            : '追踪 <b>38 个板块 ETF</b>（11 大 GICS 行业 + 主题板块）与 <b>约 170 只主流美股</b>，'
              + '用 <b>Chaikin A/D 累积派发线</b> 估算资金流向，找出资金最集中、流入最猛、撤离最快的地方。';
    }

    // 快速添加按钮
    const qa = $('#quickAdd');
    if (qa) {
        qa.innerHTML = '';
        meta.quick.forEach(s => {
            const b = document.createElement('span');
            b.className = 'chip';
            b.textContent = s;
            b.onclick = async () => {
                try {
                    await api('/api/watchlist', {
                        method: 'POST',
                        body: JSON.stringify({ symbol: s, market: m }),
                    });
                    toast('已添加 ' + symDisp(s), 'ok');
                    loadWatchlist();
                } catch (e) { toast('添加失败: ' + e.message, 'err'); }
            };
            qa.appendChild(b);
        });
    }
}

function toast(msg, kind = 'ok', ms = 3200) {
    const wrap = $('#toastWrap');
    const el = document.createElement('div');
    el.className = 'toast ' + kind;
    el.textContent = msg;
    wrap.appendChild(el);
    setTimeout(() => el.remove(), ms);
}

async function api(path, options = {}) {
    const res = await fetch(path, {
        headers: { 'Content-Type': 'application/json' },
        ...options,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.ok === false) {
        throw new Error(data.error || data.detail || ('请求失败 ' + res.status));
    }
    return data;
}

function scoreColor(s) {
    if (s >= 75) return '#26a69a';
    if (s >= 60) return '#f5a623';
    return '#6e7b8d';
}

// ---------------------------------------------------------------- K线渲染
function renderChart(el, klines, chan, opts = {}) {
    if (!klines || !klines.length) {
        el.innerHTML = '<div class="empty"><div class="big">📉</div><div class="msg">无K线数据</div></div>';
        return;
    }

    const W = Math.max(el.clientWidth || 900, 420);
    const padL = 10, padR = 66, padT = 16, padB = 24;
    const volH = 78, axisGap = 10;
    const H = (opts.height || 430);
    const plotW = W - padL - padR;
    const plotH = H - padT - padB - volH - axisGap;
    const volTop = padT + plotH + axisGap;

    const n = klines.length;
    const step = plotW / n;
    const bw = Math.max(1.4, Math.min(step * 0.68, 16));

    // ---- 价格范围
    let minP = Infinity, maxP = -Infinity;
    klines.forEach(k => {
        if (k.l < minP) minP = k.l;
        if (k.h > maxP) maxP = k.h;
    });
    if (STATE.showCenters && chan.centers) {
        chan.centers.forEach(c => {
            if (c.end_raw >= 0 && c.start_raw < n) {
                minP = Math.min(minP, c.zd);
                maxP = Math.max(maxP, c.zg);
            }
        });
    }
    const pad = (maxP - minP) * 0.06 || 1;
    minP -= pad; maxP += pad;

    const X = i => padL + (i + 0.5) * step;
    const Y = p => padT + (maxP - p) / (maxP - minP) * plotH;

    let maxVol = 0;
    klines.forEach(k => { if (k.v > maxVol) maxVol = k.v; });
    const YV = v => volTop + volH - (maxVol ? v / maxVol * volH : 0);

    const parts = [];
    parts.push(`<svg class="chart-svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" xmlns="http://www.w3.org/2000/svg">`);

    // defs
    parts.push(`<defs>
      <clipPath id="clipPlot"><rect x="${padL}" y="${padT}" width="${plotW}" height="${plotH}"/></clipPath>
    </defs>`);

    // 背景网格 + 价格轴
    parts.push(`<rect x="${padL}" y="${padT}" width="${plotW}" height="${plotH}" fill="#10151e"/>`);
    const gridN = 5;
    for (let i = 0; i <= gridN; i++) {
        const p = minP + (maxP - minP) * i / gridN;
        const y = Y(p);
        parts.push(`<line x1="${padL}" y1="${y}" x2="${padL + plotW}" y2="${y}" stroke="#1e2735" stroke-width="1"/>`);
        parts.push(`<text x="${padL + plotW + 7}" y="${y + 3.5}" fill="#6e7b8d" font-size="10.5" font-family="monospace">${p.toFixed(2)}</text>`);
    }

    // ---- 中枢（矩形）
    if (STATE.showCenters && chan.centers) {
        chan.centers.forEach(c => {
            const s = Math.max(0, c.start_raw), e = Math.min(n - 1, c.end_raw);
            if (e < 0 || s > n - 1 || e <= s) return;
            const x1 = X(s) - step * 0.5, x2 = X(e) + step * 0.5;
            const y1 = Y(c.zg), y2 = Y(c.zd);
            const fill = c.direction === 1 ? 'rgba(38,166,154,0.13)' : 'rgba(239,83,80,0.13)';
            const stroke = c.direction === 1 ? 'rgba(38,166,154,0.55)' : 'rgba(239,83,80,0.55)';
            parts.push(`<rect x="${x1}" y="${y1}" width="${Math.max(x2 - x1, 2)}" height="${Math.max(y2 - y1, 1)}"
                fill="${fill}" stroke="${stroke}" stroke-width="1" stroke-dasharray="3,2" rx="2"/>`);
            if (x2 - x1 > 46) {
                parts.push(`<text x="${x1 + 4}" y="${y1 + 11}" fill="#8b95a7" font-size="9.5" font-family="monospace">中枢 ${c.zd.toFixed(1)}~${c.zg.toFixed(1)}</text>`);
            }
        });
    }

    // ---- 成交量
    klines.forEach((k, i) => {
        const up = k.c >= k.o;
        parts.push(`<rect x="${X(i) - bw / 2}" y="${YV(k.v)}" width="${bw}" height="${Math.max(volTop + volH - YV(k.v), 0.6)}"
            fill="${up ? 'rgba(38,166,154,0.42)' : 'rgba(239,83,80,0.42)'}"/>`);
    });

    // ---- 蜡烛
    klines.forEach((k, i) => {
        const up = k.c >= k.o;
        const col = up ? '#26a69a' : '#ef5350';
        const x = X(i);
        const yo = Y(k.o), yc = Y(k.c);
        const top = Math.min(yo, yc), h = Math.max(Math.abs(yc - yo), 1.2);
        parts.push(`<line x1="${x}" y1="${Y(k.h)}" x2="${x}" y2="${Y(k.l)}" stroke="${col}" stroke-width="1"/>`);
        parts.push(`<rect x="${x - bw / 2}" y="${top}" width="${bw}" height="${h}" fill="${col}" stroke="${col}" stroke-width="0.6"/>`);
    });

    // ---- 均线
    if (STATE.showMA) {
        [['ma20', '#f5a623'], ['ma60', '#a78bfa']].forEach(([key, color]) => {
            let d = '';
            let started = false;
            klines.forEach((k, i) => {
                const v = k[key];
                if (v === null || v === undefined || Number.isNaN(v)) { started = false; return; }
                if (!started) { d += `M ${X(i)} ${Y(v)} `; started = true; }
                else d += `L ${X(i)} ${Y(v)} `;
            });
            if (d) parts.push(`<path d="${d}" fill="none" stroke="${color}" stroke-width="1.3" opacity="0.85" clip-path="url(#clipPlot)"/>`);
        });
    }

    // ---- 缠论笔
    if (STATE.showStrokes && chan.strokes) {
        chan.strokes.forEach(s => {
            const a = Math.max(0, s.start_raw), b = Math.min(n - 1, s.end_raw);
            if (b < 0 || a > n - 1 || b <= a) return;
            // 笔的端点价格：方向向上则 start=low, end=high
            const p1 = s.start_price, p2 = s.end_price;
            const col = s.direction === 1 ? '#4fd1c5' : '#ff8a80';
            parts.push(`<line x1="${X(a)}" y1="${Y(p1)}" x2="${X(b)}" y2="${Y(p2)}"
                stroke="${col}" stroke-width="1.5" opacity="0.9" stroke-linecap="round"/>`);
        });
    }

    // ---- 分型标记（小圆点）
    if (chan.fractals) {
        chan.fractals.forEach(f => {
            if (f.raw_idx < 0 || f.raw_idx >= n) return;
            const y = Y(f.price);
            const col = f.kind === 'top' ? '#ef5350' : '#26a69a';
            parts.push(`<circle cx="${X(f.raw_idx)}" cy="${y}" r="2.6" fill="none" stroke="${col}" stroke-width="1.1" opacity="0.85"/>`);
        });
    }

    // ---- 买卖点
    if (STATE.showSignals && chan.signals) {
        chan.signals.forEach(s => {
            if (s.raw_idx < 0 || s.raw_idx >= n) return;
            const x = X(s.raw_idx);
            if (s.side === 'buy') {
                const y = Y(s.price) + 16;
                parts.push(`<polygon points="${x},${y - 9} ${x - 6},${y + 1} ${x + 6},${y + 1}" fill="#26a69a" stroke="#0b0e14" stroke-width="0.8"/>`);
                parts.push(`<text x="${x}" y="${y + 13}" fill="#26a69a" font-size="10.5" font-weight="700" text-anchor="middle" font-family="monospace">${s.code}</text>`);
            } else {
                const y = Y(s.price) - 16;
                parts.push(`<polygon points="${x},${y + 9} ${x - 6},${y - 1} ${x + 6},${y - 1}" fill="#ef5350" stroke="#0b0e14" stroke-width="0.8"/>`);
                parts.push(`<text x="${x}" y="${y - 4}" fill="#ef5350" font-size="10.5" font-weight="700" text-anchor="middle" font-family="monospace">${s.code}</text>`);
            }
        });
    }

    // ---- 当前价线
    const last = klines[n - 1];
    const ly = Y(last.c);
    parts.push(`<line x1="${padL}" y1="${ly}" x2="${padL + plotW}" y2="${ly}" stroke="#4f8cff" stroke-width="1" stroke-dasharray="4,3" opacity="0.8"/>`);
    parts.push(`<rect x="${padL + plotW + 2}" y="${ly - 8}" width="58" height="16" fill="#4f8cff" rx="2"/>`);
    parts.push(`<text x="${padL + plotW + 7}" y="${ly + 4}" fill="#fff" font-size="10.5" font-family="monospace" font-weight="600">${last.c.toFixed(2)}</text>`);

    // ---- 日期轴
    const labelEvery = Math.max(1, Math.floor(n / 8));
    klines.forEach((k, i) => {
        if (i % labelEvery === 0 && i < n - 1) {
            parts.push(`<text x="${X(i)}" y="${H - 7}" fill="#6e7b8d" font-size="10" text-anchor="middle" font-family="monospace">${k.d.slice(5)}</text>`);
        }
    });

    // ---- 十字光标层
    parts.push(`<g id="crosshair" style="display:none">
        <line id="chX" y1="${padT}" y2="${volTop + volH}" stroke="#8b95a7" stroke-width="0.8" stroke-dasharray="3,3"/>
        <line id="chY" x1="${padL}" x2="${padL + plotW}" stroke="#8b95a7" stroke-width="0.8" stroke-dasharray="3,3"/>
    </g>`);
    parts.push(`<rect id="hitArea" x="${padL}" y="${padT}" width="${plotW}" height="${plotH + volH + axisGap}" fill="transparent"/>`);

    parts.push('</svg>');
    el.innerHTML = parts.join('');

    // 交互
    const svg = $('svg', el);
    const tip = $('#chartTip');
    const hit = $('#hitArea', el);
    const cross = $('#crosshair', el);
    const chX = $('#chX', el), chY = $('#chY', el);

    hit.addEventListener('mousemove', ev => {
        const rect = svg.getBoundingClientRect();
        const mx = (ev.clientX - rect.left) * (W / rect.width);
        let i = Math.floor((mx - padL) / step);
        i = Math.max(0, Math.min(n - 1, i));
        const k = klines[i];
        cross.style.display = '';
        chX.setAttribute('x1', X(i)); chX.setAttribute('x2', X(i));
        chY.setAttribute('y1', Y(k.c)); chY.setAttribute('y2', Y(k.c));

        const chg = ((k.c / klines[Math.max(0, i - 1)].c - 1) * 100);
        tip.style.display = 'block';
        tip.innerHTML = `
            <div class="row"><span class="k">日期</span><span>${k.d}</span></div>
            <div class="row"><span class="k">开</span><span>${fmt(k.o)}</span></div>
            <div class="row"><span class="k">高</span><span>${fmt(k.h)}</span></div>
            <div class="row"><span class="k">低</span><span>${fmt(k.l)}</span></div>
            <div class="row"><span class="k">收</span><span class="${cls(k.c - k.o)}">${fmt(k.c)}</span></div>
            <div class="row"><span class="k">涨跌</span><span class="${cls(chg)}">${fmtPct(chg)}</span></div>
            <div class="row"><span class="k">量</span><span>${(k.v / 1e6).toFixed(2)}M</span></div>`;
        const px = X(i) * (rect.width / W);
        tip.style.left = Math.min(px + 16, rect.width - 150) + 'px';
        tip.style.top = '14px';
    });
    hit.addEventListener('mouseleave', () => {
        cross.style.display = 'none';
        tip.style.display = 'none';
    });
}

// ---------------------------------------------------------------- 页面：总览
async function loadDashboard() {
    // 用 let 在函数作用域声明，try 块里赋值（const 是块作用域，外面读不到）
    let wres, sres;
    try {
        [wres, sres] = await Promise.all([
            api('/api/watchlist'),
            api('/api/scan/results'),
        ]);
        STATE.watchlist = wres.items || [];
        STATE.scanResults = sres.items || [];
    } catch (e) {
        toast('加载数据失败: ' + e.message, 'err');
        return;
    }

    const items = STATE.scanResults;
    const buys = items.filter(r => (r.signals || []).some(s => s.side === 'buy')).length;
    const sells = items.filter(r => (r.signals || []).some(s => s.side === 'sell')).length;
    const byStrat = {};
    items.forEach(r => { byStrat[r.strategy] = (byStrat[r.strategy] || 0) + 1; });

    $('#stats').innerHTML = `
        <div class="stat"><div class="stat-label">股票池</div><div class="stat-value">${STATE.watchlist.filter(w => w.enabled).length}</div>
            <div class="stat-sub">共 ${STATE.watchlist.length} 个标的</div></div>
        <div class="stat"><div class="stat-label">今日信号</div><div class="stat-value">${items.length}</div>
            <div class="stat-sub">${sres.date || '—'}</div></div>
        <div class="stat"><div class="stat-label">买点</div><div class="stat-value up">${buys}</div>
            <div class="stat-sub">含一/二/三类买点</div></div>
        <div class="stat"><div class="stat-label">卖点</div><div class="stat-value down">${sells}</div>
            <div class="stat-sub">含一/二/三类卖点</div></div>`;

    // 按策略分组的统计
    const meta = { oversold: '超跌反弹', breakout: '趋势突破', reversal: '底部反转' };
    const colors = { oversold: '#4f8cff', breakout: '#ef5350', reversal: '#26a69a' };
    $('#stratStats').innerHTML = Object.keys(meta).map(k => `
        <div class="stat" style="border-left:3px solid ${colors[k]}">
            <div class="stat-label">${meta[k]}</div>
            <div class="stat-value" style="color:${colors[k]}">${byStrat[k] || 0}</div>
            <div class="stat-sub">命中候选</div>
        </div>`).join('');

    renderSignalTable(items);
}

function renderSignalTable(items) {
    const box = $('#signalTable');
    let list = items;
    if (STATE.strategyFilter !== 'all') {
        list = list.filter(r => r.strategy === STATE.strategyFilter);
    }
    if (!list.length) {
        box.innerHTML = `<div class="empty"><div class="big">🔍</div>
            <div class="msg">暂无信号</div>
            <div class="hint">点击右上角「扫描股票池」立即分析，或等待每日自动扫描</div></div>`;
        return;
    }
    const rows = list.map(r => {
        const sigs = r.signals || [];
        const sigHtml = sigs.length
            ? sigs.slice(0, 3).map(s => `<span class="tag ${s.side}">${s.code}</span>`).join(' ')
            : '<span class="muted">—</span>';
        return `<tr>
            <td><span class="sym-link" onclick="openDetail('${r.symbol}')">${r.symbol}</span>
                <div class="muted" style="font-size:11.5px">${esc(r.name || '')}</div></td>
            <td><span class="tag" style="background:rgba(79,140,255,.14);color:#4f8cff">${r.strategy_name || r.strategy}</span></td>
            <td class="num">${money(r.price)}</td>
            <td class="num ${cls(r.change_pct)}">${fmtPct(r.change_pct)}</td>
            <td><span class="num" style="color:${scoreColor(r.score)};font-weight:700">${fmt(r.score, 0)}</span>
                <span class="score-bar"><span class="score-fill" style="width:${Math.min(r.score, 100)}%;background:${scoreColor(r.score)}"></span></span></td>
            <td>${sigHtml}</td>
            <td style="max-width:340px"><div style="font-size:12px;color:#a9b4c2;line-height:1.5">${esc((r.reasons || [])[0] || '—')}</div></td>
            <td><button class="btn sm" onclick="openDetail('${r.symbol}')">详情</button>
                <button class="btn sm" onclick="openOptions('${r.symbol}')">期权</button></td>
        </tr>`;
    }).join('');

    box.innerHTML = `<div class="table-wrap"><table>
        <thead><tr><th>标的</th><th>策略</th><th>价格</th><th>涨跌</th><th>评分</th><th>缠论信号</th><th>核心逻辑</th><th>操作</th></tr></thead>
        <tbody>${rows}</tbody></table></div>`;
}

// ---------------------------------------------------------------- 页面：股票池
async function loadWatchlist() {
    const qs = STATE.wlFilter ? ('?market=' + STATE.wlFilter) : '';
    const res = await api('/api/watchlist' + qs);
    STATE.watchlist = res.items || [];
    const cnt = $('#wlCount');
    if (cnt) cnt.textContent = (res.items || []).length + ' 只';
    renderWatchlist();

    // 有持仓的标的异步拉取报价，回填盈亏
    const withPos = STATE.watchlist
        .filter(w => Number(w.position_qty || 0) > 0)
        .map(w => w.symbol);
    if (withPos.length) {
        try {
            const q = await api('/api/quotes?symbols=' + withPos.join(','));
            STATE.quotes = {};
            (q.quotes || []).forEach(x => { if (x.symbol) STATE.quotes[x.symbol] = x; });
            renderWatchlist();
        } catch (e) { /* 报价失败不影响主流程 */ }
    }
}

function renderWatchlist() {
    const box = $('#watchTable');
    if (!STATE.watchlist.length) {
        const hintTxt = STATE.wlFilter === 'HK'
            ? '切换到上方「🇭🇰 港股通」入口，添加如 00700（腾讯）、09988（阿里）'
            : STATE.wlFilter === 'US'
                ? '添加你关注的美股代码，如 AAPL、NVDA'
                : '添加你关注的代码，美股如 AAPL，港股如 00700';
        box.innerHTML = `<div class="empty"><div class="big">📋</div>
            <div class="msg">股票池是空的</div>
            <div class="hint">${hintTxt}，系统会每日自动分析买卖点</div></div>`;
        return;
    }
    const rows = STATE.watchlist.map(w => {
        const pos = Number(w.position_qty || 0);
        const cost = Number(w.position_cost || 0);
        const q = (STATE.quotes || {})[w.symbol];
        const price = q && q.price ? Number(q.price) : null;
        let pnl = '<span class="muted">—</span>';
        if (pos > 0 && cost > 0 && price) {
            const abs = (price - cost) * pos;
            const pct = (price / cost - 1) * 100;
            pnl = `<span class="num ${cls(pct)}" style="font-weight:650">${fmtPct(pct)}</span>
                   <div class="num ${cls(abs)}" style="font-size:11.5px">${abs >= 0 ? '+' : ''}${money(abs)}</div>`;
        } else if (pos > 0 && cost > 0) {
            pnl = '<span class="muted">持仓中</span>';
        }
        return `<tr>
            <td><span class="sym-link" onclick="openDetail('${w.symbol}')">${symBadge(w.symbol)}</span></td>
            <td>${esc(w.name || '—')}<div class="muted" style="font-size:11px">${esc(w.sector || '')}</div></td>
            <td class="num">${pos ? pos : '—'}</td>
            <td class="num">${cost ? moneyM(cost, w.symbol) : '—'}</td>
            <td class="num">${price ? moneyM(price, w.symbol) : '—'}</td>
            <td>${w.tags ? `<span class="tag neutral">${esc(w.tags)}</span>` : '—'}</td>
            <td><span class="tag ${w.enabled ? 'buy' : 'neutral'}">${w.enabled ? '监控中' : '已停用'}</span></td>
            <td>${pnl}</td>
            <td style="white-space:nowrap">
                <button class="btn sm" onclick="openDetail('${w.symbol}')">分析</button>
                <button class="btn sm" onclick="editPosition('${w.symbol}')">持仓</button>
                <button class="btn sm danger" onclick="removeSymbol('${w.symbol}')">删除</button>
            </td>
        </tr>`;
    }).join('');
    box.innerHTML = `<div class="table-wrap"><table>
        <thead><tr><th>代码</th><th>名称</th><th>持仓股数</th><th>成本价</th><th>现价</th><th>标签</th><th>状态</th><th>盈亏</th><th>操作</th></tr></thead>
        <tbody>${rows}</tbody></table></div>`;
}

async function addSymbol() {
    const input = $('#newSymbol');
    const raw = (input.value || '').trim().toUpperCase();
    if (!raw) return;
    const syms = raw.split(/[,\s]+/).filter(Boolean);
    input.value = '';
    const tag = $('#newTag') ? $('#newTag').value : '';
    for (const s of syms) {
        try {
            // 市场按代码形态自动判断，但显式带上更符合当前入口（港股代码会被补齐为 5 位）
            const r = await api('/api/watchlist', {
                method: 'POST',
                body: JSON.stringify({ symbol: s, tags: tag, market: STATE.market }),
            });
            toast(`已添加 ${symDisp(r.symbol || s)}${r.name ? ' · ' + r.name : ''}`, 'ok');
        } catch (e) {
            toast(`添加 ${s} 失败: ${e.message}`, 'err');
        }
    }
    await loadWatchlist();
}

async function removeSymbol(sym) {
    if (!confirm(`确定从股票池移除 ${sym}？`)) return;
    await api('/api/watchlist/' + sym, { method: 'DELETE' });
    toast('已移除 ' + sym);
    await loadWatchlist();
}

function editPosition(sym) {
    const w = STATE.watchlist.find(x => x.symbol === sym) || {};
    $('#posSymbol').textContent = sym;
    $('#posQty').value = w.position_qty || '';
    $('#posCost').value = w.position_cost || '';
    $('#posTarget').value = w.target_price || '';
    $('#posStop').value = w.stop_price || '';
    $('#posModal').classList.add('show');
}

async function savePosition() {
    const sym = $('#posSymbol').textContent;
    const body = {
        position_qty: parseFloat($('#posQty').value) || 0,
        position_cost: parseFloat($('#posCost').value) || null,
        target_price: parseFloat($('#posTarget').value) || null,
        stop_price: parseFloat($('#posStop').value) || null,
    };
    await api('/api/watchlist/' + sym, { method: 'PATCH', body: JSON.stringify(body) });
    $('#posModal').classList.remove('show');
    toast('持仓信息已保存');
    await loadWatchlist();
}

// ---------------------------------------------------------------- 扫描
async function runScan(scanMarket = false) {
    const btn = scanMarket ? $('#btnMarketScan') : $('#btnScan');
    const status = $('#scanStatus');
    btn.disabled = true;
    status.innerHTML = '<div style="display:flex;align-items:center;gap:9px"><span class="spinner"></span> 正在扫描，首次获取行情需要一些时间…</div><div class="progress"><div class="progress-fill" style="width:0%"></div></div>';

    try {
        const path = scanMarket ? '/api/scan/market' : '/api/scan/run';
        const body = scanMarket
            ? { limit: parseInt($('#marketLimit')?.value || '120', 10), market: STATE.market }
            : { market: STATE.market };
        const res = await api(path, { method: 'POST', body: JSON.stringify(body) });
        toast(`扫描完成：分析 ${res.scanned || res.total} 只，命中 ${res.hit_count} 条信号`, 'ok', 4200);
        if (scanMarket) {
            STATE.marketHits = res.hits || [];
            renderMarketHits();
        } else {
            await loadDashboard();
        }
        status.innerHTML = `<div class="dim">上次扫描：${new Date().toLocaleString('zh-CN')} · 分析 ${res.scanned || 0} 只 · 命中 ${res.hit_count || 0} 条</div>`;
    } catch (e) {
        toast('扫描失败: ' + e.message, 'err', 5000);
        status.innerHTML = `<div class="down">扫描失败：${esc(e.message)}</div>`;
    } finally {
        btn.disabled = false;
    }

    // 轮询进度
    clearInterval(STATE.pollTimer);
    STATE.pollTimer = setInterval(async () => {
        try {
            const st = await api('/api/scan/status');
            if (!st.running) { clearInterval(STATE.pollTimer); return; }
            const pct = st.total ? Math.round(st.progress / st.total * 100) : 0;
            status.innerHTML = `<div style="display:flex;align-items:center;gap:9px"><span class="spinner"></span> 扫描中 ${st.progress}/${st.total} · ${esc(st.current)}</div>
                <div class="progress"><div class="progress-fill" style="width:${pct}%"></div></div>`;
        } catch (e) { clearInterval(STATE.pollTimer); }
    }, 1500);
}

function renderMarketHits() {
    const box = $('#marketResults');
    const hits = STATE.marketHits || [];
    if (!hits.length) {
        box.innerHTML = `<div class="empty"><div class="big">🌐</div><div class="msg">未发现符合条件的标的</div>
            <div class="hint">可扩大扫描范围，或调整策略阈值</div></div>`;
        return;
    }
    const rows = hits.slice(0, 60).map(r => `<tr>
        <td><span class="sym-link" onclick="openDetail('${r.symbol}')">${symBadge(r.symbol)}</span>
            <div class="muted" style="font-size:11.5px">${esc(r.name || '')}</div></td>
        <td><span class="tag" style="background:rgba(79,140,255,.14);color:#4f8cff">${r.strategy_name}</span></td>
        <td class="num">${moneyM(r.price, r.symbol)}</td>
        <td class="num ${cls(r.change_pct)}">${fmtPct(r.change_pct)}</td>
        <td><span class="num" style="color:${scoreColor(r.score)};font-weight:700">${fmt(r.score, 0)}</span></td>
        <td style="max-width:300px;font-size:12px;color:#a9b4c2">${esc((r.reasons || [])[0] || '—')}</td>
        <td><button class="btn sm primary" onclick="addToPool('${r.symbol}')">加入股票池</button></td>
    </tr>`).join('');
    box.innerHTML = `<div class="table-wrap"><table>
        <thead><tr><th>标的</th><th>策略</th><th>价格</th><th>涨跌</th><th>评分</th><th>核心逻辑</th><th>操作</th></tr></thead>
        <tbody>${rows}</tbody></table></div>`;
}

async function addToPool(sym) {
    try {
        await api('/api/watchlist', {
            method: 'POST',
            body: JSON.stringify({ symbol: sym, tags: '扫描', market: mktOf(sym) }),
        });
        toast('已加入股票池: ' + sym, 'ok');
        await loadWatchlist();
    } catch (e) { toast('添加失败: ' + e.message, 'err'); }
}

// ---------------------------------------------------------------- 详情页
async function openDetail(sym, level) {
    go('detail');
    const lv = level || STATE.detailLevel || 'day';
    const box = $('#detailBody');
    box.innerHTML = `<div class="empty"><span class="spinner"></span><div class="msg" style="margin-top:12px">正在分析 ${sym}（${lv === 'week' ? '周线' : '日线'}）…</div></div>`;
    STATE.detailSymbol = sym;
    STATE.detailLevel = lv;

    try {
        const res = await api('/api/symbol/' + sym + (lv === 'week' ? '?level=week' : ''));
        STATE.detail = res;
        renderDetail(res);
        loadFundamentals(sym);          // 不阻塞 K 线，基本面异步加载
    } catch (e) {
        box.innerHTML = `<div class="empty"><div class="big">⚠️</div><div class="msg">${esc(e.message)}</div>
            <div class="hint">请检查代码是否正确，或网络连接（yfinance 需要访问雅虎财经）</div></div>`;
    }
}

// ---------------------------------------------------------------- 基本面分析
const FUND_TONE = {
    '便宜': 'var(--up)', '偏贵': 'var(--down)', '中性': 'var(--warn)',
};

async function loadFundamentals(sym) {
    const panel = $('#fundPanel');
    if (!panel) return;
    try {
        const res = await api('/api/symbol/' + sym + '/fundamentals');
        renderFundamentals(res, sym);
    } catch (e) {
        panel.innerHTML = `<div class="panel-head"><h2>基本面分析</h2></div>
            <div class="panel-body"><div class="muted" style="padding:8px 0">基本面数据获取失败：${esc(e.message)}</div></div>`;
    }
}

/** 市值格式化 */
function mcapFmt(v) {
    if (v === null || v === undefined) return '—';
    const a = Math.abs(Number(v));
    if (a >= 1e12) return (v / 1e12).toFixed(2) + '万亿';
    if (a >= 1e8) return (v / 1e8).toFixed(0) + '亿';
    return Number(v).toLocaleString();
}

/** PE 带箱线：min—p25—median—p75—max + 当前位置 */
function fundBandRow(label, b, unit) {
    if (!b) return '';
    const span = (b.max - b.min) || 1;
    const pos = v => Math.max(0, Math.min(100, (v - b.min) / span * 100));
    const cur = b.current;
    const curPos = pos(cur);
    // 分位颜色：<20% 绿 / >80% 红 / 其余蓝
    const pc = b.percentile;
    const curColor = pc === null || pc === undefined ? 'var(--accent)'
        : pc < 20 ? 'var(--up)' : pc > 80 ? 'var(--down)' : 'var(--accent)';
    return `<div class="fund-band">
        <div class="fund-band-label">${label}</div>
        <div class="fund-band-track">
            <div class="fund-band-box" style="left:${pos(b.p25)}%;width:${pos(b.p75) - pos(b.p25)}%"></div>
            <div class="fund-band-mid" style="left:${pos(b.median)}%"></div>
            <div class="fund-band-cur" style="left:${curPos}%;background:${curColor}"
                 title="当前 ${cur}${unit}（${pc}% 分位）"></div>
        </div>
        <div class="fund-band-vals">
            <span>${fmt(b.min, 1)}</span><span>${fmt(b.p25, 1)}</span>
            <span>${fmt(b.median, 1)}</span><span>${fmt(b.p75, 1)}</span><span>${fmt(b.max, 1)}</span>
        </div>
        <div class="fund-band-pct ${pc < 20 ? 'up' : pc > 80 ? 'down' : ''}">${pc === null || pc === undefined ? '—' : pc + '%'}</div>
    </div>`;
}

function renderFundamentals(res, sym) {
    const panel = $('#fundPanel');
    if (!panel) return;
    if (!res || !res.ok) {
        panel.innerHTML = `<div class="panel-head"><h2>基本面分析</h2></div>
            <div class="panel-body"><div class="muted" style="padding:8px 0">${esc((res && res.error) || '暂无数据')}</div></div>`;
        return;
    }

    const s = res.snapshot || {};
    const h = res.history || {};
    const ev = res.events || {};
    const v = res.verdict;
    const cur = s.currency || '$';
    const toneColor = v ? (FUND_TONE[v.tone] || 'var(--accent)') : 'var(--accent)';

    // ---- 估值快照 ----
    const snapCards = [
        ['市盈率 PE', s.pe === null || s.pe === undefined ? '—' : fmt(s.pe, 2), 'TTM · 新浪'],
        ['每股收益 EPS', s.eps === null || s.eps === undefined ? '—' : cur + fmt(s.eps, 2), 'TTM'],
        ['总市值', mcapFmt(s.market_cap), s.market_cap ? cur : '—'],
        ['52 周位置', s.pos_52w === null || s.pos_52w === undefined ? '—' : s.pos_52w + '%',
         s.year_high ? `高 ${moneyM(s.year_high, sym)} / 低 ${moneyM(s.year_low, sym)}` : ''],
    ].map(([l, val, sub]) => `<div class="stat">
        <div class="stat-label">${l}</div>
        <div class="stat-value" style="font-size:19px">${val}</div>
        <div class="stat-sub">${esc(sub || '')}</div></div>`).join('');

    // ---- 历史估值对比 ----
    let histHtml = '';
    if (h && h.ok) {
        const bands = [];
        const peBand = h.pe_band || {};
        if (peBand['3y'] || peBand['1y']) {
            bands.push(`<div class="fund-band-title">PE 带分布 <span class="muted" style="font-weight:400;font-size:11px">（箱线：25%–75%，竖线为中位数，圆点为当前）</span></div>`);
            if (peBand['3y']) bands.push(fundBandRow('近 3 年', peBand['3y'], 'x'));
            if (peBand['1y']) bands.push(fundBandRow('近 1 年', peBand['1y'], 'x'));
        }
        const pp = h.price_pct || {};
        if (pp['3y'] || pp['1y']) {
            bands.push(`<div class="fund-band-title" style="margin-top:14px">价格分位 <span class="muted" style="font-weight:400;font-size:11px">（纯历史价格统计，无假设）</span></div>`);
            for (const [lbl, p] of Object.entries(pp)) {
                const nm = lbl === '1y' ? '近 1 年' : lbl === '3y' ? '近 3 年' : '近 5 年';
                const pc = p.percentile;
                const color = pc < 20 ? 'var(--up)' : pc > 80 ? 'var(--down)' : 'var(--accent)';
                bands.push(`<div class="fund-band">
                    <div class="fund-band-label">${nm}</div>
                    <div class="fund-band-track">
                        <div class="fund-band-fill" style="width:${pc}%;background:${color}22"></div>
                        <div class="fund-band-cur" style="left:${pc}%;background:${color}"></div>
                    </div>
                    <div class="fund-band-vals" style="grid-template-columns:1fr 1fr">
                        <span>低 ${moneyM(p.min, sym)}</span><span style="text-align:right">高 ${moneyM(p.max, sym)}</span>
                    </div>
                    <div class="fund-band-pct ${pc < 20 ? 'up' : pc > 80 ? 'down' : ''}">${pc}%</div>
                </div>`);
            }
        }
        if (h.pe_assumption) {
            bands.push(`<div class="fund-assume">※ ${esc(h.pe_assumption)}</div>`);
        }
        histHtml = bands.join('') || '<div class="muted">历史数据不足</div>';
    } else {
        histHtml = `<div class="muted">${esc(h.error || '历史数据不足')}</div>`;
    }

    // ---- 重要事件 ----
    let evHtml = '';
    if (ev && ev.ok) {
        const sm = ev.summary || {};
        const corp = ev.corporate_actions || [];
        const volEv = ev.volatility_events || [];
        let corpHtml = '';
        if (sm.buyback_note) {
            corpHtml += `<div class="fund-ev-sum"><span class="tag buy">回购</span>
                <b>${sm.buyback_days}</b> 个交易日 · ${esc(sm.buyback_note.replace(/^近 \d+ 个月披露回购 \d+ 个交易日，?/, ''))}</div>`;
        }
        if (sm.dividend_days) {
            corpHtml += `<div class="fund-ev-sum"><span class="tag accent">分红</span>
                <b>${sm.dividend_days}</b> 个披露日</div>`;
        }
        if (corp.length) {
            corpHtml += `<div class="fund-ev-list">${corp.slice(-6).reverse().map(c => `
                <div class="fund-ev-row"><span class="fund-ev-date">${c.date.slice(5)}</span>
                    <span class="tag ${c.type === '回购' ? 'buy' : 'accent'}">${c.type}</span>
                    <span class="fund-ev-detail">${esc(c.detail)}</span></div>`).join('')}</div>`;
        }
        if (!corpHtml) {
            corpHtml = `<div class="muted" style="font-size:12px">${esc(ev.corp_source || '无公司行动披露')}</div>`;
        }
        const volHtml = volEv.length
            ? `<div class="fund-ev-list">${volEv.map(e => `
                <div class="fund-ev-row"><span class="fund-ev-date">${e.date.slice(5)}</span>
                    <span class="tag ${e.chg_pct >= 0 ? 'buy' : 'sell'}">${e.kind}</span>
                    <span class="num ${e.chg_pct >= 0 ? 'up' : 'down'}">${fmtPct(e.chg_pct)}</span>
                    <span class="fund-ev-detail">量比 ${e.vol_ratio} · 收 ${moneyM(e.close, sym)}</span></div>`).join('')}</div>`
            : '<div class="muted" style="font-size:12px">近一年无单日涨跌超 6% 或量比超 3 的异动</div>';
        evHtml = `<div class="grid grid-2">
            <div><div class="fund-ev-title">公司行动（真实披露）</div>${corpHtml}</div>
            <div><div class="fund-ev-title">异常波动日（近一年）</div>${volHtml}</div>
        </div>
        <div class="fund-assume">※ 公司行动来自${esc(ev.corp_source || '公开披露')}；异动日 = 单日涨跌超 6% 或量比超 3 倍</div>`;
    } else {
        evHtml = `<div class="muted">${esc(ev.error || '事件数据不足')}</div>`;
    }

    // ---- 评级 ----
    const verdictHtml = v ? `
        <div class="fund-verdict">
            <div class="fund-verdict-grade" style="color:${toneColor}">${esc(v.grade)}</div>
            <div class="fund-verdict-meta">依据：${esc(v.basis)} · 当前处于 <b style="color:${toneColor}">${v.percentile}%</b> 分位</div>
            <div class="fund-verdict-caveat">⚠ ${esc(v.caveat)}</div>
        </div>` : '';

    panel.innerHTML = `
        <div class="panel-head">
            <h2>基本面分析</h2>
            <span class="tag neutral">估值 · 历史对比 · 事件</span>
            <div class="spacer"></div>
            <span class="muted" style="font-size:11.5px">覆盖 ${h.years_covered || '—'} 年历史${res.cached ? ' · 缓存' : ''}</span>
        </div>
        <div class="panel-body">
            ${verdictHtml}
            <div class="grid grid-4" style="margin:14px 0 18px">${snapCards}</div>
            <div class="fund-sec-title">历史估值对比</div>
            ${histHtml}
            <div class="fund-sec-title" style="margin-top:20px">重要事件</div>
            ${evHtml}
        </div>`;
}

function renderDetail(d) {
    const lv = d.level || 'day';
    const mkt = d.market || mktOf(d.symbol);
    $('#detailTitle').innerHTML = `${esc(symDisp(d.symbol))} <span class="muted" style="font-size:14px;font-weight:400">${esc(d.name)}</span>
        <span class="${isHK(d.symbol) ? 'sym-hk' : 'sym-us'}" style="font-size:11px">${MKT[mkt].flag} ${esc(MKT[mkt].name)}</span>
        <span class="tag ${lv === 'week' ? 'accent' : 'neutral'}" style="margin-left:8px">${lv === 'week' ? '周线级别' : '日线级别'}</span>`;
    const ind = d.indicators || {};
    const chan = d.chan || {};

    const trendTxt = { up: '上涨', down: '下跌', oscillate: '震荡' }[chan.trend] || '—';
    const trendCls = { up: 'up', down: 'down', oscillate: 'neutral' }[chan.trend] || 'neutral';

    const box = $('#detailBody');
    box.innerHTML = `
    <div class="panel">
        <div class="panel-head">
            <h2>行情概览</h2>
            <span class="tag ${trendCls}">缠论结构：${trendTxt}</span>
            <div class="spacer"></div>
            <span class="muted" style="font-size:12px">数据日期 ${esc(d.date)}</span>
        </div>
        <div class="panel-body">
            <div class="grid grid-4" style="margin-bottom:16px">
                <div class="stat"><div class="stat-label">最新价（${MKT[mkt].code}）</div>
                    <div class="stat-value ${cls(d.change_pct)}">${moneyM(d.price, d.symbol)}</div>
                    <div class="stat-sub ${cls(d.change_pct)}">${fmtPct(d.change_pct)}</div></div>
                <div class="stat"><div class="stat-label">RSI(14)</div>
                    <div class="stat-value">${fmt(ind.rsi, 1)}</div>
                    <div class="stat-sub">${ind.rsi < 30 ? '超卖区' : ind.rsi > 70 ? '超买区' : '中性区'}</div></div>
                <div class="stat"><div class="stat-label">距52周高点</div>
                    <div class="stat-value ${cls(ind.dd_52w)}">${fmtPct((ind.dd_52w || 0) * 100, 1)}</div>
                    <div class="stat-sub">回撤幅度</div></div>
                <div class="stat"><div class="stat-label">量比</div>
                    <div class="stat-value">${fmt(ind.volume_ratio, 2)}</div>
                    <div class="stat-sub">相对20日均量</div></div>
            </div>
            <div class="grid grid-3">
                <div>
                    <div class="kv"><span class="k">MA20</span><span class="v">${fmt(ind.ma20)}</span></div>
                    <div class="kv"><span class="k">MA60</span><span class="v">${fmt(ind.ma60)}</span></div>
                    <div class="kv"><span class="k">MACD DIF</span><span class="v">${fmt(ind.macd_dif, 3)}</span></div>
                    <div class="kv"><span class="k">MACD DEA</span><span class="v">${fmt(ind.macd_dea, 3)}</span></div>
                </div>
                <div>
                    <div class="kv"><span class="k">MACD 柱</span><span class="v ${cls(ind.macd_hist)}">${fmt(ind.macd_hist, 3)}</span></div>
                    <div class="kv"><span class="k">布林上轨</span><span class="v">${fmt(ind.boll_up)}</span></div>
                    <div class="kv"><span class="k">布林下轨</span><span class="v">${fmt(ind.boll_low)}</span></div>
                    <div class="kv"><span class="k">ATR(14)</span><span class="v">${fmt(ind.atr)}</span></div>
                </div>
                <div>
                    <div class="kv"><span class="k">ADX</span><span class="v">${fmt(ind.adx, 1)}</span></div>
                    <div class="kv"><span class="k">笔数量</span><span class="v">${chan.stroke_count || 0}</span></div>
                    <div class="kv"><span class="k">中枢数量</span><span class="v">${chan.center_count || 0}</span></div>
                    <div class="kv"><span class="k">最近中枢</span><span class="v">${chan.last_center ? chan.last_center.zd.toFixed(2) + ' ~ ' + chan.last_center.zg.toFixed(2) : '—'}</span></div>
                </div>
            </div>
        </div>
    </div>

    <div class="panel" id="fundPanel">
        <div class="panel-head">
            <h2>基本面分析</h2>
            <span class="tag neutral">估值 · 历史对比 · 事件</span>
            <div class="spacer"></div>
        </div>
        <div class="panel-body"><div class="dim" style="padding:6px 0"><span class="spinner"></span> 正在获取基本面数据…</div></div>
    </div>

    <div class="panel">
        <div class="chart-toolbar">
            <strong style="font-size:13px">K线与缠论结构</strong>
            <span style="width:12px"></span>
            <span class="chip ${lv === 'day' ? 'on' : ''}" onclick="setLevel('day')">日线</span>
            <span class="chip ${lv === 'week' ? 'on' : ''}" onclick="setLevel('week')">周线</span>
            <span style="width:12px"></span>
            <div class="spacer" style="flex:1"></div>
            <span class="chip ${STATE.chartBars === 60 ? 'on' : ''}" onclick="setBars(60)">60</span>
            <span class="chip ${STATE.chartBars === 120 ? 'on' : ''}" onclick="setBars(120)">120</span>
            <span class="chip ${STATE.chartBars === 260 ? 'on' : ''}" onclick="setBars(260)">全部</span>
            <span style="width:10px"></span>
            <span class="chip ${STATE.showStrokes ? 'on' : ''}" onclick="toggleChart('showStrokes')">笔</span>
            <span class="chip ${STATE.showCenters ? 'on' : ''}" onclick="toggleChart('showCenters')">中枢</span>
            <span class="chip ${STATE.showSignals ? 'on' : ''}" onclick="toggleChart('showSignals')">买卖点</span>
            <span class="chip ${STATE.showMA ? 'on' : ''}" onclick="toggleChart('showMA')">均线</span>
        </div>
        <div class="panel-body flush" style="padding:0">
            <div class="chart-container" id="chartBox"><div class="tooltip" id="chartTip"></div></div>
        </div>
        <div class="legend">
            <span><i style="background:#26a69a"></i>上涨 / 底分型</span>
            <span><i style="background:#ef5350"></i>下跌 / 顶分型</span>
            <span><i style="background:#4fd1c5"></i>向上笔</span>
            <span><i style="background:#ff8a80"></i>向下笔</span>
            <span><i style="background:rgba(38,166,154,.4)"></i>上涨中枢</span>
            <span><i style="background:rgba(239,83,80,.4)"></i>下跌中枢</span>
            <span><i style="background:#f5a623"></i>MA20</span>
            <span><i style="background:#a78bfa"></i>MA60</span>
            <span>▲ B1/B2/B3 买点 &nbsp; ▼ S1/S2/S3 卖点</span>
        </div>
    </div>

    <div class="grid grid-2">
        <div class="panel">
            <div class="panel-head"><h2>缠论买卖点</h2></div>
            <div class="panel-body" id="chanSignals"></div>
        </div>
        <div class="panel">
            <div class="panel-head"><h2>策略命中</h2></div>
            <div class="panel-body" id="strategyHits"></div>
        </div>
    </div>

    <div class="panel">
        <div class="panel-head"><h2>缠论结构明细</h2></div>
        <div class="panel-body" id="chanDetail"></div>
    </div>`;

    // 买卖点
    const sigs = chan.signals || [];
    $('#chanSignals').innerHTML = sigs.length ? sigs.map(s => `
        <div class="signal-card ${s.side}">
            <div class="signal-head">
                <span class="signal-code ${s.side === 'buy' ? 'up' : 'down'}">${s.code}</span>
                <strong>${esc(s.name)}</strong>
                <span class="tag ${s.side}">${s.side === 'buy' ? '买点' : '卖点'}</span>
                <div style="flex:1"></div>
                <span class="muted" style="font-size:11.5px">${esc(s.date)}</span>
            </div>
            <div class="signal-reason">${esc(s.reason)}</div>
            <div class="signal-meta">
                <span>信号价 <strong class="num">${money(s.price)}</strong></span>
                <span>止损参考 <strong class="num">${money(s.stop_loss)}</strong></span>
                <span>置信度 <strong>${(s.confidence * 100).toFixed(0)}%</strong></span>
            </div>
        </div>`).join('')
        : `<div class="empty" style="padding:30px"><div class="msg">当前无明确买卖点</div>
            <div class="hint">缠论讲究「不测而测」，无信号即观望</div></div>`;

    // 策略命中
    const hits = d.hits || [];
    $('#strategyHits').innerHTML = hits.length ? hits.map(h => `
        <div class="signal-card" style="border-left-color:${scoreColor(h.score)}">
            <div class="signal-head">
                <strong>${esc(h.strategy_name)}</strong>
                <span class="tag accent">评分 ${fmt(h.score, 0)}</span>
            </div>
            <ul class="reason-list">${(h.reasons || []).map(r => `<li>${esc(r)}</li>`).join('')}</ul>
        </div>`).join('')
        : `<div class="empty" style="padding:30px"><div class="msg">未命中任何策略</div>
            <div class="hint">该标的当前不符合超跌/突破/反转任一条件</div></div>`;

    // 结构明细
    const strokes = (chan.strokes || []).slice(-8).reverse();
    const centers = (chan.centers || []).slice(-4).reverse();
    const divs = (chan.divergences || []).slice(-3).reverse();
    $('#chanDetail').innerHTML = `
        <div class="grid grid-2">
            <div>
                <h4 style="font-size:13px;margin-bottom:9px" class="dim">最近的笔（由新到旧）</h4>
                ${strokes.length ? `<div class="table-wrap"><table><thead><tr><th>方向</th><th>起点</th><th>终点</th><th>幅度</th></tr></thead><tbody>
                ${strokes.map(s => `<tr><td><span class="tag ${s.direction === 1 ? 'buy' : 'sell'}">${s.direction === 1 ? '向上' : '向下'}</span></td>
                    <td class="num">${fmt(s.start_price)}</td><td class="num">${fmt(s.end_price)}</td>
                    <td class="num">${fmtPct((s.end_price / s.start_price - 1) * 100, 1)}</td></tr>`).join('')}
                </tbody></table></div>` : '<div class="muted">—</div>'}
            </div>
            <div>
                <h4 style="font-size:13px;margin-bottom:9px" class="dim">中枢</h4>
                ${centers.length ? `<div class="table-wrap"><table><thead><tr><th>区间</th><th>高度</th><th>性质</th></tr></thead><tbody>
                ${centers.map(c => `<tr><td class="num">${fmt(c.zd)} ~ ${fmt(c.zg)}</td>
                    <td class="num">${fmt(c.zg - c.zd)}</td>
                    <td><span class="tag ${c.direction === 1 ? 'buy' : 'sell'}">${c.direction === 1 ? '上涨中枢' : '下跌中枢'}</span></td></tr>`).join('')}
                </tbody></table></div>` : '<div class="muted">—</div>'}
            </div>
        </div>
        ${divs.length ? `<div class="hr"></div><h4 style="font-size:13px;margin-bottom:9px" class="dim">背驰记录</h4>
        <ul class="reason-list">${divs.map(dv => `<li>${dv.kind === 'bull' ? '底背驰' : '顶背驰'}：前段力度 ${fmt(dv.prev_power, 3)} → 当前段 ${fmt(dv.curr_power, 3)}（比值 ${fmt(dv.ratio, 2)}）</li>`).join('')}</ul>` : ''}`;

    drawChart();
}

function drawChart() {
    const d = STATE.detail;
    if (!d) return;
    const box = $('#chartBox');
    if (!box) return;
    const klines = (d.klines || []).slice(-STATE.chartBars);
    const chan = d.chan || {};
    // 相对偏移：切片后索引需要再平移
    const off = (d.klines || []).length - klines.length;
    const shifted = {
        ...chan,
        strokes: (chan.strokes || []).map(s => ({ ...s, start_raw: s.start_raw - off, end_raw: s.end_raw - off })),
        centers: (chan.centers || []).map(c => ({ ...c, start_raw: c.start_raw - off, end_raw: c.end_raw - off })),
        fractals: (chan.fractals || []).map(f => ({ ...f, raw_idx: f.raw_idx - off })),
        signals: (chan.signals || []).map(s => ({ ...s, raw_idx: s.raw_idx - off })),
    };
    box.innerHTML = '<div class="tooltip" id="chartTip"></div>';
    const holder = document.createElement('div');
    box.appendChild(holder);
    renderChart(holder, klines, shifted, { height: 430 });
}

function setBars(n) { STATE.chartBars = n; renderDetail(STATE.detail); }
function toggleChart(key) { STATE[key] = !STATE[key]; renderDetail(STATE.detail); }
function setLevel(lv) {
    if (lv === (STATE.detail?.level || 'day')) return;
    openDetail(STATE.detailSymbol, lv);
}

// ---------------------------------------------------------------- 权益曲线
function drawEquityCurve(el, equity, height = 230) {
    if (!equity || equity.length < 2) {
        el.innerHTML = '<div class="muted" style="padding:20px">无权益曲线数据</div>';
        return;
    }
    const W = Math.max(el.clientWidth || 900, 420);
    const H = height;
    const padL = 10, padR = 62, padT = 14, padB = 24;
    const plotW = W - padL - padR, plotH = H - padT - padB;

    const minV = Math.min(...equity), maxV = Math.max(...equity);
    const range = (maxV - minV) || 1;
    const X = i => padL + i / (equity.length - 1) * plotW;
    const Y = v => padT + (maxV - v) / range * plotH;

    const final = equity[equity.length - 1];
    const up = final >= 1;
    const col = up ? '#26a69a' : '#ef5350';

    let d = '', area = '';
    equity.forEach((v, i) => {
        d += (i === 0 ? 'M ' : 'L ') + X(i) + ' ' + Y(v) + ' ';
    });
    area = `M ${X(0)} ${Y(equity[0])} ` + d.slice(2) +
           ` L ${X(equity.length - 1)} ${padT + plotH} L ${X(0)} ${padT + plotH} Z`;

    const p = [];
    p.push(`<svg class="chart-svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">`);
    p.push(`<rect x="${padL}" y="${padT}" width="${plotW}" height="${plotH}" fill="#10151e"/>`);

    // 网格与刻度
    for (let i = 0; i <= 4; i++) {
        const v = minV + range * i / 4;
        const y = Y(v);
        p.push(`<line x1="${padL}" y1="${y}" x2="${padL + plotW}" y2="${y}" stroke="#1e2735" stroke-width="1"/>`);
        p.push(`<text x="${padL + plotW + 7}" y="${y + 3.5}" fill="#6e7b8d" font-size="10.5" font-family="monospace">${v.toFixed(3)}</text>`);
    }
    // 基准线 1.0
    if (minV <= 1 && maxV >= 1) {
        const y = Y(1);
        p.push(`<line x1="${padL}" y1="${y}" x2="${padL + plotW}" y2="${y}" stroke="#8b95a7" stroke-width="1" stroke-dasharray="4,3"/>`);
        p.push(`<text x="${padL + 4}" y="${y - 5}" fill="#8b95a7" font-size="10">本金 1.0</text>`);
    }

    p.push(`<path d="${area}" fill="${up ? 'rgba(38,166,154,0.13)' : 'rgba(239,83,80,0.13)'}"/>`);
    p.push(`<path d="${d}" fill="none" stroke="${col}" stroke-width="1.8" stroke-linejoin="round"/>`);
    p.push(`<circle cx="${X(equity.length - 1)}" cy="${Y(final)}" r="3.5" fill="${col}"/>`);
    p.push(`<text x="${X(equity.length - 1) - 6}" y="${Y(final) - 10}" fill="${col}" font-size="11" text-anchor="end" font-family="monospace" font-weight="700">${final.toFixed(3)}</text>`);
    p.push(`<text x="${padL}" y="${H - 7}" fill="#6e7b8d" font-size="10">按成交顺序（资金分散于并发持仓）</text>`);
    p.push('</svg>');

    el.innerHTML = p.join('');
}


// ---------------------------------------------------------------- 策略回测
async function runBacktest() {
    const btn = $('#btnBacktest');
    const box = $('#btResult');
    btn.disabled = true;
    box.innerHTML = `<div class="empty"><span class="spinner"></span><div class="msg" style="margin-top:12px">正在回测…</div></div>`;

    const symsRaw = ($('#btSymbols').value || '').trim();
    const body = {
        symbols: symsRaw ? symsRaw.split(/[,\s]+/).filter(Boolean) : null,
        period: $('#btPeriod').value,
        chan: {
            min_stroke_bars: parseInt($('#btStroke').value, 10) || 4,
            power_ratio: parseFloat($('#btPower').value) || 1.0,
        },
        exit: {
            stop_pct: (parseFloat($('#btStop').value) || 6) / 100,
            take_pct: (parseFloat($('#btTake').value) || 12) / 100,
            max_hold: parseInt($('#btHold').value, 10) || 20,
            use_chan_stop: $('#btChanStop').checked,
            exit_on_reverse: $('#btReverse').checked,
        },
        mode: $('#btMode').value,
        entry_from_ratio: parseFloat($('#btOos').value) || 0,
    };

    try {
        const res = await api('/api/backtest', { method: 'POST', body: JSON.stringify(body) });
        renderBacktest(res);
    } catch (e) {
        box.innerHTML = `<div class="empty"><div class="big">⚠️</div><div class="msg">${esc(e.message)}</div></div>`;
    } finally {
        btn.disabled = false;
    }
}

function renderBacktest(r) {
    const box = $('#btResult');
    if (!r.ok) {
        box.innerHTML = `<div class="empty"><div class="big">⚠️</div><div class="msg">${esc(r.error || '回测失败')}</div></div>`;
        return;
    }
    const s = r.stats || {};
    const has = s.n_trades > 0;

    const stat = (label, value, sub, color) => `
        <div class="stat"><div class="stat-label">${label}</div>
            <div class="stat-value" ${color ? `style="color:${color}"` : ''}>${value}</div>
            ${sub ? `<div class="stat-sub">${sub}</div>` : ''}</div>`;

    const winColor = s.win_rate >= 50 ? '#26a69a' : '#ef5350';
    const pnlColor = (s.avg_pnl || 0) >= 0 ? '#26a69a' : '#ef5350';

    let html = `<div class="grid grid-4" style="margin-bottom:16px">
        ${stat('交易笔数', s.n_trades ?? '—', `${r.symbols ? r.symbols.length : 0} 只标的`)}
        ${stat('胜率', s.win_rate !== null ? s.win_rate + '%' : '—', '盈利笔数占比', winColor)}
        ${stat('平均单笔', (s.avg_pnl !== null ? (s.avg_pnl >= 0 ? '+' : '') + s.avg_pnl + '%' : '—'), '期望值', pnlColor)}
        ${stat('盈亏比', s.profit_factor ?? '—', '总盈利/总亏损')}
    </div>
    <div class="grid grid-4" style="margin-bottom:16px">
        ${stat('最大回撤', s.max_drawdown !== null ? s.max_drawdown + '%' : '—', '权益曲线峰值回落')}
        ${stat('夏普比率', s.sharpe ?? '—', '风险调整收益')}
        ${stat('总收益', (s.total_return !== null ? (s.total_return >= 0 ? '+' : '') + s.total_return + '%' : '—'),
               (s.total_return || 0) >= 0 ? '' : '累计', (s.total_return || 0) >= 0 ? '#26a69a' : '#ef5350')}
        ${stat('平均持有', s.avg_bars !== null ? s.avg_bars + ' 根' : '—', `MFE ${s.avg_mfe}% / MAE ${s.avg_mae}%`)}
    </div>`;

    if (!has) {
        box.innerHTML = html + `<div class="empty"><div class="msg">该参数下未产生任何交易</div>
            <div class="hint">可放宽成笔K线数、降低背驰阈值，或增加标的与周期</div></div>`;
        return;
    }

    html += `<div class="panel"><div class="panel-head"><h2>权益曲线</h2>
        <div class="spacer"></div><span class="muted" style="font-size:12px">共 ${s.n_trades} 笔，资金等权分散（并发约 ${r.n_concurrent || 1} 个仓位）</span></div>
        <div class="panel-body" style="padding:12px"><div id="equityBox"></div></div></div>`;

    // 出场原因与信号分布
    const byReason = {}, byCode = {};
    (r.trades || []).forEach(t => {
        byReason[t.exit_reason] = (byReason[t.exit_reason] || 0) + 1;
        byCode[t.code] = (byCode[t.code] || 0) + 1;
    });
    const reasonName = {
        stop_loss: '止损', take_profit: '止盈', max_hold: '超时',
        reverse_signal: '反向信号', trailing_stop: '移动止损', end_of_data: '期末平仓',
    };
    html += `<div class="grid grid-2" style="margin-bottom:16px">
        <div class="panel"><div class="panel-head"><h2>出场原因</h2></div><div class="panel-body">
        ${Object.entries(byReason).sort((a, b) => b[1] - a[1]).map(([k, v]) =>
            `<div class="kv"><span class="k">${reasonName[k] || k}</span><span class="v">${v} 笔 (${(v / s.n_trades * 100).toFixed(0)}%)</span></div>`).join('')}
        </div></div>
        <div class="panel"><div class="panel-head"><h2>信号分布</h2></div><div class="panel-body">
        ${Object.entries(byCode).sort((a, b) => b[1] - a[1]).map(([k, v]) =>
            `<div class="kv"><span class="k">${k} ${k[0] === 'B' ? '买点' : '卖点'}</span><span class="v">${v} 笔</span></div>`).join('')}
        </div></div>
    </div>`;

    // 交易明细
    const rows = (r.trades || []).slice(-60).reverse().map(t => `
        <tr>
            <td>${esc(t.symbol)}</td>
            <td><span class="tag ${t.code[0] === 'B' ? 'buy' : 'sell'}">${t.code}</span></td>
            <td class="muted" style="font-size:11.5px">${esc(t.entry_date)}</td>
            <td class="num">${fmt(t.entry_price)}</td>
            <td class="muted" style="font-size:11.5px">${esc(t.exit_date)}</td>
            <td class="num">${fmt(t.exit_price)}</td>
            <td class="num ${cls(t.pnl_pct)}" style="font-weight:650">${fmtPct(t.pnl_pct)}</td>
            <td><span class="tag ${t.exit_reason === 'take_profit' ? 'buy' : t.exit_reason === 'stop_loss' ? 'sell' : 'neutral'}">${reasonName[t.exit_reason] || t.exit_reason}</span></td>
            <td class="num up">${fmt(t.mfe, 1)}%</td>
            <td class="num down">${fmt(t.mae, 1)}%</td>
            <td class="num">${t.bars_held}</td>
        </tr>`).join('');

    html += `<div class="panel"><div class="panel-head"><h2>交易明细</h2>
        <div class="spacer"></div><span class="muted" style="font-size:12px">最近 ${Math.min((r.trades || []).length, 60)} 笔</span></div>
        <div class="panel-body flush"><div class="table-wrap"><table>
        <thead><tr><th>标的</th><th>信号</th><th>入场日</th><th>入场价</th><th>出场日</th><th>出场价</th>
            <th>盈亏</th><th>出场原因</th><th>MFE</th><th>MAE</th><th>持有</th></tr></thead>
        <tbody>${rows}</tbody></table></div></div></div>`;

    box.innerHTML = html;
    drawEquityCurve($('#equityBox'), r.equity || []);
}


// ---------------------------------------------------------------- 参数自迭代
async function runOptimize() {
    const btn = $('#btnOptimize');
    const box = $('#opResult');
    btn.disabled = true;
    box.innerHTML = '';
    $('#opStatus').innerHTML = '<div style="display:flex;align-items:center;gap:9px"><span class="spinner"></span> 正在加载行情…</div>';

    const body = {
        n_samples: parseInt($('#opSamples').value, 10) || 40,
        objective: $('#opObjective').value,
        split: parseFloat($('#opSplit').value) || 0.7,
        min_trades: parseInt($('#opMinTrades').value, 10) || 25,
        period: '3y',
    };

    try {
        await api('/api/optimize', { method: 'POST', body: JSON.stringify(body) });
        STATE.optTimer = setInterval(pollOptimize, 1200);
    } catch (e) {
        $('#opStatus').innerHTML = `<div class="down">${esc(e.message)}</div>`;
        btn.disabled = false;
    }
}

async function pollOptimize() {
    let st;
    try {
        st = await api('/api/optimize/status');
    } catch (e) {
        clearInterval(STATE.optTimer);
        return;
    }
    const pct = st.total ? Math.round(st.progress / st.total * 100) : 0;
    $('#opStatus').innerHTML = st.running
        ? `<div style="display:flex;align-items:center;gap:9px"><span class="spinner"></span> ${esc(st.message)}</div>
           <div class="progress"><div class="progress-fill" style="width:${pct}%"></div></div>`
        : `<div class="dim">${esc(st.message || '就绪')}</div>`;

    if (!st.running) {
        clearInterval(STATE.optTimer);
        $('#btnOptimize').disabled = false;
        if (st.result) renderOptimize(st.result);
    }
}

function renderOptimize(r) {
    const box = $('#opResult');
    if (!r.ok) {
        box.innerHTML = `<div class="panel"><div class="panel-body"><div class="down">${esc(r.error || '优化失败')}</div></div></div>`;
        return;
    }
    const b = r.best || {}, bl = r.baseline || {};
    const acc = r.accepted;

    const fmtStat = (s, k, suffix = '') => (s && s[k] !== null && s[k] !== undefined) ? s[k] + suffix : '—';

    const cmpRow = (label, key, suffix = '') => `
        <tr><td>${label}</td>
            <td class="num">${fmtStat(b.in_sample, key, suffix)}</td>
            <td class="num">${fmtStat(bl.in_sample, key, suffix)}</td>
            <td class="num ${(b.out_sample?.[key] || 0) >= (bl.out_sample?.[key] || 0) ? 'up' : 'down'}"
                style="font-weight:650">${fmtStat(b.out_sample, key, suffix)}</td>
            <td class="num">${fmtStat(bl.out_sample, key, suffix)}</td></tr>`;

    const paramLabel = {
        min_stroke_bars: '成笔K线数', power_ratio: '背驰阈值',
        stop_pct: '止损', take_pct: '止盈', max_hold: '最大持有',
        use_chan_stop: '缠论关键位止损', exit_on_reverse: '反向信号出场',
    };
    const fmtParam = (k, v) => {
        if (typeof v === 'boolean') return v ? '开启' : '关闭';
        if (k === 'stop_pct' || k === 'take_pct') return (v * 100).toFixed(0) + '%';
        return v;
    };

    const paramRows = Object.keys(paramLabel).map(k => `
        <tr><td>${paramLabel[k]}</td>
            <td class="num" style="color:#4f8cff;font-weight:650">${fmtParam(k, b.params?.[k])}</td>
            <td class="num muted">${fmtParam(k, bl.params?.[k])}</td></tr>`).join('');

    const topRows = (r.top || []).map((t, i) => `
        <tr><td>${i + 1}</td>
            <td class="num" style="font-size:11.5px">${Object.entries(t.params).map(([k, v]) =>
                `${paramLabel[k]}=${fmtParam(k, v)}`).join(', ')}</td>
            <td class="num">${t.in_sample?.n_trades ?? '—'}</td>
            <td class="num">${t.in_sample?.win_rate ?? '—'}%</td>
            <td class="num ${cls(t.in_sample?.avg_pnl || 0)}">${fmtPct(t.in_sample?.avg_pnl)}</td>
            <td class="num">${t.out_sample?.n_trades ?? '—'}</td>
            <td class="num ${cls(t.out_sample?.avg_pnl || 0)}" style="font-weight:650">${fmtPct(t.out_sample?.avg_pnl)}</td>
        </tr>`).join('');

    box.innerHTML = `
    <div class="panel" style="border-left:3px solid ${acc ? '#26a69a' : '#f5a623'}">
        <div class="panel-head"><h2>结论</h2>
            <span class="tag ${acc ? 'buy' : 'warn'}">${acc ? '建议应用' : '保持默认'}</span></div>
        <div class="panel-body">
            <div style="font-size:14px;line-height:1.7;white-space:pre-line">${esc(r.verdict || '')}</div>
            <div class="hr"></div>
            <div class="muted" style="font-size:12px">
                目标函数：${esc(r.objective_name)} · 采样 ${r.n_samples} 组 ·
                样本内/外分割 ${(r.split * 100).toFixed(0)}% / ${(100 - r.split * 100).toFixed(0)}% ·
                最少交易数 ${r.min_trades} · ${(r.symbols || []).length} 只标的
            </div>
            ${acc ? `<div style="margin-top:14px">
                <button class="btn primary" onclick="applyOptimized()">应用这组参数</button>
                <span class="muted" style="margin-left:10px;font-size:12px">将写入 config.json 并影响后续扫描</span>
            </div>` : ''}
        </div>
    </div>

    <div class="panel">
        <div class="panel-head"><h2>样本内 vs 样本外对比</h2></div>
        <div class="panel-body flush"><div class="table-wrap"><table>
            <thead><tr><th>指标</th>
                <th>最优(样本内)</th><th>默认(样本内)</th>
                <th>最优(样本外)</th><th>默认(样本外)</th></tr></thead>
            <tbody>
                ${cmpRow('交易笔数', 'n_trades')}
                ${cmpRow('胜率', 'win_rate', '%')}
                ${cmpRow('平均单笔', 'avg_pnl', '%')}
                ${cmpRow('盈亏比', 'profit_factor')}
                ${cmpRow('最大回撤', 'max_drawdown', '%')}
                ${cmpRow('夏普', 'sharpe')}
                ${cmpRow('平均持有', 'avg_bars')}
            </tbody></table></div></div>
    </div>

    <div class="grid grid-2">
        <div class="panel">
            <div class="panel-head"><h2>参数对比</h2></div>
            <div class="panel-body flush"><div class="table-wrap"><table>
                <thead><tr><th>参数</th><th>优化后</th><th>默认</th></tr></thead>
                <tbody>${paramRows}</tbody></table></div></div>
        </div>
        <div class="panel">
            <div class="panel-head"><h2>为什么这样改</h2></div>
            <div class="panel-body">
                <ul class="reason-list" id="optInsight"></ul>
            </div>
        </div>
    </div>

    ${topRows ? `<div class="panel"><div class="panel-head"><h2>候选参数排序</h2>
        <div class="spacer"></div><span class="muted" style="font-size:12px">按样本内得分排序，再看样本外</span></div>
        <div class="panel-body flush"><div class="table-wrap"><table>
        <thead><tr><th>#</th><th>参数组合</th><th>样本内笔数</th><th>样本内胜率</th><th>样本内均收益</th>
            <th>样本外笔数</th><th>样本外均收益</th></tr></thead>
        <tbody>${topRows}</tbody></table></div></div></div>` : ''}`;

    renderOptInsight(b, bl);
}

function renderOptInsight(b, bl) {
    const ul = $('#optInsight');
    if (!ul) return;
    const items = [];
    const p = b.params || {}, d = bl.params || {};

    if (p.use_chan_stop === false && d.use_chan_stop === true) {
        items.push('关闭「缠论关键位止损」：缠论给的止损位往往过近，导致正常波动就被扫出局，改为固定幅度止损更稳。');
    }
    if (p.exit_on_reverse === false && d.exit_on_reverse === true) {
        items.push('关闭「反向信号出场」：缠论买卖点转换频繁，反向出场会把持仓周期切得过短，错过趋势段。');
    }
    if (p.min_stroke_bars !== undefined && d.min_stroke_bars !== undefined && p.min_stroke_bars !== d.min_stroke_bars) {
        items.push(`成笔K线数 ${d.min_stroke_bars} → ${p.min_stroke_bars}：`
            + (p.min_stroke_bars < d.min_stroke_bars ? '更灵敏，捕捉更多转折。' : '更严格，只保留结构性更强的笔。'));
    }
    if (p.stop_pct !== undefined && d.stop_pct !== undefined) {
        const r = p.take_pct / p.stop_pct;
        items.push(`盈亏比设定 ${(p.take_pct * 100).toFixed(0)}% / ${(p.stop_pct * 100).toFixed(0)}% `
            + `= ${r.toFixed(1)}:1，需要 ${(100 / (1 + r) * 100).toFixed(0)}% 以上胜率才能盈利。`);
    }
    const bi = b.in_sample || {}, bo = b.out_sample || {};
    if (bi.win_rate && bo.win_rate && bi.win_rate - bo.win_rate > 8) {
        items.push(`注意：样本内胜率 ${bi.win_rate}% 但样本外仅 ${bo.win_rate}%，`
            + '差距说明存在过拟合，实盘应以样本外数字为准。');
    }
    if (!items.length) items.push('优化参数与默认接近，说明当前设置在样本上已较稳健。');
    ul.innerHTML = items.map(x => `<li>${esc(x)}</li>`).join('');
}

async function applyOptimized() {
    try {
        const r = await api('/api/optimize/apply', { method: 'POST' });
        toast('参数已应用：成笔K线数 ' + r.config.chan.min_stroke_bars +
              '，止损 ' + (r.config.exit.stop_pct * 100).toFixed(0) + '%', 'ok', 4500);
        await loadSettings();
    } catch (e) {
        toast('应用失败: ' + e.message, 'err');
    }
}

async function resetParams() {
    if (!confirm('恢复为默认参数？将覆盖已优化的设置。')) return;
    try {
        await api('/api/optimize/reset', { method: 'POST' });
        toast('已恢复默认参数', 'ok');
        $('#opResult').innerHTML = '';
        await loadSettings();
    } catch (e) {
        toast('恢复失败: ' + e.message, 'err');
    }
}

// ---------------------------------------------------------------- 期权
async function openOptions(sym) {
    go('options');
    $('#optSymbol').value = sym;
    await loadOptions();
}

async function loadOptions() {
    const sym = ($('#optSymbol').value || '').trim().toUpperCase();
    if (!sym) { toast('请输入股票代码', 'err'); return; }
    const box = $('#optBody');
    box.innerHTML = `<div class="empty"><span class="spinner"></span><div class="msg" style="margin-top:12px">正在获取 ${sym} 期权链…</div></div>`;

    try {
        const res = await api('/api/symbol/' + sym + '/options');
        STATE.opts = res;
        renderOptions(res, sym);
    } catch (e) {
        box.innerHTML = `<div class="empty"><div class="big">⚠️</div><div class="msg">${esc(e.message)}</div>
            <div class="hint">该标的可能不支持期权，或期权数据暂不可用</div></div>`;
    }
}

function renderOptions(res, sym) {
    const box = $('#optBody');
    if (res.error && !res.strategies?.length) {
        box.innerHTML = `<div class="empty"><div class="big">📊</div><div class="msg">${esc(res.error)}</div>
            <div class="hint">可先查看正股信号，或换一个流动性好的标的（如 AAPL / NVDA / SPY）</div></div>`;
        return;
    }

    const env = res.iv_env;
    const envHtml = env ? `<div class="stat" style="grid-column:span 2;border-left:3px solid ${env.level === 'high' ? '#f5a623' : env.level === 'low' ? '#26a69a' : '#4f8cff'}">
            <div class="stat-label">期权估值环境 (IV/HV = ${env.ratio})</div>
            <div class="stat-value" style="font-size:19px">${{ high: '期权偏贵', low: '期权偏便宜', normal: '定价合理' }[env.level]}</div>
            <div class="stat-sub">${esc(env.advice)}</div></div>` : '';

    const strategies = (res.strategies || []).map(s => {
        const legs = (s.legs || []).map(l => `
            <div class="opt-leg">
                <span class="tag ${l.action === 'BUY' ? 'buy' : 'sell'}">${l.action === 'BUY' ? '买入' : '卖出'}</span>
                <span class="tag neutral">${l.type}</span>
                <span>行权价 <strong>${fmt(l.strike)}</strong></span>
                <span>权利金 <strong>${money(l.last)}</strong></span>
                <span>Delta <strong>${fmt(l.delta, 2)}</strong></span>
                <span>IV <strong>${l.iv ? (l.iv * 100).toFixed(1) + '%' : '—'}</strong></span>
                <span class="muted">OI ${l.open_interest || 0}</span>
            </div>`).join('');

        const m = (k, v) => `<div class="opt-metric"><div class="k">${k}</div><div class="v">${v}</div></div>`;
        return `<div class="opt-strategy">
            <h4>${esc(s.name)} <span class="tag ${s.direction === 'bearish' ? 'sell' : s.direction === 'bullish' ? 'buy' : 'neutral'}">${esc(s.tag)}</span></h4>
            <div class="opt-suit">${esc(s.suit || '')}</div>
            ${legs ? `<div class="opt-legs">${legs}</div>` : ''}
            <div class="opt-metrics">
                ${s.premium !== undefined && s.premium !== null ? m('权利金', money(s.premium)) : ''}
                ${m('最大收益', typeof s.max_profit === 'number' ? money(s.max_profit) : (s.max_profit || '—'))}
                ${m('最大亏损', typeof s.max_loss === 'number' ? money(s.max_loss) : (s.max_loss || '—'))}
                ${s.breakeven !== undefined && s.breakeven !== null ? m('盈亏平衡', money(s.breakeven)) : ''}
                ${s.capital !== undefined && s.capital !== null ? m('占用资金', typeof s.capital === 'number' ? money(s.capital) : s.capital) : ''}
            </div>
            <div class="opt-note">${esc(s.note || '')}</div>
            <div style="margin-top:9px;font-size:11.5px;color:#ef5350">⚠ 风险：${esc(s.risk || '—')}</div>
        </div>`;
    }).join('');

    // 期权链表格
    const chain = res.chain;
    let chainHtml = '';
    if (chain && chain.calls && chain.calls.length) {
        const rows = chain.calls.slice(0, 18).map((c, i) => {
            const p = (chain.puts || [])[i] || {};
            return `<tr>
                <td class="num">${fmt(c.strike)}</td>
                <td class="num">${money(c.last)}</td><td class="num">${fmt(c.delta, 2)}</td>
                <td class="num">${c.iv ? (c.iv * 100).toFixed(1) + '%' : '—'}</td>
                <td class="num">${c.volume || 0}</td><td class="num">${c.open_interest || 0}</td>
                <td class="num ${cls(p.change)}">${money(p.last)}</td><td class="num">${fmt(p.delta, 2)}</td>
                <td class="num">${p.iv ? (p.iv * 100).toFixed(1) + '%' : '—'}</td>
                <td class="num">${p.volume || 0}</td><td class="num">${p.open_interest || 0}</td>
            </tr>`;
        }).join('');
        chainHtml = `<div class="panel">
            <div class="panel-head"><h2>期权链</h2>
                <span class="tag accent">${chain.selected} · 剩余 ${chain.dte} 天</span>
                <div class="spacer"></div>
                <span class="muted" style="font-size:12px">标的 ${money(chain.underlying)}</span></div>
            <div class="panel-body flush">
                <div class="table-wrap"><table>
                <thead><tr><th colspan="6" style="color:#26a69a">CALL（看涨）</th><th colspan="5" style="color:#ef5350">PUT（看跌）</th></tr>
                <tr><th>行权价</th><th>权利金</th><th>Delta</th><th>IV</th><th>量</th><th>持仓</th>
                    <th>权利金</th><th>Delta</th><th>IV</th><th>量</th><th>持仓</th></tr></thead>
                <tbody>${rows}</tbody></table></div>
            </div></div>`;
    }

    const note = res.data_note
        ? `<div class="opt-note" style="border-left-color:#f5a623;margin-bottom:16px">
             ⚠ 数据说明：${esc(res.data_note)}</div>`
        : '';

    const theoBadge = res.theoretical
        ? `<div style="margin-bottom:14px"><span class="tag warn">理论估算报价</span>
           <span class="muted" style="font-size:12px;margin-left:8px">基于历史波动率 ${((res.hv || 0) * 100).toFixed(1)}% 的 Black-Scholes 计算值</span></div>`
        : '';

    box.innerHTML = `
        ${note}
        <div class="grid grid-4" style="margin-bottom:18px">
            <div class="stat"><div class="stat-label">标的</div><div class="stat-value" style="font-size:21px">${sym}</div>
                <div class="stat-sub">${money(res.price)}</div></div>
            <div class="stat"><div class="stat-label">到期日</div><div class="stat-value" style="font-size:17px">${esc(res.expiration || '—')}</div>
                <div class="stat-sub">剩余 ${res.dte ?? '—'} 天</div></div>
            ${envHtml}
        </div>
        ${theoBadge}
        ${renderOptHorizons(res.horizons)}
        <div class="panel">
            <div class="panel-head"><h2>策略明细（按信号直接映射）</h2></div>
            <div class="panel-body">${strategies || '<div class="muted">暂无建议</div>'}</div>
        </div>
        ${chainHtml}`;
}

/** 期限（短期/中长期）× 方向（Call/Put）策略矩阵 */
function renderOptHorizons(hz) {
    if (!hz || !hz.horizons) return '';
    const ctx = hz.context || {};
    const ctxTag = {
        bullish: ['buy', '缠论看多'], bearish: ['sell', '缠论看空'],
        mixed: ['warn', '多空信号并存'], neutral: ['neutral', '无明确信号'],
    }[ctx.direction] || ['neutral', '—'];

    const sideBlock = (title, tone, list) => {
        if (!list || !list.length) {
            return `<div class="opt-side"><div class="opt-side-h ${tone}">${title}</div>
                <div class="muted" style="padding:14px;font-size:12.5px">该期限下暂无可选合约</div></div>`;
        }
        return `<div class="opt-side"><div class="opt-side-h ${tone}">${title}</div>
            ${list.map(s => optStrategyCard(s)).join('')}</div>`;
    };

    const blocks = Object.values(hz.horizons).map(b => `
        <div class="opt-horizon">
            <div class="opt-h-head">
                <div>
                    <h3>${esc(b.label)}</h3>
                    <div class="opt-h-meta">
                        到期 ${esc(b.expiration || '—')} · 剩余 ${b.dte ?? '—'} 天
                        ${b.theoretical ? '<span class="tag warn" style="margin-left:8px">理论报价</span>' : ''}
                    </div>
                </div>
                <div class="spacer"></div>
                <div class="opt-h-best">适用：${esc(b.best_for || '')}</div>
            </div>
            <div class="opt-h-traits">
                ${(b.traits || []).map(t => `<div>· ${esc(t)}</div>`).join('')}
                <div class="opt-h-watch">⚠ ${esc(b.watch || '')}</div>
            </div>
            ${b.note ? `<div class="tag warn" style="margin-bottom:12px">${esc(b.note)}</div>` : ''}
            <div class="grid grid-2">
                ${sideBlock('▲ CALL · 看涨与多头工具', 'call', b.call)}
                ${sideBlock('▼ PUT · 看跌、防守与建仓工具', 'put', b.put)}
            </div>
        </div>`).join('');

    return `<div class="panel">
        <div class="panel-head">
            <h2>操作建议矩阵 · 期限 × 方向</h2>
            <div class="spacer"></div>
            <span class="tag ${ctxTag[0]}">当前：${esc(ctxTag[1])}</span>
        </div>
        <div class="panel-body">${blocks}</div>
    </div>`;
}

function optStrategyCard(s) {
    const sc = Number(s.score || 0);
    const scColor = sc >= 70 ? 'var(--up)' : sc >= 50 ? 'var(--accent)' : 'var(--muted)';
    const isCall = s.option_type === 'CALL';
    const legs = (s.legs || []).map(l => `
        <div class="opt-leg">
            <span class="tag ${l.action === 'BUY' ? 'buy' : 'sell'}">${l.action === 'BUY' ? '买入' : '卖出'}</span>
            <span class="tag neutral">${l.type}</span>
            <span>行权价 <strong>${fmt(l.strike)}</strong></span>
            <span>权利金 <strong>${money(l.last)}</strong></span>
            <span>Delta <strong>${fmt(l.delta, 2)}</strong></span>
            ${l.iv ? `<span>IV <strong>${(l.iv * 100).toFixed(1)}%</strong></span>` : ''}
        </div>`).join('');

    const m = (k, v) => `<div class="opt-metric"><div class="k">${k}</div><div class="v">${v}</div></div>`;
    const num = v => (typeof v === 'number' ? money(v) : (v ?? '—'));

    return `<div class="opt-strategy ${isCall ? 'is-call' : 'is-put'}">
        <div class="opt-s-head">
            <h4>${esc(s.name)}
                <span class="tag neutral">${esc(s.en || '')}</span>
                <span class="tag ${s.role === '买方' ? 'buy' : s.role === '卖方' ? 'sell' : 'accent'}">${esc(s.role || '')}</span>
            </h4>
            <div class="opt-score" style="color:${scColor}">${sc.toFixed(0)}</div>
        </div>
        <div class="opt-suit">适用：${esc(s.suit || '')}</div>
        <div class="opt-advice"><span class="lbl">操作建议</span><p>${esc(s.advice || '')}</p></div>
        <div class="opt-reason"><span class="lbl">操作理由</span><p>${esc(s.reason || '')}</p></div>
        ${legs ? `<div class="opt-legs">${legs}</div>` : ''}
        <div class="opt-metrics">
            ${s.premium !== undefined && s.premium !== null ? m('权利金', num(s.premium)) : ''}
            ${m('最大收益', num(s.max_profit))}
            ${m('最大亏损', num(s.max_loss))}
            ${s.breakeven !== undefined && s.breakeven !== null ? m('盈亏平衡', num(s.breakeven)) : ''}
            ${s.capital !== undefined && s.capital !== null ? m('占用资金', num(s.capital)) : ''}
        </div>
        <div class="opt-risk">⚠ ${esc(s.risk || '—')}</div>
    </div>`;
}

// ---------------------------------------------------------------- 设置
async function loadSettings() {
    const res = await api('/api/config');
    const c = res.config;
    $('#setStrokeBars').value = c.chan.min_stroke_bars;
    $('#setPowerRatio').value = c.chan.power_ratio;
    $('#setAutoScan').checked = !!c.auto_scan.enabled;
    $('#setHour').value = c.auto_scan.hour;
    $('#setMinute').value = c.auto_scan.minute;
    $('#setOs').checked = !!c.strategies.oversold;
    $('#setBo').checked = !!c.strategies.breakout;
    $('#setRv').checked = !!c.strategies.reversal;
}

async function saveSettings() {
    const patch = {
        chan: {
            min_stroke_bars: parseInt($('#setStrokeBars').value, 10),
            power_ratio: parseFloat($('#setPowerRatio').value),
        },
        auto_scan: {
            enabled: $('#setAutoScan').checked,
            hour: parseInt($('#setHour').value, 10),
            minute: parseInt($('#setMinute').value, 10),
        },
        strategies: {
            oversold: $('#setOs').checked,
            breakout: $('#setBo').checked,
            reversal: $('#setRv').checked,
        },
    };
    await api('/api/config', { method: 'POST', body: JSON.stringify({ patch }) });
    toast('设置已保存', 'ok');
}

// ---------------------------------------------------------------- 路由
const PAGE_TITLES = {
    dashboard: '总览看板', watchlist: '股票池', market: '市场扫描', hot: '热点板块',
    positions: '持仓盈亏', review: '信号复盘', volprice: '量价提醒',
    backtest: '策略回测', optimize: '参数自迭代',
    detail: '个股分析', options: '期权策略', chan: '缠论图解',
    settings: '系统设置',
};

function go(page) {
    if (STATE.page && STATE.page !== page) STATE.prevPage = STATE.page;
    STATE.page = page;
    $$('.page').forEach(p => p.classList.toggle('on', p.id === 'page-' + page));
    $$('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.page === page));
    $('#pageTitle').textContent = PAGE_TITLES[page] || '';
    window.scrollTo(0, 0);

    // 「返回」按钮：只在上一个页面有意义时显示（例如从热点板块点进来）
    const backBtn = $('#btnDetailBack');
    if (backBtn) {
        const prev = STATE.prevPage;
        const show = page === 'detail' && prev && prev !== 'detail' && PAGE_TITLES[prev];
        backBtn.style.display = show ? '' : 'none';
        if (show) backBtn.textContent = '← 返回' + PAGE_TITLES[prev];
    }

    if (page === 'dashboard') loadDashboard();
    if (page === 'watchlist') loadWatchlist();
    if (page === 'settings') loadSettings();
    if (page === 'positions') loadPositions();
    if (page === 'review') loadReview();
    if (page === 'volprice') loadVolprice();
    if (page === 'hot') loadHotspots();
    if (page === 'optimize') pollOptimize();
    if (page === 'chan') renderChanTheory();
}


// ---------------------------------------------------------------- 热点板块
const HOT_STATE = { loaded: false, sort: 'amount' };

async function loadHotspots(force = false) {
    const ids = ['#hotActive', '#hotSecIn', '#hotSecOut', '#hotStkIn', '#hotStkOut', '#hotIndustry'];
    const boxes = ids.map(i => $(i));
    if (force || !HOT_STATE.loaded) {
        boxes.forEach((b, i) => {
            b.innerHTML = `<div class="dim" style="padding:18px"><span class="spinner"></span> 正在统计资金流向…</div>`;
        });
        $('#hotStats').innerHTML =
            `<div class="dim" style="grid-column:1/-1"><span class="spinner"></span> 加载中…</div>`;
    }
    try {
        const qs = new URLSearchParams({
            window: $('#hotWindow') ? $('#hotWindow').value : '5',
            mode: $('#hotMode') ? $('#hotMode').value : 'full',
            market: STATE.market,
        });
        if (force) qs.set('_t', String(Date.now()));
        const res = await api('/api/hotspots?' + qs.toString());
        renderHotspots(res);
        HOT_STATE.loaded = true;
    } catch (e) {
        boxes.forEach(b => {
            b.innerHTML = `<div class="empty"><div class="big">⚠️</div><div class="msg">${esc(e.message)}</div></div>`;
        });
        $('#hotStats').innerHTML = '';
    }
}

/** 金额格式化：亿元 / 万元 */
function amtCNY(v) {
    if (v === null || v === undefined) return '—';
    const x = Number(v);
    if (!isFinite(x)) return '—';
    const a = Math.abs(x);
    if (a >= 1e8) return (x / 1e8).toFixed(2) + '亿';
    if (a >= 1e4) return (x / 1e4).toFixed(0) + '万';
    return x.toFixed(0);
}

function signedAmt(v) {
    if (v === null || v === undefined) return '—';
    const x = Number(v);
    const s = x >= 0 ? '+' : '-';
    return s + amtCNY(Math.abs(x));
}

function hotStatCard(label, value, sub, tone) {
    return `<div class="stat">
        <div class="stat-label">${label}</div>
        <div class="stat-value" style="font-size:19px;${tone ? 'color:' + tone : ''}">${value}</div>
        <div class="stat-sub">${sub || ''}</div>
    </div>`;
}

function renderHotspots(res) {
    if (!res || !res.ok) {
        $('#hotActive').innerHTML = `<div class="empty"><div class="msg">${esc((res && res.error) || '无数据')}</div></div>`;
        return;
    }
    const sec = res.sectors || {}, stk = res.stocks || {};
    const pb = res.price_breadth || {}, fb = res.flow_breadth || {};
    // 金额按市场货币显示（美股 USD / 港股 HKD）
    const cur = res.currency || (res.market === 'HK' ? 'HK$' : '$');
    const amt = v => (v === null || v === undefined) ? '—' : cur + amtCNY(v);
    const samt = v => (v === null || v === undefined)
        ? '—' : (v >= 0 ? '+' : '-') + cur + amtCNY(Math.abs(v));

    // ---- 概览卡 ----
    const toneColor = { risk_on: 'var(--up)', risk_off: 'var(--down)', mixed: 'var(--warn)' }[fb.tone] || '';
    const winLabel = res.mode === 'fast' ? '当日' : `近 ${res.window} 日`;
    $('#hotStats').innerHTML = [
        hotStatCard('市场资金情绪', fb.label || '—',
            `${fb.inflow || 0} 个板块流入 / ${fb.outflow || 0} 个流出`, toneColor),
        hotStatCard('涨跌家数', `${pb.up_ratio ?? '—'}%`,
            `涨 ${pb.up || 0} · 跌 ${pb.down || 0}（口径：价格）`),
        hotStatCard('统计范围', `${res.counts?.sectors || 0} 板块 / ${res.counts?.stocks || 0} 股`,
            `${cur}计价 · ${winLabel} · ${res.mode === 'fast' ? '实时报价' : '日线累计'} · 耗时 ${res.elapsed}s${res.cached ? ' · 缓存' : ''}`),
        hotStatCard('数据时间', (res.as_of || '—').slice(5, 16) || '—',
            esc((res.method && res.method.model) || '')),
    ].join('');

    $('#hotNote').innerHTML = res.note
        ? `<div class="tag warn" style="display:block;padding:9px 12px;line-height:1.65;font-size:12.5px">⚠ ${esc(res.note)}</div>`
        : '';

    // ---- ① 活跃 TOP10 ----
    const active = sec.active || [];
    const maxTurn = Math.max(1, ...active.map(r => r.turnover || 0));
    $('#hotActive').innerHTML = active.length ? `<div class="hot-active">
        ${active.map((r, i) => {
        const w = ((r.turnover || 0) / maxTurn * 100).toFixed(1);
        const kindTag = r.kind === 'industry'
            ? '<span class="tag accent">行业</span>' : '<span class="tag purple">主题</span>';
        const nf = r.net_flow || 0;
        const flowCls = nf >= 0 ? 'up' : 'down';
        return `<div class="hot-row clickable" data-sym="${esc(r.symbol)}"
                onclick="openDetail('${esc(r.symbol)}')" title="点击进入 ${esc(r.symbol)} 个股分析">
                <div class="hot-rank">${i + 1}</div>
                <div class="hot-main">
                    <div class="hot-name">${esc(r.symbol)} <span class="hot-cn">${esc(r.name)}</span> ${kindTag}
                        <span class="hot-desc">${esc(r.desc || '')}</span></div>
                    <div class="hot-bar"><i style="width:${w}%;background:${nf >= 0 ? 'var(--up)' : 'var(--down)'}"></i></div>
                </div>
                <div class="hot-metrics">
                    <div><span>活跃度</span><b>${r.act_score ?? '—'}</b></div>
                    <div><span>成交额</span><b>${amt(r.turnover)}</b></div>
                    <div><span>占比</span><b>${r.turnover_share ?? '—'}%</b></div>
                    <div><span>量比</span><b>${r.vol_ratio ?? '—'}</b></div>
                    <div><span>涨跌</span><b class="num ${(r.change_pct || 0) >= 0 ? 'up' : 'down'}">${fmtPct(r.change_pct)}</b></div>
                    <div><span>净额</span><b class="num ${flowCls}">${samt(nf)}</b></div>
                </div>
            </div>`;
    }).join('')}</div>` : '<div class="muted">暂无数据</div>';

    // ---- ②③ 板块流入/流出 ----
    const secCard = (r, i, isIn) => {
        const nf = r.net_flow || 0;
        const fr = r.flow_ratio;
        return `<div class="hot-card ${isIn ? 'in' : 'out'} clickable" data-sym="${esc(r.symbol)}"
                onclick="openDetail('${esc(r.symbol)}')" title="点击进入 ${esc(r.symbol)} 分析">
            <div class="hot-c-head">
                <span class="hot-c-rank">${i + 1}</span>
                <b>${esc(r.symbol)}</b>
                <span class="hot-cn">${esc(r.name)}</span>
                <div class="spacer"></div>
                <span class="hot-c-price">${money(r.price)}</span>
                <span class="num ${(r.change_pct || 0) >= 0 ? 'up' : 'down'}">${fmtPct(r.change_pct)}</span>
                <button class="btn sm hot-add" title="加入股票池"
                    onclick="event.stopPropagation();addToPool('${esc(r.symbol)}')">+池</button>
            </div>
            <div class="hot-c-flow">
                <div class="hot-c-net ${nf >= 0 ? 'up' : 'down'}">${samt(nf)}</div>
                <div class="hot-c-sub">成交 ${amt(r.turnover)}</div>
            </div>
            <div class="hot-c-strength">
                <span>强度</span>
                <div class="hot-c-bar"><i style="width:${Math.min(100, Math.abs(fr || 0))}%;background:${(fr || 0) >= 0 ? 'var(--up)' : 'var(--down)'}"></i></div>
                <b class="${(fr || 0) >= 0 ? 'up' : 'down'}">${fr === null || fr === undefined ? '—' : fr + '%'}</b>
            </div>
        </div>`;
    };
    $('#hotSecIn').innerHTML = (sec.inflow || []).map((r, i) => secCard(r, i, true)).join('')
        || '<div class="muted">暂无数据</div>';
    $('#hotSecOut').innerHTML = (sec.outflow || []).map((r, i) => secCard(r, i, false)).join('')
        || '<div class="muted">暂无数据</div>';

    // ---- ④⑤ 个股流入/流出 ----
    const stkCard = (r, i, isIn) => {
        const nf = r.net_flow || 0;
        const fr = r.flow_ratio;
        return `<div class="hot-card ${isIn ? 'in' : 'out'} clickable" data-sym="${esc(r.symbol)}"
                onclick="openDetail('${esc(r.symbol)}')" title="点击进入 ${esc(r.symbol)} 个股分析">
            <div class="hot-c-head">
                <span class="hot-c-rank">${i + 1}</span>
                <b>${esc(r.symbol)}</b>
                <span class="hot-cn">${esc(r.name)}</span>
                <span class="tag neutral">${esc(r.industry || '')}</span>
                <div class="spacer"></div>
                <span class="hot-c-price">${money(r.price)}</span>
                <span class="num ${(r.change_pct || 0) >= 0 ? 'up' : 'down'}">${fmtPct(r.change_pct)}</span>
                <button class="btn sm hot-add" title="加入股票池"
                    onclick="event.stopPropagation();addToPool('${esc(r.symbol)}')">+池</button>
            </div>
            <div class="hot-c-flow">
                <div class="hot-c-net ${nf >= 0 ? 'up' : 'down'}">${samt(nf)}</div>
                <div class="hot-c-sub">成交 ${amt(r.turnover)}</div>
            </div>
            <div class="hot-c-strength">
                <span>强度</span>
                <div class="hot-c-bar"><i style="width:${Math.min(100, Math.abs(fr || 0))}%;background:${(fr || 0) >= 0 ? 'var(--up)' : 'var(--down)'}"></i></div>
                <b class="${(fr || 0) >= 0 ? 'up' : 'down'}">${fr === null || fr === undefined ? '—' : fr + '%'}</b>
            </div>
        </div>`;
    };
    $('#hotStkIn').innerHTML = (stk.inflow || []).map((r, i) => stkCard(r, i, true)).join('')
        || '<div class="muted">暂无数据</div>';
    $('#hotStkOut').innerHTML = (stk.outflow || []).map((r, i) => stkCard(r, i, false)).join('')
        || '<div class="muted">暂无数据</div>';

    // ---- 行业聚合 ----
    const agg = res.industry_agg || [];
    const maxAbs = Math.max(1, ...agg.map(a => Math.abs(a.net_flow || 0)));
    $('#hotIndustry').innerHTML = agg.length ? `<div class="hot-agg">
        ${agg.map(a => {
        const w = Math.abs(a.net_flow || 0) / maxAbs * 50;
        const pos = (a.net_flow || 0) >= 0;
        const etfClick = a.etf
            ? `clickable" data-sym="${esc(a.etf)}" onclick="openDetail('${esc(a.etf)}')" title="点击分析 ${esc(a.etf)}（${esc(a.industry)}行业 ETF）`
            : '';
        return `<div class="hot-agg-row ${etfClick}">
                <div class="hot-agg-name">${esc(a.industry)}${a.etf ? `<span class="hot-agg-etf">${esc(a.etf)}</span>` : ''}<span class="hot-agg-n">${a.count} 只</span></div>
                <div class="hot-agg-bar">
                    <div class="hot-agg-mid"></div>
                    <i style="${pos ? `left:50%;width:${w}%` : `right:50%;width:${w}%`};background:${pos ? 'var(--up)' : 'var(--down)'}"></i>
                </div>
                <div class="hot-agg-val ${pos ? 'up' : 'down'}">${samt(a.net_flow)}</div>
                <div class="hot-agg-br">涨${a.breadth ?? '—'}%</div>
            </div>`;
    }).join('')}</div>` : '<div class="muted">暂无数据</div>';
}


// ---------------------------------------------------------------- 量价提醒
const VP_TONE = { good: 'var(--up)', warn: 'var(--warn)', bad: 'var(--down)', neutral: 'var(--text-2)' };

async function loadVolprice(force = false) {
    const stats = $('#vpStats');
    const buysBox = $('#vpBuys');
    const sellsBox = $('#vpSells');
    stats.innerHTML = `<div class="dim" style="grid-column:1/-1"><span class="spinner"></span> 正在扫描量价形态…</div>`;
    buysBox.innerHTML = sellsBox.innerHTML =
        `<div class="empty"><div class="msg">扫描中…</div></div>`;
    try {
        const qs = new URLSearchParams({
            period: $('#vpPeriod').value,
            min_confidence: $('#vpThreshold').value || '55',
            refresh: force ? '1' : '0',
        });
        const syms = $('#vpSymbols').value.trim();
        if (syms) qs.set('symbols', syms);
        const res = await api('/api/volprice?' + qs.toString());
        renderVolprice(res);
    } catch (e) {
        stats.innerHTML = '';
        buysBox.innerHTML = sellsBox.innerHTML =
            `<div class="empty"><div class="big">⚠️</div><div class="msg">${esc(e.message)}</div></div>`;
    }
    loadVpMeta();
}

function renderVolprice(res) {
    const buys = res.buys || [];
    const sells = res.sells || [];
    const stats = $('#vpStats');

    const high = [...buys, ...sells].filter(x => x.confidence >= 75).length;
    stats.innerHTML = `
        <div class="stat">
            <div class="stat-label">买入提醒</div>
            <div class="stat-value" style="color:var(--up)">${buys.length}</div>
            <div class="stat-sub">量价形态看多</div>
        </div>
        <div class="stat">
            <div class="stat-label">卖出提醒</div>
            <div class="stat-value" style="color:var(--down)">${sells.length}</div>
            <div class="stat-sub">量价形态看空</div>
        </div>
        <div class="stat">
            <div class="stat-label">高置信信号</div>
            <div class="stat-value" style="color:var(--warn)">${high}</div>
            <div class="stat-sub">置信度 ≥ 75</div>
        </div>
        <div class="stat">
            <div class="stat-label">已扫描标的</div>
            <div class="stat-value">${res.scanned ?? 0}</div>
            <div class="stat-sub">门槛 ${res.threshold ?? 55} 分 · ${res.date || ''}</div>
        </div>`;

    $('#vpBuyCount').textContent = buys.length;
    $('#vpSellCount').textContent = sells.length;
    $('#badgeVP').textContent = buys.length + sells.length;
    $('#badgeVP').style.display = (buys.length + sells.length) ? '' : 'none';

    $('#vpBuys').innerHTML = buys.length
        ? buys.map(a => vpCard(a)).join('')
        : `<div class="empty"><div class="big">◇</div><div class="msg">暂无买入提醒</div>
           <div class="hint">当前无标的同时满足量价形态与置信门槛</div></div>`;

    $('#vpSells').innerHTML = sells.length
        ? sells.map(a => vpCard(a)).join('')
        : `<div class="empty"><div class="big">◇</div><div class="msg">暂无卖出提醒</div>
           <div class="hint">持仓股出现放量滞涨、顶背离等形态时会在此提示</div></div>`;

    if (res.errors && res.errors.length) {
        $('#vpBuys').insertAdjacentHTML('beforeend',
            `<div class="tag warn" style="margin-top:12px">${res.errors.length} 只标的取数失败：${esc(res.errors[0])}</div>`);
    }
}

function vpCard(a) {
    const isBuy = a.side === 'buy';
    const conf = Number(a.confidence || 0);
    const confColor = conf >= 75 ? 'var(--warn)' : (conf >= 62 ? 'var(--accent)' : 'var(--text-2)');
    const rel = a.relation || {};
    const m = a.metrics || {};

    const kv = (k, v, colorCls = '') => v === null || v === undefined || v === ''
        ? '' : `<div class="kv"><span class="k">${k}</span><span class="v ${colorCls}">${v}</span></div>`;

    const stopPct = a.stop ? ((a.stop / a.price - 1) * 100) : null;
    const tgtPct = a.target ? ((a.target / a.price - 1) * 100) : null;

    return `<div class="vp-card ${isBuy ? 'buy' : 'sell'}" data-symbol="${esc(a.symbol)}">
        <div class="vp-head">
            <div class="vp-title">
                <b>${esc(a.symbol)}</b>
                <span class="dim">${esc(a.name || '')}</span>
                <span class="tag ${isBuy ? 'buy' : 'sell'}">${esc(a.signal_name || a.code)}</span>
                ${a.resonance ? '<span class="tag purple">缠论共振</span>' : ''}
                ${a.has_position ? '<span class="tag accent">持仓中</span>' : ''}
            </div>
            <div class="vp-conf" title="置信度">
                <div class="conf-bar"><i style="width:${conf}%;background:${confColor}"></i></div>
                <span style="color:${confColor};font-weight:700">${conf.toFixed(0)}</span>
            </div>
        </div>

        <div class="vp-line">
            <span class="vp-code">${esc(a.code)}</span>
            <span class="vp-name">${esc(a.signal_name || '')}</span>
            ${rel.label ? `<span class="tag ${rel.tone === 'good' ? 'buy' : rel.tone === 'bad' ? 'sell' : 'neutral'}"
                style="background:transparent;border:1px solid ${VP_TONE[rel.tone] || 'var(--border-2)'};color:${VP_TONE[rel.tone] || 'var(--text-2)'}">${esc(rel.label)}</span>` : ''}
            <span class="spacer"></span>
            <span class="${cls(a.change_pct)}">${fmtPct(a.change_pct)}</span>
        </div>

        <div class="vp-levels">
            ${kv('触发价', fmt(a.price))}
            ${kv('止损', a.stop ? `${fmt(a.stop)} (${fmtPct(stopPct)})` : '—', 'down')}
            ${kv('目标', a.target ? `${fmt(a.target)} (${fmtPct(tgtPct)})` : '—', 'up')}
            ${kv('量比', m.vr !== undefined && m.vr !== null ? `${fmt(m.vr, 2)}×` : (a.state ? '' : ''))}
            ${kv('MFI', m.mfi !== undefined && m.mfi !== null ? fmt(m.mfi, 0) : '')}
            ${kv('CMF', m.cmf !== undefined && m.cmf !== null ? fmt(m.cmf, 3) : '')}
        </div>

        <ul class="vp-reasons">
            ${(a.reasons || []).map(r => `<li>${esc(r)}</li>`).join('')}
        </ul>
        <div class="vp-foot dim">${esc(a.date || '')} · ${esc(a.desc || '')}</div>
    </div>`;
}

async function runSourceCompare() {
    const box = $('#srcCompareResult');
    const btn = $('#btnSrcCompare');
    btn.disabled = true;
    btn.textContent = '回测中…';
    box.innerHTML = `<div class="dim"><span class="spinner"></span> 正在回测三种信号源（含样本外检验）…</div>`;
    try {
        const res = await api('/api/backtest/sources', {
            method: 'POST',
            body: JSON.stringify({ period: '3y', mode: 'long_only', oos_ratio: 0.7 }),
        });
        renderSourceCompare(res);
    } catch (e) {
        box.innerHTML = `<div class="down">${esc(e.message)}</div>`;
    } finally {
        btn.disabled = false;
        btn.textContent = '运行对比回测';
    }
}

function renderSourceCompare(res) {
    const box = $('#srcCompareResult');
    if (!res || !res.ok) {
        box.innerHTML = `<div class="down">${esc((res && res.error) || '回测失败')}</div>`;
        return;
    }
    const cell = (s) => {
        if (!s || !s.n_trades) return '<td class="num dim">—</td>'.repeat(3);
        const wr = s.win_rate ?? 0;
        const ap = s.avg_pnl ?? 0;
        return `<td class="num">${s.n_trades}</td>`
            + `<td class="num" style="color:${wr >= 55 ? 'var(--up)' : wr < 50 ? 'var(--down)' : 'var(--text-2)'};font-weight:650">${wr}%</td>`
            + `<td class="num ${cls(ap)}">${fmtPct(ap)}</td>`;
    };
    const rows = (res.rows || []).map(r => `<tr>
        <td style="font-weight:650">${esc(r.name)}</td>
        ${cell(r.in_sample)}${cell(r.out_sample)}
        <td class="dim" style="font-size:12px">${esc(r.desc || '')}</td>
    </tr>`).join('');

    const v = res.verdict || {};
    const best = v.best_name || '—';
    const enough = v.volprice_enough_samples;

    box.innerHTML = `
        <table class="tbl">
            <thead><tr>
                <th rowspan="2" style="width:130px">信号源</th>
                <th colspan="3" style="text-align:center">样本内（前 ${Math.round((1 - res.oos_ratio) * 100)}%）</th>
                <th colspan="3" style="text-align:center">样本外（后 ${Math.round(res.oos_ratio * 100)}%）</th>
                <th rowspan="2">说明</th>
            </tr><tr>
                <th class="num">笔数</th><th class="num">胜率</th><th class="num">均值</th>
                <th class="num">笔数</th><th class="num">胜率</th><th class="num">均值</th>
            </tr></thead>
            <tbody>${rows}</tbody>
        </table>
        <div class="tag ${enough ? 'accent' : 'warn'}" style="margin-top:14px">
            样本外单笔期望最优：${esc(best)}
        </div>
        <div class="dim" style="margin-top:9px;font-size:12.5px;line-height:1.7">
            ${esc(v.note || '')}
        </div>
        <div class="dim" style="margin-top:9px;font-size:12px">
            出场规则：止损 ${(res.exit?.stop_pct * 100).toFixed(0)}% · 止盈 ${(res.exit?.take_pct * 100).toFixed(0)}%
            · 最长持有 ${res.exit?.max_hold} 根K线 · ${(res.symbols || []).length} 只标的
        </div>`;
}


async function loadVpMeta() {
    const box = $('#vpMeta');
    if (!box || box.dataset.loaded === '1') return;
    try {
        const res = await api('/api/volprice/meta');
        const meta = res.meta || {};
        const row = (code) => {
            const m = meta[code] || {};
            const buy = m.side === 'buy';
            return `<tr>
                <td><span class="tag ${buy ? 'buy' : 'sell'}">${esc(code)}</span></td>
                <td style="font-weight:600">${esc(m.name || '')}</td>
                <td class="dim">${esc(m.desc || '')}</td>
            </tr>`;
        };
        box.innerHTML = `<table class="tbl">
            <thead><tr><th style="width:92px">代码</th><th style="width:130px">形态</th><th>含义</th></tr></thead>
            <tbody>
                ${(res.buy_codes || []).map(row).join('')}
                ${(res.sell_codes || []).map(row).join('')}
            </tbody>
        </table>`;
        box.dataset.loaded = '1';
    } catch (e) {
        box.innerHTML = `<div class="dim" style="padding:14px">形态说明加载失败</div>`;
    }
}


// ---------------------------------------------------------------- 缠论图解
const C_UP = '#26a69a', C_DN = '#ef5350', C_DIM = '#6e7b8d',
    C_TXT = '#a9b4c2', C_ACC = '#4f8cff', C_ZS = '#f5a623';

/** 画一根K线：cx 中心x，yh/yl 影线，yo/yc 开收（y 越小价格越高） */
function svgK(cx, yh, yl, yo, yc, w = 13) {
    const up = yc < yo;
    const col = up ? C_UP : C_DN;
    const top = Math.min(yo, yc);
    const h = Math.max(Math.abs(yo - yc), 2.5);
    return `<line x1="${cx}" y1="${yh}" x2="${cx}" y2="${yl}" stroke="${col}" stroke-width="1.4"/>`
        + `<rect x="${cx - w / 2}" y="${top}" width="${w}" height="${h}" fill="${col}" rx="1.5"/>`;
}

function svgWrap(h, inner) {
    return `<svg viewBox="0 0 680 ${h}" width="100%" style="max-width:680px;display:block;margin:0 auto"`
        + ` xmlns="http://www.w3.org/2000/svg">${inner}</svg>`;
}

function svgT(x, y, t, o = {}) {
    const size = o.size || 12, fill = o.fill || C_TXT;
    const anchor = o.anchor || 'middle', weight = o.weight || 400;
    return `<text x="${x}" y="${y}" font-size="${size}" fill="${fill}"`
        + ` text-anchor="${anchor}" font-weight="${weight}">${t}</text>`;
}

const CHAN_CONCEPTS = [
    ['走势必完美', '公理', '任何级别、任何走势类型终将完成。既然必然完成，那么在「完成的临界点」介入就是效率最高的——这是缠论一切买点的哲学源头。'],
    ['级别', '递归', '1分/5分/日线/周线……是同一走势的不同放大倍率。大级别定方向，小级别定介入点，必须联立观察。'],
    ['自同构性', '结构', '分型、笔、线段、中枢在任意级别重复出现。正因为结构自相似，缠论才能递归分析、跨级别套用。'],
    ['中枢', '核心', '被至少三个连续次级别走势重叠覆盖的价格区间，是多空反复争夺的战场，也是走势的引力中心。'],
    ['背驰', '转折', '价格创新高/新低，但力度（MACD面积、量能）反而衰竭。它是「走势必完美」的力学表达，也是转折信号。'],
    ['三类买卖点', '操作', '一类是转折起点，二类是回调确认，三类是突破确认——覆盖趋势从诞生到确认的全过程。'],
];

// ---- 七步图解 ----
const CHAN_STEPS = [
    {
        n: 1, title: 'K线包含处理', tag: '标准化',
        desc: '相邻两根K线若存在包含关系（一根的高低点全部落在另一根之内），必须合并成一根新K线，'
            + '否则后续的分型判断会被噪音干扰。合并规则：向上走势取「高高的高、高高的低」，向下反之。',
        note: '这是缠论全部推导的地基——价格先被标准化，才谈得上结构。',
        svg: () => {
            const ks = [
                svgK(90, 40, 120, 110, 50), svgK(150, 55, 105, 60, 100),
                svgK(210, 47, 95, 52, 88), svgK(270, 30, 78, 36, 70),
            ].join('');
            const merged = svgK(150, 125, 205, 195, 135, 15) + svgK(270, 115, 163, 121, 155, 15);
            return svgWrap(215,
                svgT(60, 24, '原始K线', { anchor: 'start', size: 12, fill: C_DIM })
                + ks
                + `<rect x="126" y="46" width="108" height="66" fill="none" stroke="${C_ZS}"`
                + ` stroke-width="1.2" stroke-dasharray="4 3"/>`
                + svgT(180, 138, 'K2、K3 被 K1 包含', { size: 11, fill: C_ZS })
                + `<line x1="340" y1="70" x2="340" y2="142" stroke="${C_DIM}" stroke-width="1.2"/>`
                + `<polygon points="340,154 334,141 346,141" fill="${C_DIM}"/>`
                + svgT(360, 112, '合并', { anchor: 'start', size: 11, fill: C_DIM })
                + svgT(60, 122, '合并后', { anchor: 'start', size: 12, fill: C_DIM })
                + merged
                + svgT(150, 220, '新K线（高=最高，低=最低）', { size: 11, fill: C_UP }));
        },
    },
    {
        n: 2, title: '分型', tag: '顶与底',
        desc: '顶分型：中间K线的高点最高、低点也最高；底分型反之。分型是所有走势结构的最小单位，'
            + '代表一次多空博弈的胜负已分。',
        note: '注意：分型必须由「合并后」的K线构成，直接用原始K线会大量误判。',
        svg: () => svgWrap(200,
            svgT(200, 22, '顶分型', { size: 13, fill: C_DN, weight: 600 })
            + svgK(140, 75, 135, 85, 125) + svgK(200, 40, 105, 48, 98) + svgK(260, 80, 140, 90, 130)
            + svgT(200, 158, '中间K线：高点最高、低点也最高', { size: 11 })
            + `<line x1="320" y1="30" x2="320" y2="150" stroke="#2b3546"/>`
            + svgT(500, 22, '底分型', { size: 13, fill: C_UP, weight: 600 })
            + svgK(440, 60, 120, 68, 112) + svgK(500, 95, 155, 100, 150) + svgK(560, 55, 118, 62, 110)
            + svgT(500, 178, '中间K线：低点最低、高点也最低', { size: 11 })),
    },
    {
        n: 3, title: '笔', tag: '最小走势',
        desc: '两个相邻且性质相反的顶/底分型之间，若中间至少间隔一根独立K线，就构成一笔。'
            + '向上笔 = 底分型→顶分型；向下笔 = 顶分型→底分型。笔是缠论中最小的走势单位。',
        note: '本系统额外要求「成笔最少K线数」（默认4）与最小幅度，过滤毛刺笔。',
        svg: () => {
            const pts = [[70, 155], [160, 62], [250, 122], [340, 45], [430, 112], [520, 58]];
            const noise = [[70, 155], [100, 120], [118, 138], [140, 80], [160, 62], [195, 95],
            [215, 78], [250, 122], [285, 90], [305, 100], [340, 45], [380, 80],
            [400, 65], [430, 112], [465, 85], [495, 95], [520, 58]];
            const poly = noise.map(p => p.join(',')).join(' ');
            let lines = '';
            for (let i = 0; i < pts.length - 1; i++) {
                const up = pts[i][1] > pts[i + 1][1];
                lines += `<line x1="${pts[i][0]}" y1="${pts[i][1]}" x2="${pts[i + 1][0]}"`
                    + ` y2="${pts[i + 1][1]}" stroke="${up ? C_UP : C_DN}" stroke-width="3.2" opacity=".95"/>`;
            }
            const dots = pts.map((p, i) =>
                `<circle cx="${p[0]}" cy="${p[1]}" r="4.5" fill="${i % 2 === 0 ? C_UP : C_DN}"/>`).join('');
            return svgWrap(200,
                `<polyline points="${poly}" fill="none" stroke="${C_DIM}" stroke-width="1.1" opacity=".55"/>`
                + lines + dots
                + svgT(70, 178, '底', { size: 11, fill: C_UP }) + svgT(160, 48, '顶', { size: 11, fill: C_DN })
                + svgT(250, 140, '底', { size: 11, fill: C_UP }) + svgT(340, 31, '顶', { size: 11, fill: C_DN })
                + svgT(430, 130, '底', { size: 11, fill: C_UP }) + svgT(520, 44, '顶', { size: 11, fill: C_DN })
                + svgT(600, 120, '细线=实际价格', { anchor: 'start', size: 11, fill: C_DIM })
                + svgT(600, 140, '粗线=笔', { anchor: 'start', size: 11, fill: C_ACC }));
        },
    },
    {
        n: 4, title: '线段', tag: '更高一级',
        desc: '至少三笔构成一条线段，且线段方向由其中笔的整体方向决定。线段是比笔高一级的走势单位，'
            + '用于构筑中枢。',
        note: '原版缠论用「特征序列」划分线段，主观性最强；本系统改用笔序列直接构建（见「升级」第1条）。',
        svg: () => {
            const p = [[60, 160], [150, 70], [235, 125], [330, 40], [420, 105], [510, 55], [600, 115]];
            let bi = '';
            for (let i = 0; i < p.length - 1; i++) {
                const up = p[i][1] > p[i + 1][1];
                bi += `<line x1="${p[i][0]}" y1="${p[i][1]}" x2="${p[i + 1][0]}" y2="${p[i + 1][1]}"`
                    + ` stroke="${up ? C_UP : C_DN}" stroke-width="2.2" opacity=".8"/>`;
            }
            return svgWrap(190,
                bi
                + `<line x1="60" y1="160" x2="330" y2="40" stroke="${C_ACC}" stroke-width="4" opacity=".9"/>`
                + `<line x1="330" y1="40" x2="600" y2="115" stroke="${C_ACC}" stroke-width="4" opacity=".9"/>`
                + p.map(q => `<circle cx="${q[0]}" cy="${q[1]}" r="3.5" fill="${C_TXT}"/>`).join('')
                + svgT(195, 30, '线段一（由前3笔构成 · 向上）', { size: 11, fill: C_ACC })
                + svgT(470, 88, '线段二（向下）', { size: 11, fill: C_ACC })
                + svgT(340, 178, '实线=笔   粗蓝线=线段', { size: 11, fill: C_DIM }));
        },
    },
    {
        n: 5, title: '中枢', tag: '走势核心',
        desc: '连续三个次级别走势类型的重叠区间，即为中枢。上沿记 ZG、下沿记 ZD。'
            + '中枢是缠论的核心概念：走势的一切变化，本质是围绕中枢的离开与回拉。',
        note: '中枢延伸=震荡持续；新生中枢=趋势延续；中枢破坏=转折来临。',
        svg: () => {
            const p = [[70, 150], [170, 58], [265, 118], [360, 46], [455, 108], [550, 50]];
            let bi = '';
            for (let i = 0; i < p.length - 1; i++) {
                const up = p[i][1] > p[i + 1][1];
                bi += `<line x1="${p[i][0]}" y1="${p[i][1]}" x2="${p[i + 1][0]}" y2="${p[i + 1][1]}"`
                    + ` stroke="${up ? C_UP : C_DN}" stroke-width="2.4"/>`;
            }
            return svgWrap(210,
                `<rect x="120" y="58" width="200" height="60" fill="${C_ZS}" opacity=".13"`
                + ` stroke="${C_ZS}" stroke-width="1.3" stroke-dasharray="5 3"/>`
                + bi
                + p.map(q => `<circle cx="${q[0]}" cy="${q[1]}" r="3.5" fill="${C_TXT}"/>`).join('')
                + svgT(100, 62, 'ZG 上沿', { anchor: 'end', size: 11, fill: C_ZS })
                + svgT(100, 120, 'ZD 下沿', { anchor: 'end', size: 11, fill: C_ZS })
                + svgT(220, 148, '中枢区间（多空反复争夺）', { size: 11, fill: C_ZS })
                + svgT(400, 45, '离开中枢', { size: 11, fill: C_UP })
                + svgT(560, 38, '三买区域', { size: 11, fill: C_ACC })
                + svgT(340, 192, '围绕中枢的运动：进入 → 震荡 → 离开', { size: 11, fill: C_DIM }));
        },
    },
    {
        n: 6, title: '背驰', tag: '转折信号',
        desc: '价格创出新高（或新低），但对应的力度指标反而减弱——这就是背驰。'
            + '它意味着推动走势的能量已经衰竭，「走势必完美」将要兑现。',
        note: '本系统用 MACD 柱面积（而非单纯看柱高）比较两段同向走势的力度，更稳健。',
        svg: () => {
            const AXIS = 210;                       // MACD 零轴
            const bar = (x, h) => `<rect x="${x}" y="${h >= 0 ? AXIS - h : AXIS}" width="8"`
                + ` height="${Math.abs(h)}" fill="${h >= 0 ? C_UP : C_DN}" opacity=".78"/>`;
            let macd = '';
            for (let i = 0; i < 14; i++) macd += bar(120 + i * 12, Math.sin(i / 2.2) * 26 + 6);
            for (let i = 0; i < 12; i++) macd += bar(370 + i * 12, Math.sin(i / 2.2) * 15 + 3);
            return svgWrap(285,
                svgT(60, 24, '价格', { anchor: 'start', size: 12, fill: C_DIM })
                + `<polyline points="70,165 150,95 200,130 300,60 360,105 450,42 520,80 600,55"`
                + ` fill="none" stroke="${C_TXT}" stroke-width="2"/>`
                + `<circle cx="300" cy="60" r="4" fill="${C_DN}"/><circle cx="450" cy="42" r="4" fill="${C_DN}"/>`
                + svgT(300, 50, '高点A', { size: 11, fill: C_DN })
                + svgT(478, 32, '高点B（创新高）', { size: 11, fill: C_DN })
                + svgT(60, 185, 'MACD', { anchor: 'start', size: 12, fill: C_DIM })
                + `<line x1="100" y1="${AXIS}" x2="620" y2="${AXIS}" stroke="#3a465c" stroke-width="1"/>`
                + svgT(632, AXIS + 4, '0', { anchor: 'start', size: 10, fill: C_DIM })
                + macd
                + svgT(200, 252, '面积大', { size: 11, fill: C_UP })
                + svgT(430, 252, '面积反而小', { size: 11, fill: C_ZS })
                + svgT(340, 276, '价创新高 + 力度衰竭 = 顶背驰', { size: 12, fill: C_ZS, weight: 600 }));
        },
    },
    {
        n: 7, title: '三类买卖点', tag: '操作落点',
        desc: '一类买点：下跌趋势背驰后的转折起点；二类买点：一类之后回调不破新低的确认点；'
            + '三类买点：离开中枢后回抽不回中枢的加速点。卖点S1/S2/S3 完全镜像。',
        note: '一类风险最大收益最高，三类最稳但位置较高——本系统按缠论强度+量价共振给出置信度。',
        svg: () => {
            const path = '60,55 120,120 170,95 220,140 285,205 350,130 405,168 470,98 530,135 600,70';
            const zs = `<rect x="120" y="95" width="100" height="45" fill="${C_ZS}" opacity=".13"`
                + ` stroke="${C_ZS}" stroke-width="1.2" stroke-dasharray="4 3"/>`
                + `<rect x="410" y="98" width="120" height="70" fill="${C_ZS}" opacity=".13"`
                + ` stroke="${C_ZS}" stroke-width="1.2" stroke-dasharray="4 3"/>`;
            const mark = (x, y, t, c) => `<circle cx="${x}" cy="${y}" r="7" fill="none" stroke="${c}" stroke-width="2"/>`
                + svgT(x, y - 14, t, { size: 12, fill: c, weight: 700 })
                + `<line x1="${x}" y1="${y + 7}" x2="${x}" y2="${y + 26}" stroke="${c}" stroke-width="1" stroke-dasharray="3 2"/>`;
            return svgWrap(250,
                zs
                + `<polyline points="${path}" fill="none" stroke="${C_TXT}" stroke-width="2"/>`
                + mark(285, 205, 'B1', C_UP) + mark(405, 168, 'B2', C_UP) + mark(530, 135, 'B3', C_ACC)
                + mark(120, 120, 'S1', C_DN)
                + svgT(285, 240, '背驰转折', { size: 11, fill: C_UP })
                + svgT(405, 240, '回调不破', { size: 11, fill: C_UP })
                + svgT(530, 240, '回踩不回中枢', { size: 11, fill: C_ACC })
                + svgT(170, 82, '中枢A', { size: 11, fill: C_ZS })
                + svgT(470, 88, '中枢B', { size: 11, fill: C_ZS })
                + svgT(120, 158, '顶背驰', { size: 11, fill: C_DN })
                + svgT(660, 40, 'S2/S3', { anchor: 'end', size: 11, fill: C_DIM })
                + svgT(660, 56, '为镜像', { anchor: 'end', size: 11, fill: C_DIM }));
        },
    },
];

const CHAN_IMPL = [
    ['K线包含处理', 'merge_klines()', '完整实现', '按方向合并，向上取高高、向下取低低'],
    ['顶/底分型', 'find_fractals()', '完整实现', '基于合并K线，含独立K线间隔校验'],
    ['笔', 'build_strokes()', '完整实现 + 过滤', '增加「最少成笔K线数」与最小幅度，过滤毛刺'],
    ['线段', 'build_segments()', '实用简化', '笔序列直接构建，不引入特征序列'],
    ['中枢', 'build_centers()', '完整实现', '输出 ZG/ZD 上下沿及重叠区间'],
    ['背驰', 'detect_divergence()', '完整实现 + 量化', 'MACD 柱面积比较 + 力度比阈值可调'],
    ['三类买点', 'find_signals()', '完整实现', 'B1/B2/B3，含止损位与置信度'],
    ['三类卖点', 'find_signals()', '完整实现', 'S1/S2/S3，与买点同一套逻辑'],
    ['多级别联立', 'to_weekly()', '部分实现', '支持周线视图确认日线信号的大级别位置'],
    ['区间套', '—', '未实现', '见下方说明'],
];

const CHAN_UPGRADE = [
    ['1', '线段构建去主观化',
        '原版用「特征序列 + 特征序列的包含处理」划分线段，是公认最容易因人而异、最难复现的部分。'
        + '本系统改为<b>由笔序列直接构建线段</b>：规则确定、任何人跑出同样结果，牺牲一点理论完备性换取可回测性。'],
    ['2', '无未来函数的回测',
        '回测时只使用「当时已确认」的结构（笔的结束位置 + 确认延迟 ≤ 当前位置）。'
        + '实现上先一次性算好完整结构，再按位置截断取用——既严格无偷价，又比逐根重算快约 100 倍。'],
    ['3', '次日开盘成交',
        '信号当日收盘才能确认，因此统一在<b>次日开盘</b>成交；若跳空，按<b>更不利</b>的价格成交。'
        + '杜绝了「用信号当日收盘价成交」这种常见却致命的虚假收益。'],
    ['4', '信号时效性过滤',
        '缠论笔的确认天然滞后，数月前产生的买卖点仍在图上会严重干扰判断。'
        + '本系统默认只展示<b>最近 45 个交易日</b>内的信号，陈旧点位自动隐藏。'],
    ['5', '参数自迭代 + 样本外验证',
        '70/30 时间分割，样本外不超越默认基线则<b>拒绝应用</b>；最少交易数门槛防止小样本侥幸。'
        + '并已修正「用测试集挑参数」的选择偏差——参数只依据样本内选出，样本外仅作通过/不通过判定。'],
    ['6', '量价共振（本系统独创叠加）',
        '缠论只看价格形态，对「资金是否认可」是盲的。本系统叠加 OBV / MFI / CMF 等资金维度，'
        + '与缠论买卖点同向时标注<b>「共振」</b>并提升置信度，弥补单一维度的缺陷。'],
    ['7', '信号 → 期权自动映射',
        '把买卖点翻译成具体的期权策略（买入认购 / 卖出认沽 / 备兑 / 价差等），'
        + '并给出希腊字母、理论定价与 IV/HV 对比，把「点位判断」延伸到「怎么下注」。'],
];

const CHAN_LIMITS = [
    ['区间套（多级别精确套嵌）', '仅提供日线/周线切换，未实现「用次级别背驰精确锁定本级别点位」的递归定位。周线目前只用于人工确认大级别位置。'],
    ['线段的特征序列划分', '已按升级第1条简化，与原版定义存在差异，极端行情下线段划分可能与人工判读不一致。'],
    ['中枢的扩展与扩张', '实现了基础中枢识别，未区分中枢延伸、扩展、扩张等细分形态。'],
    ['盘整背驰与趋势背驰的区分', '统一用力度比判断，未按原版严格区分两类背驰的不同处理方式。'],
    ['小级别（分钟线）', '数据源为日线，不支持分钟级别的多级别联立分析。'],
];

function renderChanTheory() {
    const cbox = $('#chanConcepts');
    if (cbox) {
        cbox.innerHTML = CHAN_CONCEPTS.map(([t, tag, d]) => `
            <div class="chan-card">
                <div class="chan-card-h"><b>${esc(t)}</b><span class="tag accent">${esc(tag)}</span></div>
                <div class="chan-card-b">${esc(d)}</div>
            </div>`).join('');
    }

    const dbox = $('#chanDiagrams');
    if (dbox && dbox.dataset.done !== '1') {
        dbox.innerHTML = CHAN_STEPS.map(s => `
            <div class="chan-step">
                <div class="chan-step-h">
                    <span class="chan-step-n">${s.n}</span>
                    <b>${esc(s.title)}</b>
                    <span class="tag purple">${esc(s.tag)}</span>
                </div>
                <div class="chan-step-svg">${s.svg()}</div>
                <div class="chan-step-d">${esc(s.desc)}</div>
                <div class="chan-step-note">${esc(s.note)}</div>
            </div>`).join('');
        dbox.dataset.done = '1';
    }

    const ibox = $('#chanImpl');
    if (ibox) {
        ibox.innerHTML = `<table class="tbl">
            <thead><tr>
                <th style="width:150px">缠论概念</th><th style="width:180px">本系统实现</th>
                <th style="width:130px">实现程度</th><th>说明</th>
            </tr></thead>
            <tbody>${CHAN_IMPL.map(([a, b, c, d]) => {
            const full = c.startsWith('完整');
            const none = c === '未实现';
            return `<tr>
                    <td style="font-weight:600">${esc(a)}</td>
                    <td class="dim" style="font-family:var(--mono);font-size:12px">${esc(b)}</td>
                    <td><span class="tag ${none ? 'neutral' : full ? 'buy' : 'warn'}">${esc(c)}</span></td>
                    <td class="dim">${esc(d)}</td>
                </tr>`;
        }).join('')}</tbody></table>`;
    }

    const ubox = $('#chanUpgrade');
    if (ubox) {
        ubox.innerHTML = CHAN_UPGRADE.map(([n, t, d]) => `
            <div class="chan-up">
                <div class="chan-up-n">${n}</div>
                <div class="chan-up-c">
                    <div class="chan-up-t">${esc(t)}</div>
                    <div class="chan-up-d">${d}</div>
                </div>
            </div>`).join('');
    }

    const lbox = $('#chanLimits');
    if (lbox) {
        lbox.innerHTML = CHAN_LIMITS.map(([t, d]) => `
            <div class="chan-lim">
                <b>${esc(t)}</b><span>${esc(d)}</span>
            </div>`).join('');
    }
}


// ---------------------------------------------------------------- 持仓盈亏
async function loadPositions() {
    const box = $('#posTable');
    const stats = $('#posStats');
    stats.innerHTML = '';
    box.innerHTML = `<div class="empty"><span class="spinner"></span><div class="msg" style="margin-top:12px">加载持仓…</div></div>`;
    try {
        const res = await api('/api/positions');
        renderPositions(res);
    } catch (e) {
        box.innerHTML = `<div class="empty"><div class="big">⚠️</div><div class="msg">${esc(e.message)}</div></div>`;
    }
}

function renderPositions(res) {
    const box = $('#posTable');
    const stats = $('#posStats');
    const s = res.summary;

    stats.innerHTML = s ? `
        <div class="stat"><div class="stat-label">持仓标的</div><div class="stat-value">${s.count}</div>
            <div class="stat-sub">只</div></div>
        <div class="stat"><div class="stat-label">总成本</div><div class="stat-value" style="font-size:21px">${money(s.cost)}</div></div>
        <div class="stat"><div class="stat-label">市值</div><div class="stat-value" style="font-size:21px">${money(s.market_value)}</div></div>
        <div class="stat"><div class="stat-label">浮动盈亏</div>
            <div class="stat-value ${cls(s.pnl)}">${s.pnl >= 0 ? '+' : ''}${money(s.pnl)}</div>
            <div class="stat-sub ${cls(s.pnl_pct)}">${fmtPct(s.pnl_pct)}</div></div>` : '';

    if (!res.items || !res.items.length) {
        box.innerHTML = `<div class="empty"><div class="big">▣</div>
            <div class="msg">暂无持仓</div>
            <div class="hint">在「股票池」中点击「持仓」填写股数与成本价后，此处会显示实时盈亏</div></div>`;
        return;
    }

    const rows = res.items.map(r => {
        const sigHtml = (r.signals || []).length
            ? r.signals.slice(0, 2).map(x => `<span class="tag ${x.side}">${x.code}</span>`).join(' ')
            : '<span class="muted">—</span>';
        return `<tr>
            <td><span class="sym-link" onclick="openDetail('${r.symbol}')">${r.symbol}</span>
                <div class="muted" style="font-size:11.5px">${esc(r.name)}</div></td>
            <td class="num">${r.qty}</td>
            <td class="num">${money(r.cost)}</td>
            <td class="num">${money(r.price)}</td>
            <td class="num">${money(r.market_value)}</td>
            <td class="num ${cls(r.pnl)}">${r.pnl >= 0 ? '+' : ''}${money(r.pnl)}</td>
            <td class="num ${cls(r.pnl_pct)}" style="font-weight:650">${fmtPct(r.pnl_pct)}</td>
            <td>${sigHtml}</td>
            <td style="font-size:12px;color:#a9b4c2;max-width:220px">${esc(r.advice)}</td>
            <td><button class="btn sm" onclick="openDetail('${r.symbol}')">分析</button>
                <button class="btn sm" onclick="openOptions('${r.symbol}')">期权</button></td>
        </tr>`;
    }).join('');

    box.innerHTML = `<div class="table-wrap"><table>
        <thead><tr><th>标的</th><th>股数</th><th>成本价</th><th>现价</th><th>市值</th>
            <th>盈亏额</th><th>盈亏%</th><th>缠论信号</th><th>操作建议</th><th>操作</th></tr></thead>
        <tbody>${rows}</tbody></table></div>`;
}


// ---------------------------------------------------------------- 信号复盘
async function loadReview(force = false) {
    const box = $('#reviewTable');
    const st = $('#reviewStats');
    st.innerHTML = `<div class="dim"><span class="spinner"></span> 正在回溯信号表现…</div>`;
    try {
        const res = force
            ? await api('/api/review/refresh', { method: 'POST' })
            : await api('/api/review');
        renderReview(res);
    } catch (e) {
        st.innerHTML = `<div class="down">${esc(e.message)}</div>`;
        box.innerHTML = '';
    }
}

function renderReview(res) {
    const st = $('#reviewStats');
    const box = $('#reviewTable');
    const H = res.horizons || [5, 10, 20];

    if (!res.total) {
        st.innerHTML = `<div class="empty" style="padding:26px"><div class="msg">暂无历史信号</div>
            <div class="hint">执行几次扫描后，系统会累积信号并自动评估其有效性</div></div>`;
        box.innerHTML = '';
        return;
    }

    const ov = (res.stats.overall || {})['全部'];
    if (ov) {
        const cards = H.map(h => {
            const wr = ov.win_rate[h];
            const ar = ov.avg_ret[h];
            const mfe = ov.avg_mfe[h];
            const mae = ov.avg_mae[h];
            return `<div class="stat">
                <div class="stat-label">${h} 个交易日后</div>
                <div class="stat-value" style="color:${wr >= 50 ? '#26a69a' : '#ef5350'}">${wr}%</div>
                <div class="stat-sub">胜率 · 基于 ${ov.count} 个信号</div>
                <div class="hr" style="margin:9px 0"></div>
                <div class="kv"><span class="k">平均收益</span><span class="v ${cls(ar)}">${fmtPct(ar)}</span></div>
                <div class="kv"><span class="k">最大有利</span><span class="v up">${fmtPct(mfe)}</span></div>
                <div class="kv"><span class="k">最大不利</span><span class="v down">${fmtPct(mae)}</span></div>
            </div>`;
        }).join('');
        st.innerHTML = `<div class="grid grid-3">${cards}</div>
            <div class="muted" style="margin-top:12px;font-size:12px">
                已评估 ${res.total - res.pending} 个信号${res.pending ? `，${res.pending} 个信号尚在观察期（数据不足）` : ''}
            </div>
            ${ov.count < 30 ? `<div class="tag warn" style="margin-top:10px">样本量偏小（${ov.count} 个），统计意义有限，仅作参数调优参考</div>` : ''}`;
    } else {
        st.innerHTML = `<div class="muted">信号尚在观察期，暂无足够后续数据。</div>`;
    }

    // 按类型分组
    const byCode = res.stats.by_code || {};
    const keys = Object.keys(byCode).sort();
    if (keys.length) {
        const rows = keys.map(k => {
            const v = byCode[k];
            return `<tr>
                <td><span class="tag ${k[0] === 'B' ? 'buy' : 'sell'}">${k}</span></td>
                <td class="num">${v.count}</td>
                ${H.map(h => `<td class="num" style="color:${(v.win_rate[h] ?? 0) >= 50 ? '#26a69a' : '#ef5350'};font-weight:650">${v.win_rate[h] ?? '—'}%</td>`).join('')}
                ${H.map(h => `<td class="num ${cls(v.avg_ret[h] ?? 0)}">${fmtPct(v.avg_ret[h])}</td>`).join('')}
                <td class="num up">${fmtPct(v.avg_mfe[H[1]])}</td>
                <td class="num down">${fmtPct(v.avg_mae[H[1]])}</td>
            </tr>`;
        }).join('');
        st.innerHTML += `<div class="hr"></div>
            <h4 style="font-size:13px;margin-bottom:9px" class="dim">分信号类型统计</h4>
            <div class="table-wrap"><table>
            <thead><tr><th>信号</th><th>样本数</th>
                ${H.map(h => `<th>${h}日胜率</th>`).join('')}
                ${H.map(h => `<th>${h}日均收益</th>`).join('')}
                <th>10日均MFE</th><th>10日均MAE</th></tr></thead>
            <tbody>${rows}</tbody></table></div>`;
    }

    // 明细
    const items = (res.items || []).filter(i => i.outcome);
    if (!items.length) {
        box.innerHTML = `<div class="empty" style="padding:30px"><div class="msg">信号仍在观察期</div>
            <div class="hint">需要信号出现后经过足够的交易日才能评估</div></div>`;
        return;
    }
    const rows = items.slice(0, 80).map(it => {
        const oc = it.outcome || {};
        return `<tr>
            <td><span class="sym-link" onclick="openDetail('${it.symbol}')">${it.symbol}</span></td>
            <td>${esc(it.sig_date)}</td>
            <td><span class="tag ${it.side}">${it.code}</span></td>
            <td class="num">${money(it.price)}</td>
            ${H.map(h => {
                const r = oc[`d${h}`];
                const w = oc[`win${h}`];
                if (r === null || r === undefined) return '<td class="num muted">—</td>';
                return `<td class="num ${cls(r)}">${fmtPct(r)}${w ? ' ✓' : ' ✗'}</td>`;
            }).join('')}
            <td class="num" style="color:#a9b4c2">${fmt(it.confidence * 100, 0)}%</td>
        </tr>`;
    }).join('');
    box.innerHTML = `<div class="table-wrap"><table>
        <thead><tr><th>标的</th><th>信号日期</th><th>类型</th><th>信号价</th>
            ${H.map(h => `<th>${h}日后</th>`).join('')}<th>置信度</th></tr></thead>
        <tbody>${rows}</tbody></table></div>`;
}

// ---------------------------------------------------------------- 版本号
/** 读取系统版本号显示在侧边栏（失败静默，不影响主流程） */
async function loadVersion() {
    const el = $('#appVersion');
    if (!el) return;
    try {
        const r = await api('/api/health');
        if (r && r.version) el.textContent = r.version;
    } catch (_) { /* 版本号拿不到也不影响使用 */ }
}

// ---------------------------------------------------------------- 初始化
document.addEventListener('DOMContentLoaded', () => {
    $$('.nav-item').forEach(n => n.addEventListener('click', () => go(n.dataset.page)));
    $$('#mktSwitch .mkt-tab').forEach(t =>
        t.addEventListener('click', () => setMarket(t.dataset.market)));
    $$('#wlFilter .chip').forEach(c => c.addEventListener('click', () => {
        $$('#wlFilter .chip').forEach(x => x.classList.remove('on'));
        c.classList.add('on');
        STATE.wlFilter = c.dataset.mkt || '';
        loadWatchlist();
    }));
    applyMarket();

    $('#btnScan').addEventListener('click', () => runScan(false));
    $('#btnMarketScan').addEventListener('click', () => runScan(true));
    $('#btnAdd').addEventListener('click', addSymbol);
    $('#newSymbol').addEventListener('keydown', e => { if (e.key === 'Enter') addSymbol(); });
    $('#btnBacktest').addEventListener('click', runBacktest);
    $('#btnOptimize').addEventListener('click', runOptimize);
    $('#opSplit').addEventListener('input', () => {
        $('#lblSplit').textContent = Math.round((parseFloat($('#opSplit').value) || 0.7) * 100);
    });
    $('#btnLoadOptions').addEventListener('click', loadOptions);
    $('#optSymbol').addEventListener('keydown', e => { if (e.key === 'Enter') loadOptions(); });
    $('#btnSaveSettings').addEventListener('click', saveSettings);
    $('#btnVpRefresh').addEventListener('click', () => loadVolprice(true));
    $('#btnHotRefresh').addEventListener('click', () => loadHotspots(true));
    $('#btnDetailBack').addEventListener('click', () => go(STATE.prevPage || 'dashboard'));
    $('#hotWindow').addEventListener('change', () => loadHotspots(true));
    $('#hotMode').addEventListener('change', () => loadHotspots(true));
    $('#btnSrcCompare').addEventListener('click', runSourceCompare);
    $('#vpThreshold').addEventListener('change', () => loadVolprice(true));
    $('#vpPeriod').addEventListener('change', () => loadVolprice(true));
    $('#vpSymbols').addEventListener('keydown', e => { if (e.key === 'Enter') loadVolprice(true); });
    $('#posSave').addEventListener('click', savePosition);
    $('#posCancel').addEventListener('click', () => $('#posModal').classList.remove('show'));
    $('#posModal').addEventListener('click', e => { if (e.target.id === 'posModal') e.currentTarget.classList.remove('show'); });

    $$('#stratFilter .chip').forEach(c => c.addEventListener('click', () => {
        STATE.strategyFilter = c.dataset.v;
        $$('#stratFilter .chip').forEach(x => x.classList.toggle('on', x === c));
        renderSignalTable(STATE.scanResults);
    }));

    window.addEventListener('resize', () => {
        if (STATE.page === 'detail' && STATE.detail) drawChart();
    });

    loadVersion();
    go('dashboard');
});
