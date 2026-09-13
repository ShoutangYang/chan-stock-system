/**
 * 前端 K 线渲染逻辑的无浏览器验证：
 * 模拟最小 DOM，执行 app.js 中的 renderChart，检查生成的 SVG 是否包含预期图元。
 */
const fs = require('fs');
const path = require('path');

const srcPath = path.join(__dirname, 'app', 'web', 'app.js');
const src = fs.readFileSync(srcPath, 'utf8');

const start = src.indexOf('function renderChart');
if (start < 0) { console.error('未找到 renderChart'); process.exit(1); }
const end = src.indexOf('\n}\n', start);
const fnSrc = src.slice(start, end + 3);

// --- 最小 DOM 模拟 ---
function mockNode() {
    return {
        style: {},
        setAttribute() {}, getAttribute() { return null; },
        addEventListener() {},
        getBoundingClientRect() { return { left: 0, top: 0, width: 1200, height: 430 }; },
        appendChild() {},
        remove() {},
        classList: { add() {}, remove() {}, toggle() {} },
    };
}
global.document = {
    querySelector: () => mockNode(),
    querySelectorAll: () => [],
    createElement: () => mockNode(),
    addEventListener() {},
};

const STATE = {
    showStrokes: true, showCenters: true, showSignals: true, showMA: true, chartBars: 120,
};
global.STATE = STATE;

// $ 实现（供 renderChart 内部使用）
global.$ = () => mockNode();

const renderChart = eval(`(function () {\n${fnSrc}\nreturn renderChart;\n})()`);

const test = JSON.parse(fs.readFileSync(path.join(__dirname, 'data', 'testdata.json'), 'utf8'));
const el = { clientWidth: 1200, innerHTML: '' };

// 模拟前端 drawChart 的切片 + 索引偏移逻辑
const BARS = 120;
const klines = test.klines.slice(-BARS);
const off = test.klines.length - klines.length;
const chan = {
    ...test.chan,
    strokes: (test.chan.strokes || []).map(s => ({ ...s, start_raw: s.start_raw - off, end_raw: s.end_raw - off })),
    centers: (test.chan.centers || []).map(c => ({ ...c, start_raw: c.start_raw - off, end_raw: c.end_raw - off })),
    fractals: (test.chan.fractals || []).map(f => ({ ...f, raw_idx: f.raw_idx - off })),
    signals: (test.chan.signals || []).map(s => ({ ...s, raw_idx: s.raw_idx - off })),
};
console.log(`切片: ${klines.length} 根, 偏移 ${off}, 可见信号 ${chan.signals.filter(s => s.raw_idx >= 0 && s.raw_idx < klines.length).length} 个\n`);

try {
    renderChart(el, klines, chan, { height: 430 });
} catch (e) {
    console.error('❌ renderChart 抛异常:', e.message);
    console.error(e.stack.split('\n').slice(0, 4).join('\n'));
    process.exit(1);
}

const svg = el.innerHTML;
const checks = [
    ['SVG 根节点', /<svg[\s\S]*<\/svg>/.test(svg)],
    ['蜡烛图元 (rect)', (svg.match(/<rect /g) || []).length > 50],
    ['成交量柱', (svg.match(/rgba\(38,166,154,0\.42\)|rgba\(239,83,80,0\.42\)/g) || []).length > 50],
    ['笔 (stroke line)', /stroke="#4fd1c5"|stroke="#ff8a80"/.test(svg)],
    ['中枢 (rect dash)', /stroke-dasharray="3,2"/.test(svg)],
    ['均线路径', /<path d="M /.test(svg)],
    ['当前价标签', /fill="#4f8cff"/.test(svg)],
    ['日期轴标签', /font-family="monospace">\d{2}-\d{2}</.test(svg)],
    ['无 NaN 坐标', !/NaN/.test(svg)],
    ['无 undefined', !/undefined/.test(svg)],
];

let pass = 0;
console.log('K线渲染验证：');
for (const [name, ok] of checks) {
    console.log(`  ${ok ? '✓' : '✗'} ${name}`);
    if (ok) pass++;
}
console.log(`\nSVG 长度: ${svg.length} 字符`);

// 买卖点标记（若有信号）
const sigCount = (test.chan.signals || []).filter(s => s.raw_idx >= 0).length;
if (sigCount > 0) {
    const hasPoly = /<polygon/.test(svg);
    console.log(`  买卖点标记: 信号 ${sigCount} 个, 三角标记 ${hasPoly ? '✓ 已绘制' : '✗ 缺失'}`);
} else {
    console.log('  买卖点标记: 本次数据无信号（跳过）');
}

process.exit(pass === checks.length ? 0 : 1);
