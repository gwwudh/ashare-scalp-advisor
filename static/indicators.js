/**
 * 指标计算库：供主图叠加 / 副图面板使用
 */
(function (global) {
  function ema(values, period) {
    const out = new Array(values.length).fill(null);
    if (!values.length || period < 1) return out;
    const k = 2 / (period + 1);
    let prev = null;
    for (let i = 0; i < values.length; i++) {
      const v = values[i];
      if (v == null || Number.isNaN(v)) continue;
      if (i < period - 1) continue;
      if (prev == null) {
        let sum = 0;
        let ok = true;
        for (let j = i - period + 1; j <= i; j++) {
          if (values[j] == null) { ok = false; break; }
          sum += values[j];
        }
        if (!ok) continue;
        prev = sum / period;
      } else {
        prev = v * k + prev * (1 - k);
      }
      out[i] = prev;
    }
    return out;
  }

  function sma(values, period) {
    const out = new Array(values.length).fill(null);
    let sum = 0;
    for (let i = 0; i < values.length; i++) {
      const v = values[i];
      sum += v;
      if (i >= period) sum -= values[i - period];
      if (i >= period - 1) out[i] = sum / period;
    }
    return out;
  }

  function stdev(values, period, means) {
    const out = new Array(values.length).fill(null);
    for (let i = period - 1; i < values.length; i++) {
      const m = means[i];
      if (m == null) continue;
      let s = 0;
      for (let j = i - period + 1; j <= i; j++) {
        const d = values[j] - m;
        s += d * d;
      }
      out[i] = Math.sqrt(s / period);
    }
    return out;
  }

  function macd(closes, fast = 12, slow = 26, signal = 9) {
    const ef = ema(closes, fast);
    const es = ema(closes, slow);
    const dif = closes.map((_, i) =>
      ef[i] != null && es[i] != null ? ef[i] - es[i] : null
    );
    const dea = ema(dif.map((v) => (v == null ? 0 : v)), signal);
    // fix dea: only where dif valid — recompute properly
    const deaOut = new Array(closes.length).fill(null);
    const k = 2 / (signal + 1);
    let prev = null;
    let count = 0;
    for (let i = 0; i < dif.length; i++) {
      if (dif[i] == null) continue;
      count += 1;
      if (count < signal) continue;
      if (prev == null) {
        let sum = 0;
        let n = 0;
        for (let j = i; j >= 0 && n < signal; j--) {
          if (dif[j] == null) continue;
          sum += dif[j];
          n += 1;
        }
        if (n < signal) continue;
        prev = sum / signal;
      } else {
        prev = dif[i] * k + prev * (1 - k);
      }
      deaOut[i] = prev;
    }
    const hist = dif.map((v, i) =>
      v != null && deaOut[i] != null ? v - deaOut[i] : null
    );
    return { dif, dea: deaOut, hist };
  }

  function kdj(bars, n = 9, m1 = 3, m2 = 3) {
    const kArr = new Array(bars.length).fill(null);
    const dArr = new Array(bars.length).fill(null);
    const jArr = new Array(bars.length).fill(null);
    let k = 50;
    let d = 50;
    for (let i = 0; i < bars.length; i++) {
      if (i < n - 1) continue;
      let hh = -Infinity;
      let ll = Infinity;
      for (let j = i - n + 1; j <= i; j++) {
        hh = Math.max(hh, bars[j].high);
        ll = Math.min(ll, bars[j].low);
      }
      const rsv = hh === ll ? 50 : ((bars[i].close - ll) / (hh - ll)) * 100;
      k = (rsv + (m1 - 1) * k) / m1;
      d = (k + (m2 - 1) * d) / m2;
      const j = 3 * k - 2 * d;
      kArr[i] = k;
      dArr[i] = d;
      jArr[i] = j;
    }
    return { k: kArr, d: dArr, j: jArr };
  }

  function boll(closes, period = 20, mult = 2) {
    const mid = sma(closes, period);
    const sd = stdev(closes, period, mid);
    const upper = mid.map((m, i) => (m != null && sd[i] != null ? m + mult * sd[i] : null));
    const lower = mid.map((m, i) => (m != null && sd[i] != null ? m - mult * sd[i] : null));
    return { upper, mid, lower };
  }

  function rsi(closes, period = 14) {
    const out = new Array(closes.length).fill(null);
    if (closes.length < period + 1) return out;
    let gain = 0;
    let loss = 0;
    for (let i = 1; i <= period; i++) {
      const ch = closes[i] - closes[i - 1];
      if (ch >= 0) gain += ch;
      else loss -= ch;
    }
    let avgGain = gain / period;
    let avgLoss = loss / period;
    out[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
    for (let i = period + 1; i < closes.length; i++) {
      const ch = closes[i] - closes[i - 1];
      const g = ch > 0 ? ch : 0;
      const l = ch < 0 ? -ch : 0;
      avgGain = (avgGain * (period - 1) + g) / period;
      avgLoss = (avgLoss * (period - 1) + l) / period;
      out[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
    }
    return out;
  }

  /** 自定义：两均线差值 */
  function emaDiff(closes, fast = 5, slow = 20) {
    const a = ema(closes, fast);
    const b = ema(closes, slow);
    return a.map((v, i) => (v != null && b[i] != null ? v - b[i] : null));
  }

  function fillLeading(values) {
    const out = values.slice();
    let first = null;
    for (let i = 0; i < out.length; i++) {
      if (out[i] != null && !Number.isNaN(out[i])) {
        first = out[i];
        break;
      }
    }
    if (first == null) return out;
    for (let i = 0; i < out.length; i++) {
      if (out[i] == null || Number.isNaN(out[i])) out[i] = first;
      else break;
    }
    return out;
  }

  function toLine(times, values, opts) {
    // 空值用 whitespace 占位，保证与主图 K 线一一对齐
    // fillLeading：把指标预热期左侧空档用首个有效值填上，避免副图左侧空洞
    const vals = opts && opts.fillLeading ? fillLeading(values) : values;
    const out = [];
    for (let i = 0; i < times.length; i++) {
      const v = vals[i];
      if (v == null || Number.isNaN(v)) {
        out.push({ time: times[i] });
      } else {
        out.push({ time: times[i], value: v });
      }
    }
    return out;
  }

  function toHist(times, values, upColor, downColor, opts) {
    const vals = opts && opts.fillLeading ? fillLeading(values) : values;
    const out = [];
    for (let i = 0; i < times.length; i++) {
      const v = vals[i];
      if (v == null || Number.isNaN(v)) {
        out.push({ time: times[i] });
      } else {
        out.push({
          time: times[i],
          value: v,
          color: v >= 0 ? upColor : downColor,
        });
      }
    }
    return out;
  }

  global.IndCalc = {
    ema,
    sma,
    macd,
    kdj,
    boll,
    rsi,
    emaDiff,
    toLine,
    toHist,
  };
})(window);
