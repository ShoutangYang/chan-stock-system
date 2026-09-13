# 更新日志

## v1.0.0 · 2026-09-13

**初始版本入库：美股 + 港股通 缠论交易系统**

- **核心引擎**：`app/core/`
  - `chanlun.py` 缠论引擎（分型 / 笔 / 线段 / 中枢 / 背驰 / 三类买卖点）
  - `volprice.py` 量价引擎（五买五卖形态、枢轴点背离、ATR 止损目标）
  - `strategies.py` 超跌 / 趋势突破 / 底部反转 / 量价共振四类策略
  - `options.py` 期权链 + Black-Scholes + 策略映射，含「短期 / 中长期 × Put / Call」策略矩阵，
    每个策略带操作建议与操作理由
  - `hotspot.py` 市场热点板块（Chaikin A/D 资金流估算、活跃度、行业聚合）
  - `market.py` 市场层抽象：美股 / 港股通区分、代码规范化、106 只港股通标的池
  - `fundamental.py` 基本面分析（估值、历史估值对比、重要事件）
  - `indicators.py` 技术指标（MACD / RSI / BOLL / ADX / ATR / OBV / MFI / CMF）
  - `scanner.py` 并发扫描器、`review.py` 信号复盘、`backtest.py` 回测（无未来函数）、
    `btsource.py` 三信号源对比、`optimizer.py` 参数自迭代（样本外验证）
- **API 接口**：`app/main.py` FastAPI 服务，支持美股 / 港股双市场，
  新增 `/api/hotspots`、`/api/markets`、`/api/universe`、`/api/symbol/{sym}/fundamentals`
- **前端页面**：`app/web/` 原生 JS + 自绘 SVG K 线；市场切换器、热点板块（点击个股直接进分析）、
  个股详情页基本面面板（估值卡 / PE 带箱线 / 价格分位 / 公司行动 / 异动）
- **文档**：`README.md`（架构、数据源、参数说明、版本管理）
- **配置/脚本**：`start.py` 一键启动、`commit.py` 版本管理助手、`VERSION`、`CHANGELOG.md`、
  `.gitignore`、`.gitattributes`
- **测试**：`test_pages.js`（154 项断言）、`test_chart.js`（K 线渲染验证）

数据源说明：行情来自新浪（美股 / 港股实时报价）与腾讯（港股日 K，含公司行动），全部免费且无密钥；
港股历史 PE 序列、总市值、股息率在免费源中不可得，界面以「—」显示并在方法说明中标注，不编造数据。
