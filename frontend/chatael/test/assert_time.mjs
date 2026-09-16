/**
 * 冻结判据断言 — 对象是 src/utils/time.ts 的真源码(经 esbuild 编译, 非重写)。
 * 判据来源: docs/plans/2026-09-15-chatael-message-timestamps.md 第 5 节。
 * 用法: node assert_time.mjs   (调用方分别以 TZ=UTC / TZ=Asia/Shanghai 跑两遍)
 */
import { parseMessageTime, formatMessageTime, formatFullTime } from './time.mjs';

const TZ = process.env.TZ || '(system)';
let pass = 0, fail = 0;
const t = (name, got, want) => {
  const ok = got === want;
  ok ? pass++ : fail++;
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${name.padEnd(46)} got=${String(got).padEnd(22)} want=${want}`);
};

console.log(`\n[TZ=${TZ}]`);

// 判据 1: 后端历史串(无时区, CST 墙钟, 6 位微秒) → 真实时刻 2026-09-12T03:23:23Z
const hist = parseMessageTime('2026-09-12 11:23:23.210514');
t('历史串 → UTC 瞬时', hist && hist.toISOString(), '2026-09-12T03:23:23.210Z');

// 判据 2: 本地消息(带 Z) 不被二次偏移
const local = parseMessageTime('2026-09-15T13:12:50.123Z');
t('本地串(带Z) 不二次偏移', local && local.toISOString(), '2026-09-15T13:12:50.123Z');

// 判据 3: 空/非法 → null 且不抛
for (const bad of [undefined, null, '', '   ', 'not-a-date', '2026-13-45 99:99:99']) {
  let got;
  try { const d = parseMessageTime(bad); got = d === null ? 'null' : 'Date(' + d.toISOString() + ')'; }
  catch (e) { got = 'THREW:' + e.message; }
  t(`非法输入 ${JSON.stringify(bad)} → null 且不抛`, got, 'null');
}

// 判据 4a: 显示格式规则 —— 与"访客本地日历"比较, 故样本用本地构造器构造
// (首轮此组曾误写为时区无关: 日期前缀规则天然依赖访客本地日期, 见 docs/plans 修订记录)
const now = new Date(2026, 8, 12, 12, 0, 0);
t('同日 → HH:MM', formatMessageTime(new Date(2026, 8, 12, 11, 23, 0), now), '11:23');
t('同日(跨小时) → HH:MM', formatMessageTime(new Date(2026, 8, 12, 0, 5, 0), now), '00:05');
t('同年他日 → M/D HH:MM', formatMessageTime(new Date(2026, 7, 1, 9, 5, 0), now), '8/1 09:05');
t('跨年 → Y/M/D HH:MM', formatMessageTime(new Date(2025, 11, 31, 23, 59, 0), now), '2025/12/31 23:59');

// 判据 4b: 时区正确性(本次修的就是这个) —— 同一后端串在不同访客时区必须显出
// 相差 8 小时的两个钟点(UTC 访客看到的是真实换算结果, 而非 CST 墙钟)。
const histClock = formatMessageTime(parseMessageTime('2026-09-12 11:23:23.210514'), now);
t('历史串按访客时区换算', histClock, TZ === 'UTC' ? '03:23' : '11:23');

// 判据 5: 悬浮完整时刻(数值格式, 与语言无关)
t('tooltip 完整时刻', formatFullTime(parseMessageTime('2026-09-12 11:23:23.210514')),
  TZ === 'UTC' ? '2026-09-12 03:23:23' : '2026-09-12 11:23:23');

// 判据 6: 跨天两条消息的时刻排序不因规范化而倒挂(必须出现日期前缀差异)
const a = parseMessageTime('2026-09-14 23:59:00'), b = parseMessageTime('2026-09-15 00:01:00');
t('跨天顺序不倒挂', a < b, true);
t('跨天两条渲染不同(带日期前缀)', formatMessageTime(a, b) !== formatMessageTime(b, b), true);

console.log(`  → 通过 ${pass} / ${pass + fail}`);
process.exit(fail ? 1 : 0);
