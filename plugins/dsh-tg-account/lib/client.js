window.__ModuleLoader__.load({
  id: "@local/dsh-tg-account",
  factory: function (require) {
    var module = { exports: {} };
    var React = require("react");
    var h = React.createElement;

    /*
     * 纪律：
     *  1. fail-open —— 桥不可达时只显示「¥--」，不抛错、不挡输入框。
     *  2. 不碰官方 DOM —— 只在自己的胶囊里渲染。
     *  3. 只读为主：唯一写操作是「切过去」按钮（新建会话 + 预填交接卡路径），
     *     且必须由用户点击触发；不做任何自动跳转。trim 开关写宿主文件。
     */
    var API = "/tg-account/api";
    var inject = ["slots"];
    /* apply() 里捕获的客户端根 ctx：切会话要用 uiWorkspace 服务，不放进 inject，
       免得服务缺失时整个胶囊加载不出来。 */
    var hostCtx = null;
    /* uiWorkspace 必须走 ctx.inject 才能拿到（cordis 服务隔离）：2026-10-29 修。
       之前直接 ctx.get("uiWorkspace") 拿不到，切换会话静默失败。 */
    var uiWorkspaceSvc = null;

    /* 当前会话 id（2026-10-29 修）：composer.dock 的 props 是空对象，URL 也常常没有
       session 段，旧实现直接退回 "default"，宿主只能按「最近写入的会话」猜——
       并行开两个会话时会记到别人头上（实测开关开到了另一个会话）。
       以 DSH 客户端 sessions 快照为准：current 优先，其次被主视图持有的那个。 */
    function currentSessionId() {
      try {
        var svc = getService("sessions");
        var snap = svc && svc.list && typeof svc.list.getSnapshot === "function" ? svc.list.getSnapshot() : null;
        if (!snap) return "";
        var cur = snap.current;
        if (typeof cur === "string" && cur) return cur;
        if (cur && typeof cur === "object" && typeof cur.id === "string" && cur.id) return cur.id;
        var byId = snap.byId || {};
        var ids = Object.keys(byId);
        for (var i = 0; i < ids.length; i++) {
          var row = byId[ids[i]];
          if (row && row.retainedBy && (row.retainedBy.mainView || 0) > 0) return ids[i];
        }
      } catch (e) {
        /* 拿不到就退回 URL/default */
      }
      return "";
    }

    function resolveSession(props) {
      try {
        if (props && typeof props.sessionId === "string" && props.sessionId) return props.sessionId;
        var cur = currentSessionId();
        if (cur) return cur;
        var m = String(location.search || "").match(/session=([^&]+)/);
        if (m) return decodeURIComponent(m[1]);
        m = String(location.hash || "").match(/session\/([0-9a-f-]{6,})/i);
        if (m) return m[1];
      } catch (e) {
        /* ignore */
      }
      return "default";
    }

    function fmtCny(v) {
      if (typeof v !== "number" || !isFinite(v)) return "¥--";
      if (v >= 10) return "¥" + v.toFixed(1);
      if (v >= 1) return "¥" + v.toFixed(2);
      return "¥" + v.toFixed(3);
    }

    function fmtK(n) {
      if (!n) return "0";
      return n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : Math.round(n / 1000) + "k";
    }

    /* 本会话省的钱都在一元以下，固定两位小数，别显示成 ¥0.655；
       正数不许被四舍五入成 ¥0.00（否则「省 ¥0.00 · 3 次」自相矛盾），低于半分写 ¥<0.01 */
    function fmt2(v) {
      if (typeof v !== "number" || !isFinite(v)) return "¥--";
      if (v > 0 && v < 0.005) return "¥<0.01";
      return "¥" + v.toFixed(2);
    }

    /* 面板口径（2026-10-30 用户要求）：钱一律 3 位小数，分厘级差异看得见 */
    function fmtCny3(v) {
      if (typeof v !== "number" || !isFinite(v)) return "—";
      if (v > 0 && v < 0.0005) return "¥<0.001";
      return "¥" + v.toFixed(3);
    }

    /* 命中率：缓存读 ÷ (输入 + 缓存读)，3 位小数百分比 */
    function fmtRate(v) {
      if (typeof v !== "number" || !isFinite(v)) return "—";
      return (v * 100).toFixed(3) + "%";
    }

    /* 本会话链：链首 + 转接出来的子会话（各自并上子代理子树）；单会话也算一条链 */
    function fmtChain(c) {
      if (!c) return "—";
      if (typeof c.cny !== "number") return c.stale ? "统计中…" : "—";
      return fmtCny3(c.cny) + " · " + c.n + " 会话";
    }

    function fmtChainSaved(c) {
      if (!c) return "—";
      if (typeof c.savedCny !== "number") return "统计中…";
      return fmtCny3(c.savedCny);
    }

    function chainTip(c) {
      if (!c) return "本会话链：宿主未返回链级账目";
      return "本会话链合计 " + fmtCny3(c.cny) +
        "（" + (c.n || 0) + "/" + (c.members || 0) + " 个会话进账）" +
        "\n省额 " + fmtCny3(c.savedCny) + "（" + (c.savedN || 0) + " 个会话）" +
        "\n口径：同一条血统链（链首 + 转接出的子会话，单会话也算一条链），各自含自己的子代理" +
        (c.stale ? "\n（索引重建中，稍后刷新）" : "");
    }

    /* 悬停只给金额：每个省钱模块一行，合计不重复写 */
    function linesTip(list) {
      return (list || [])
        .filter(function (x) { return x && typeof x.cny === "number"; })
        .map(function (x) { return x.label + " " + fmtCny(x.cny); })
        .join("\n");
    }

    function savedTip(s) {
      if (!s || typeof s.cny !== "number") return "累计节省：正在统计历史会话，约需 30 秒。";
      return linesTip([
        { label: "工具 schema", cny: s.schema },
        { label: "技能目录", cny: s.catalog },
        { label: "工具输出压缩", cny: s.comp },
      ]) || "暂无可计金额";
    }

    /* 悬停在「省」：同样只给各子模块金额；换会话是预测，单列一行 */
    function selfTip(d) {
      var s = d && d.savedSelf;
      if (!s) return "本次会话节省：尚未计算完成。";
      return linesTip([
        { label: "工具 schema", cny: s.schema },
        { label: "技能目录", cny: s.catalog },
        { label: "工具输出压缩", cny: s.comp },
        { label: "换会话 100 步（预测）", cny: d.saveNet100 },
      ]) || "暂无可计金额";
    }

    /* 字节数换算：逐级降，500 B 就说 500 B，别凑成 1 KB */
    function fmtBytes(b) {
      if (!b || typeof b !== "number" || !isFinite(b)) return "";
      if (b < 1024) return Math.round(b) + " B";
      const kb = Math.round(b / 1024);
      if (kb < 1024) return kb + " KB";
      return (b / 1048576).toFixed(1) + " MB";
    }

    /* 正数金额不四舍五入成 0：<¥0.001 写 ¥<0.001，否则「省 ¥0.000 · 3 次」自相矛盾 */
    function fmtMoney(v) {
      if (typeof v !== "number" || !isFinite(v) || v <= 0) return "";
      if (v >= 10) return "¥" + v.toFixed(1);
      if (v >= 1) return "¥" + v.toFixed(2);
      if (v >= 0.001) return "¥" + v.toFixed(3);
      return "¥<0.001";
    }

    /* 瘦身行（2026-10-27 用户要求）：数字取「全部对话合计」（d.saved），本会话值只进悬停。
       单个会话的压缩省额常在分厘级（4 次 = ¥0.01），看不出效果；累计 188 次才有说服力。
       累计账由宿主后台扫全部会话算出（约 30 秒 / 缓存 10 分钟）：没算完（pending）或扫出 0 个会话（解压全挂）
       都降级标「本次」，既不拿本会话数冒充合计，也不把「扫描失败」显示成「合计为 0」。 */
    function trimStat(d) {
      var agg = d && d.saved;
      var self = d && d.savedSelf;
      var selfOk = !!(self && typeof self.comp === "number");
      var selfLine = selfOk
        ? "本会话 压缩 " + fmt2(self.comp) + (self.compCount ? " · " + self.compCount + " 次" : "")
        : "本会话 压缩：统计中…";
      var basis = "口径：只算工具结果压缩（日志里的 tg-trim / save-token 通知）；7 条输出纪律只走提示词，无基线，不计金额";
      var aggOk = !!(agg && !agg.pending && typeof agg.comp === "number" && Number(agg.sessions) > 0);
      if (!aggOk) {
        return {
          text: selfOk
            ? "本次对话 省 " + fmt2(self.comp) + (self.compCount ? " · " + self.compCount + " 次" : "")
            : "统计中…",
          tip: "全部对话合计正在统计（首启约 30 秒）\n" + selfLine + "\n" + basis,
        };
      }
      var bytes = fmtBytes(agg.compBytes);
      var tail = bytes ? " · 省下 " + bytes : "";
      /* 宿主的累计账从 TG 改配置那天算起（SAVED_SINCE），不是开天辟地：「全部」必须带起点，否则是夸大 */
      var since = "";
      if (typeof agg.since === "number" && agg.since > 0) {
        var sd = new Date(agg.since);
        since = " · 自 " + (sd.getMonth() + 1) + "-" + sd.getDate() + " 起";
      }
      /* 缺 steps 时整段不写，别渲染成「0 步」这种自相矛盾的假数 */
      var steps = Number(agg.steps) > 0 ? " · " + agg.steps + " 步" : "";
      var total = typeof agg.cny === "number" ? agg.cny : null;
      var cfgCny = total === null ? 0 : total - agg.comp;
      var cfgTail = total !== null && cfgCny >= 0.0005
        ? "另：schema + 技能目录 累计省 " + fmtMoney(cfgCny) +
          "（气泡「累计省」" + fmtMoney(total) + " ≈ 压缩 + 这两项；气泡「省」只含本会话）"
        : "";
      return {
        text: agg.comp > 0
          ? "全部对话 省 " + fmtMoney(agg.comp) + (agg.compCount ? " · " + agg.compCount + " 次" : "")
          : "全部对话 暂无压缩",
        tip: [
          "全部对话合计（" + agg.sessions + " 个会话" + steps + since + "）",
          agg.comp > 0
            ? "工具输出压缩 " + fmtMoney(agg.comp) + (agg.compCount ? " · " + agg.compCount + " 次" : "") + tail
            : "工具输出压缩 暂未触发",
          selfLine,
          cfgTail,
          basis + (agg.stale ? "\n（缓存 10 分钟，后台重算中）" : ""),
        ].filter(Boolean).join("\n"),
      };
    }

    /* 悬停「今日」：说清口径（是花掉的钱合计，不是省下的钱），以及含几个会话 */
    function todayTip(t) {
      if (!t || typeof t.cny !== "number") return "今日消费：正在统计今天其他会话，约几秒。";
      var others = Math.max(0, (t.sessions || 1) - 1);
      return "今日消费合计 " + fmtCny(t.cny) +
        "\n本会话（含子代理）+ " + others + " 个其他会话/子代理 · 按本地日切" +
        (t.stale ? "\n（缓存 60 秒）" : "");
    }

    /* 悬停「今日省」：口径 = 只算今天的步与今天进上下文的量（配置反事实 + 工具输出压缩），不含预测 */
    function todaySavedTip(t) {
      if (!t || typeof t.saved !== "number") return "今日省：正在统计今天其他会话，约几秒。";
      var others = Math.max(0, (t.sessions || 1) - 1);
      return "今日省合计 " + fmtCny3(t.saved) +
        "\n口径：今天进上下文的 schema / 技能目录 / 工具输出压缩；本会话 + " + others +
        " 个其他会话/子代理 · 按本地日切" +
        (t.stale ? "\n（缓存 60 秒）" : "");
    }

    /* 悬停「本」：2026-10-27 起「本」= 本会话 + 它派出去的子代理。
       子代理是独立会话文件，钱烧在别的文件里，不并进来就会「本 ¥1.40 / 今日 ¥16」对不上账。
       超预算时把原因放最前（按单个子代理判，不是合计）。 */
    function subTip(d) {
      var self = d && typeof d.cny === "number" ? d.cny : 0;
      var s = d && d.subagents;
      var lines = ["本会话 " + fmtCny(self)];
      if (!s || !s.n) {
        lines.push("没有派子代理（本会话就是全部）");
        return lines.join("\n");
      }
      var a = s.alert;
      var b = s.budget || { steps: 40, cny: 1 };
      if (a && a.on) {
        lines.push("⚠ 超预算：" + (a.reasons || []).join(" + "));
        if (a.worst) lines.push("最重 " + a.worst.sid + " " + a.worst.steps + " 步 / " + fmtCny(a.worst.cny));
      }
      lines.push(s.n + " 个子代理 " + fmtCny(s.cny) + "（今日 " + fmtCny(s.today) + "）· 共 " + (s.steps || 0) + " 步");
      (s.list || []).slice(0, 6).forEach(function (k) {
        lines.push("  " + k.sid + "  " + (k.steps || 0) + " 步  " + fmtCny(k.cny));
      });
      if (s.n > 6) lines.push("  … 其余 " + (s.n - 6) + " 个");
      lines.push("预算：单个子代理 ≤" + b.steps + " 步、≤" + fmtCny(b.cny) + "（改 _local/tg-budget.json 可覆盖）");
      lines.push("口径：子代理费用记在独立会话文件，这里按 parentSession 归并");
      if (s.stale) lines.push("（树索引后台建立中，约几秒）");
      return lines.join("\n");
    }

    var PILL = {
      display: "inline-flex",
      alignItems: "center",
      gap: "6px",
      padding: "2px 9px",
      borderRadius: "999px",
      fontSize: "12px",
      lineHeight: "18px",
      fontVariantNumeric: "tabular-nums",
      background: "rgba(255,255,255,0.07)",
      color: "inherit",
      cursor: "pointer",
      border: "1px solid rgba(255,255,255,0.08)",
      userSelect: "none",
    };
    var PILL_HOT = Object.assign({}, PILL, {
      background: "#8c1d18",
      borderColor: "#b3261e",
      color: "#fff",
    });
    /* 子代理超预算（2026-10-27）：与「建议切换」的暗红分开——那个是省钱建议，这个是「已经超了」 */
    var PILL_ALERT = Object.assign({}, PILL, {
      background: "#6b4300",
      borderColor: "#c98a00",
      color: "#ffe6b8",
    });
    /* 已转接（2026-10-29）：对话已交出去，用冷蓝跟「建议切换」的暗红、超预算的琥珀区分开 */
    var PILL_DONE = Object.assign({}, PILL, {
      background: "#123a52",
      borderColor: "#2f7fa8",
      color: "#c9e9fb",
    });

    var PANEL = {
      position: "fixed",
      right: "24px",
      bottom: "68px",
      zIndex: 60,
      width: "360px",
      maxHeight: "62vh",
      overflow: "auto",
      background: "#1b1b20",
      color: "#e8e8ea",
      border: "1px solid rgba(255,255,255,0.14)",
      borderRadius: "10px",
      boxShadow: "0 12px 40px rgba(0,0,0,0.5)",
      padding: "12px 14px",
      fontSize: "12px",
    };

    function useAccount(session, ms) {
      var s = React.useState(null);
      var data = s[0];
      var setData = s[1];
      React.useEffect(function () {
        var alive = true;
        function tick() {
          fetch(API + "/cost?session=" + encodeURIComponent(session), { credentials: "omit", cache: "no-store" })
            .then(function (r) { return r.json(); })
            .then(function (j) { if (alive) setData(j && j.ok ? j : null); })
            .catch(function () { if (alive) setData(null); });
        }
        tick();
        var t = setInterval(tick, ms);
        return function () { alive = false; clearInterval(t); };
      }, [session, ms]);
      return data;
    }

    /*
     * 输出瘦身：全局一份策略（宿主写 _local/tg-trim.json，拼 system prompt 时现读），
     * 所以这里没有 session 参数；改完下一步就生效。
     */
    function useTrim() {
      var s = React.useState(null);
      var prof = s[0];
      var setProf = s[1];
      React.useEffect(function () {
        fetch(API + "/trim", { credentials: "omit", cache: "no-store" })
          .then(function (r) { return r.json(); })
          .then(function (j) { if (j && j.ok) setProf(j); })
          .catch(function () {});
      }, []);
      function save(patch) {
        var next = {
          master: typeof patch.master === "boolean" ? patch.master : (prof && prof.master),
          rules: Object.assign({}, (prof && prof.rules) || {}, patch.rules || {}),
        };
        setProf(function (old) {
          return Object.assign({}, old || {}, { master: next.master, rules: next.rules });
        });
        fetch(API + "/trim", {
          method: "POST",
          headers: { "content-type": "application/json" },
          credentials: "omit",
          body: JSON.stringify(patch),
        })
          .then(function (r) { return r.json(); })
          .then(function (j) { if (j && j.ok) setProf(j); })
          .catch(function () {});
      }
      function flipMaster() {
        save({ master: !(prof && prof.master) });
      }
      function flipRule(key) {
        var rules = {};
        rules[key] = !(prof && prof.rules && prof.rules[key] !== false);
        save({ rules: rules });
      }
      return [prof, flipMaster, flipRule];
    }

    /*
     * 一键复制交接卡：正文从宿主 /tg-account/api/card 取（浏览器读不到本地文件）。
     * 优先 navigator.clipboard（Electron 自定义协议下不一定算安全上下文），失败退 textarea + execCommand。
     */
    function copyText(text) {
      return new Promise(function (resolve, reject) {
        function fallback() {
          try {
            var ta = document.createElement("textarea");
            ta.value = text;
            ta.setAttribute("readonly", "");
            ta.style.position = "fixed";
            ta.style.left = "-9999px";
            ta.style.top = "0";
            document.body.appendChild(ta);
            ta.select();
            ta.setSelectionRange(0, text.length);
            var ok = document.execCommand("copy");
            document.body.removeChild(ta);
            if (ok) resolve();
            else reject(new Error("execCommand 返回 false"));
          } catch (e) {
            reject(e);
          }
        }
        try {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(resolve, fallback);
            return;
          }
        } catch (e) {
          /* 直接走兜底 */
        }
        fallback();
      });
    }

    var BTN = {
      cursor: "pointer",
      flex: "0 0 auto",
      borderRadius: "6px",
      border: "1px solid rgba(255,255,255,0.22)",
      background: "rgba(255,255,255,0.08)",
      color: "#fff",
      padding: "1px 8px",
      fontSize: "11px",
      lineHeight: "16px",
      whiteSpace: "nowrap",
    };
    var BTN_OK = Object.assign({}, BTN, {
      background: "#2e7d32",
      borderColor: "#43a047",
      cursor: "default",
    });
    var BTN_BAD = Object.assign({}, BTN, { background: "#8c1d18", borderColor: "#b3261e" });

    function shortPath(p) {
      var parts = String(p || "").replace(/\\/g, "/").split("/").filter(Boolean);
      return parts.slice(-2).join("/");
    }

    /* ---------- 母会话血统：侧栏行前面的色点 + 顺序号 ----------
       数据来自宿主 /tg-account/api/lineage（任务夹的 01-<sid8>.md / 99-cards.json，
       加上「切过去」时显式记的 parent）。同组同色，n 是链上第几个。 */
    var lineageState = { map: null, at: 0, subs: [] };

    function lineageRefresh(force) {
      var now = Date.now();
      if (!force && now - lineageState.at < 60000) return;
      lineageState.at = now;
      fetch(API + "/lineage", { credentials: "omit" })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          if (!j || !j.ok || !j.map) return;
          lineageState.map = j.map;
          lineageState.subs.slice().forEach(function (f) {
            try { f(); } catch (e) { /* 单个订阅挂了不影响别人 */ }
          });
          if (orderEnabled()) {
            try { applyChainOrder(lineageState.map); } catch (e) { /* 排序失败不影响标记 */ }
          }
        })
        .catch(function () { /* 桥不可达就不标 */ });
    }

    function useLineage() {
      var s = React.useState(0);
      var bump = s[1];
      React.useEffect(function () {
        var f = function () { bump(function (x) { return x + 1; }); };
        lineageState.subs.push(f);
        lineageRefresh(false);
        return function () {
          lineageState.subs = lineageState.subs.filter(function (g) { return g !== f; });
        };
      }, []);
      return lineageState.map || {};
    }

    /* ---------- 自动聚合排序 ----------
       同一母会话的行挨在一起；链内方向可切（顺叙=母在上 / 倒叙=最近的先，2026-10-26 加）。
       官方没有「分组」接口，唯一写入口是手动排序 uiWorkspace.view.setSessionOrder
       （dsh-client-ui-workspace:1314），所以只有存在 ≥2 成员的链时才动手；
       关掉开关会切回「最近更新」。每次只用「链成员变化」触发，避免跟用户拖拽打架。 */
    var ORDER_ON_KEY = "tg-lineage-order:on";
    var ORDER_SIG_KEY = "tg-lineage-order:sig2";
    var ORDER_DIR_KEY = "tg-lineage-order:dir";

    function orderEnabled() {
      try { return localStorage.getItem(ORDER_ON_KEY) !== "0"; } catch (e) { return true; }
    }

    /* 链内方向（2026-10-26 加）：asc = 顺叙（母在上、子往下），desc = 倒叙（最近的先） */
    function orderDir() {
      try { return localStorage.getItem(ORDER_DIR_KEY) === "desc" ? "desc" : "asc"; } catch (e) { return "asc"; }
    }

    function setOrderDir(v) {
      try { localStorage.setItem(ORDER_DIR_KEY, v === "desc" ? "desc" : "asc"); } catch (e) { /* 存不了就只影响本次 */ }
      lineageState.subs.slice().forEach(function (f) { try { f(); } catch (e) {} });
      applyChainOrder(lineageState.map || {});
    }

    function setOrderEnabled(on) {
      try { localStorage.setItem(ORDER_ON_KEY, on ? "1" : "0"); } catch (e) { /* 存不了就只影响本次 */ }
      lineageState.subs.slice().forEach(function (f) { try { f(); } catch (e) {} });
      if (!on) {
        var uw = getService("uiWorkspace");
        try {
          if (uw && uw.view && typeof uw.view.setOrderBy === "function") uw.view.setOrderBy("updated", {});
        } catch (e) { /* 恢复不了就留在手动排序，用户自己切 */ }
        return;
      }
      applyChainOrder(lineageState.map || {});
    }

    function readSig() {
      try { return localStorage.getItem(ORDER_SIG_KEY) || ""; } catch (e) { return ""; }
    }

    function writeSig(v) {
      try { localStorage.setItem(ORDER_SIG_KEY, v); } catch (e) { /* 无所谓 */ }
    }

    /* 纯函数：给定成员、血统表、时间取值器、方向，算出「链整块、链内顺叙/倒叙」的顺序；没有链返回 null */
    function planOrder(ids, map, atFn, dir) {
      var way = dir === "desc" ? "desc" : "asc";
      var chains = {};
      ids.forEach(function (id) {
        var hit = map[sid8Of(id)];
        if (!hit) return;
        (chains[hit.g] = chains[hit.g] || []).push(id);
      });
      var groups = Object.keys(chains).filter(function (g) { return chains[g].length >= 2; });
      if (!groups.length) return null;
      var base = ids.slice().sort(function (a, b) { return (atFn(b) || 0) - (atFn(a) || 0); });
      groups.forEach(function (g) {
        var members = chains[g].slice().sort(function (a, b) {
          var d = ((map[sid8Of(a)] || {}).n || 0) - ((map[sid8Of(b)] || {}).n || 0);
          return way === "desc" ? -d : d;
        });
        var spots = members.map(function (m) { return base.indexOf(m); }).filter(function (i) { return i >= 0; });
        if (!spots.length) return;
        var head = Math.min.apply(null, spots);
        base = base.filter(function (id) { return members.indexOf(id) < 0; });
        base.splice.apply(base, [head, 0].concat(members));
      });
      return base;
    }

    /* 每个工作区各自算：链成员按「最近的先」整块插到原本最靠前那个成员的位置 */
    function applyChainOrder(map) {
      var uw = getService("uiWorkspace");
      var ws = getService("workspaces");
      var sessions = getService("sessions");
      if (!uw || !uw.view || typeof uw.view.setSessionOrder !== "function") return false;
      if (!ws || !ws.list || !sessions || !sessions.list) return false;
      var snapW;
      var snapS;
      try {
        snapW = ws.list.getSnapshot();
        snapS = sessions.list.getSnapshot();
      } catch (e) {
        return false;
      }
      if (!snapW || !snapW.items || !snapS || !snapS.byId) return false;
      var applied = false;
      var dir = orderDir();
      snapW.items.forEach(function (item) {
        var ids = (item && item.sessionIds) || [];
        if (!item || !item.workspaceId || ids.length < 2) return;
        var base = planOrder(ids, map, function (id) {
          var row = snapS.byId[id];
          return (row && row.updatedAt) || 0;
        }, dir);
        if (!base) return;
        var sig = dir + "|" + item.workspaceId + "|" + base.join(",");
        if (readSig() === sig) return;
        try {
          uw.view.setSessionOrder(item.workspaceId, base, {});
          writeSig(sig);
          applied = true;
        } catch (e) {
          /* 官方接口形态变了就放弃，只留色点 */
        }
      });
      return applied;
    }

    function sid8Of(v) {
      return String(v || "").replace(/^session-/, "").replace(/[^0-9a-zA-Z-]/g, "").slice(0, 8);
    }

    /* 同一组固定同一个色相：组名字符串哈希，跨会话稳定（旧画法，保留给面板图例） */
    function hueOf(key) {
      var h = 0;
      key = String(key || "");
      for (var i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) % 360;
      return h;
    }

    /* ---------- 标识样式（2026-10-26）----------
       行右上角一个小角标，不带数字；颜色/形态按「链」统一，存 localStorage。
       默认分配：同屏各链的颜色与形态互不重复，且首次分配后立刻落盘，之后不再漂。 */
    var MARK_KEY = "tg-marker-style:v1";
    var MARK_COLORS = ["#4f9cff", "#39c07a", "#e0a03c", "#e06a6a", "#a97ce8", "#2fbcc4", "#d4708f", "#8fb43c", "#e07f3c", "#6d86f0"];
    var MARK_SHAPES = ["triangle", "slash", "dot", "square"];
    var MARK_SHAPE_NAMES = { triangle: "三角", slash: "斜线", dot: "圆点", square: "方块" };
    var markSubs = [];

    function readMark() {
      try {
        var d = JSON.parse(localStorage.getItem(MARK_KEY) || "{}");
        if (!d || typeof d !== "object") return { groups: {} };
        d.groups = d.groups || {};
        return d;
      } catch (e) { return { groups: {} }; }
    }
    function writeMark(d) {
      try { localStorage.setItem(MARK_KEY, JSON.stringify(d)); } catch (e) { /* 存不了就本次用默认 */ }
    }
    function hashIdx(s, n) {
      var h = 0;
      s = String(s || "");
      for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 1000003;
      return h % n;
    }
    function styleFor(map) {
      var d = readMark();
      var used = {};
      var groups = [];
      Object.keys(map || {}).forEach(function (k) {
        var g = map[k] && map[k].g;
        if (g && groups.indexOf(g) < 0) groups.push(g);
      });
      groups.sort();
      var out = {};
      var changed = false;
      groups.forEach(function (g) {
        var hit = d.groups[g];
        if (!hit || !hit.color || !hit.shape) {
          var ci = hashIdx(g, MARK_COLORS.length);
          var si = hashIdx(g + "|shape", MARK_SHAPES.length);
          var pick = null;
          for (var t = 0; t < MARK_COLORS.length * MARK_SHAPES.length && !pick; t++) {
            var c = MARK_COLORS[(ci + t) % MARK_COLORS.length];
            var s = MARK_SHAPES[(si + Math.floor(t / MARK_COLORS.length)) % MARK_SHAPES.length];
            if (!used[c + "|" + s]) pick = { color: c, shape: s };
          }
          hit = pick || { color: MARK_COLORS[ci], shape: MARK_SHAPES[si] };
          d.groups[g] = hit;
          changed = true;
        }
        used[hit.color + "|" + hit.shape] = true;
        out[g] = hit;
      });
      if (changed) writeMark(d);
      return out;
    }
    function setStyle(group, patch) {
      var d = readMark();
      var cur = d.groups[group] || {};
      d.groups[group] = {
        color: (patch && patch.color) || cur.color || MARK_COLORS[0],
        shape: (patch && patch.shape) || cur.shape || MARK_SHAPES[0],
      };
      writeMark(d);
      markSubs.slice().forEach(function (f) { try { f(); } catch (e) {} });
    }
    function useMarkStyles(map) {
      var st = React.useState(0);
      var bump = st[1];
      React.useEffect(function () {
        var f = function () { bump(function (x) { return x + 1; }); };
        markSubs.push(f);
        return function () { markSubs = markSubs.filter(function (g) { return g !== f; }); };
      }, []);
      return styleFor(map);
    }

    /* 角标（内联版）：给 ⋯ 菜单里的形态预览用 */
    function cornerMark(color, shape) {
      var base = { position: "relative", display: "inline-block", flex: "0 0 auto", marginRight: "4px", lineHeight: 0 };
      if (shape === "dot") return h("span", { style: Object.assign({}, base, { width: "6px", height: "6px", borderRadius: "50%", background: color }) });
      if (shape === "square") return h("span", { style: Object.assign({}, base, { width: "6px", height: "6px", borderRadius: "1px", background: color }) });
      if (shape === "slash") return h("span", { style: Object.assign({}, base, { width: "10px", height: "10px", borderTop: "2px solid " + color, transform: "rotate(45deg)" }) });
      return h("span", { style: Object.assign({}, base, { width: "0px", height: "0px", borderTop: "9px solid " + color, borderRight: "9px solid transparent" }) });
    }

    /* 侧栏行的链标：官方 leading 槽（16×20）会被「进行中」的转圈圆点整格顶掉——
       ui-workspace 里是 `showStatus ? <SessionStatusDots/> : renderSlot("sidebar.session.row.leading")`，
       槽一旦被状态点占用，挂在槽里的角标连挂载机会都没有。
       所以不再往槽里塞，改成在行上直接钉一个绝对定位小点，落进行尾的 8px 左内边距，
       与槽里的图标（8px 起）互不遮挡（2026-10-29 定）。 */
    function markCss(stl) {
      var c = stl && stl.color ? stl.color : MARK_COLORS[0];
      var shape = (stl && stl.shape) || MARK_SHAPES[0];
      var base = "position:absolute;left:1px;top:50%;pointer-events:none;z-index:5;";
      if (shape === "slash") return base + "width:8px;height:8px;margin-top:-5px;border-top:2px solid " + c + ";transform:rotate(45deg);";
      if (shape === "square") return base + "width:6px;height:6px;margin-top:-3px;border-radius:1px;background:" + c + ";";
      if (shape === "dot") return base + "width:6px;height:6px;margin-top:-3px;border-radius:50%;background:" + c + ";";
      return base + "width:0;height:0;margin-top:-4px;border-top:8px solid " + c + ";border-right:8px solid transparent;";
    }

    function rowSid(row) {
      var k = row && row.getAttribute ? row.getAttribute("data-row-key") : "";
      if (!k || k.indexOf("session:") !== 0) return "";
      return sid8Of(k.slice(8));
    }

    function applyChainMarks() {
      var map = lineageState.map || null;
      var styles = styleFor(map || {});
      var rows = [];
      try { rows = document.querySelectorAll('[data-row-key^="session:"]'); } catch (e) { return; }
      for (var i = 0; i < rows.length; i++) {
        var row = rows[i];
        var sid = rowSid(row);
        var hit = sid && map ? map[sid] : null;
        var node = null;
        for (var j = 0; j < row.children.length; j++) {
          var ch = row.children[j];
          if (ch.getAttribute && ch.getAttribute("data-tg-chain-mark") === "1") { node = ch; break; }
        }
        if (!hit) {
          if (node && node.parentNode) node.parentNode.removeChild(node);
          continue;
        }
        if (!node) {
          node = document.createElement("span");
          node.setAttribute("data-tg-chain-mark", "1");
          row.appendChild(node);
        }
        try { if (getComputedStyle(row).position === "static") row.style.position = "relative"; } catch (e) { /* 拿不到样式就用行的包含块 */ }
        var stl = styles[hit.g] || { color: MARK_COLORS[0], shape: MARK_SHAPES[0] };
        node.style.cssText = markCss(stl);
        node.title = "同一母会话 " + hit.g + " · 第 " + hit.n + " / " + hit.t + " 个 · " + MARK_SHAPE_NAMES[stl.shape];
      }
      /* 容器可能刚被 React 换掉（或安装时还没渲染出来）：每次落完标顺手确认一次观察目标 */
      armChainObs();
    }

    /* 行会随列表刷新重建：既跟 DOM 变更走，也跟血统轮询走。
       2026-10-29 优化：观察范围从 document.body 收窄到「侧栏行容器」。body 整棵子树在流式输出时
       每条消息都在变，MutationObserver 回调会被打成一片（每次都要过一遍 childList 记录）；
       侧栏行只在列表/排序变化时才重建，回调量级小一到两个数量级。容器被 React 换掉时自动重挂。 */
    function marksContainer() {
      try {
        var row = document.querySelector('[data-row-key^="session:"]');
        return row && row.parentElement ? row.parentElement : null;
      } catch (e) {
        return null;
      }
    }

    var chainObs = null;
    var chainObsRoot = null;
    var chainPend = 0;

    function scheduleChainMarks() {
      if (chainPend) return;
      chainPend = setTimeout(function () {
        chainPend = 0;
        try {
          applyChainMarks();
        } catch (e) {
          /* 正在重渲染时拿不到 DOM：等 4s 那一轮补 */
        }
      }, 80);
      if (chainPend && typeof chainPend.unref === "function") chainPend.unref();
    }

    function armChainObs() {
      if (typeof MutationObserver === "undefined") return;
      var root = marksContainer();
      if (!root || root === chainObsRoot) return;
      if (chainObs) {
        try { chainObs.disconnect(); } catch (e) { /* 已经断开 */ }
      }
      chainObsRoot = root;
      try {
        chainObs = new MutationObserver(scheduleChainMarks);
        chainObs.observe(root, { childList: true, subtree: true });
      } catch (e) {
        chainObs = null;
        chainObsRoot = null;
      }
    }

    /* 行会随列表刷新重建：既跟 DOM 变更走，也跟血统轮询走 */
    function installChainMarks() {
      if (installChainMarks._on) return;
      if (typeof document === "undefined" || !document.body) return; // 纯 Node 里跑测试时不启动
      installChainMarks._on = true;
      applyChainMarks();
      armChainObs();
      var iv1 = setInterval(applyChainMarks, 4000);
      var iv2 = setInterval(function () { lineageRefresh(false); }, 30000);
      if (iv1 && typeof iv1.unref === "function") iv1.unref();
      if (iv2 && typeof iv2.unref === "function") iv2.unref();
      lineageRefresh(true);
      markSubs.push(applyChainMarks);
      lineageState.subs.push(applyChainMarks);
    }

    /* ⋯ 菜单里的一行：本链标识的颜色 / 形态（点开就地改） */
    function MarkerMenuRow(props) {
      var map = useLineage();
      var styles = useMarkStyles(map);
      var menuApi = null;
      try { menuApi = props && typeof props.useMenuOpenState === "function" ? props.useMenuOpenState() : null; } catch (e) { menuApi = null; }
      var st = React.useState(false);
      var open = st[0];
      var setOpen = st[1];
      var key = sid8Of(props && props.sessionId);
      var hit = key ? map[key] : null;
      if (!hit) return null;
      var cur = styles[hit.g] || { color: MARK_COLORS[0], shape: MARK_SHAPES[0] };
      var rowStyle = {
        display: "flex", alignItems: "center", gap: "6px", width: "100%",
        padding: "4px 8px", background: "none", border: "0", borderRadius: "4px",
        color: "inherit", font: "inherit", fontSize: "12px", textAlign: "left", cursor: "pointer",
      };
      var swatch = function (c) {
        return h("button", {
          key: c, type: "button", title: c,
          onClick: function (ev) { ev.stopPropagation(); setStyle(hit.g, { color: c }); },
          style: {
            width: "14px", height: "14px", borderRadius: "50%", padding: 0, cursor: "pointer",
            background: c, border: cur.color === c ? "2px solid #fff" : "1px solid rgba(255,255,255,0.25)",
            boxShadow: cur.color === c ? "0 0 0 1px " + c : "none",
          },
        });
      };
      var shapeBtn = function (s) {
        var on = cur.shape === s;
        return h("button", {
          key: s, type: "button", title: MARK_SHAPE_NAMES[s],
          onClick: function (ev) { ev.stopPropagation(); setStyle(hit.g, { shape: s }); },
          style: {
            display: "inline-flex", alignItems: "center", justifyContent: "center",
            width: "20px", height: "20px", padding: 0, borderRadius: "4px", cursor: "pointer",
            background: on ? "rgba(255,255,255,0.14)" : "transparent",
            border: on ? "1px solid rgba(255,255,255,0.35)" : "1px solid transparent",
          },
        }, cornerMark(cur.color, s, false));
      };
      return h("div", { style: { display: "flex", flexDirection: "column", gap: "4px", width: "100%" } },
        h("button", {
          type: "button", role: "menuitem", "aria-expanded": open ? "true" : "false",
          onClick: function (ev) {
            ev.stopPropagation();
            setOpen(function (v) { return !v; });
          },
          style: rowStyle,
        }, h("span", { style: { flex: "1 0 auto" } }, "标识颜色与形态"),
          h("span", { style: { display: "inline-flex", alignItems: "center", gap: "4px", opacity: 0.8 } },
            cornerMark(cur.color, cur.shape, false),
            h("span", { style: { fontSize: "11px" } }, MARK_SHAPE_NAMES[cur.shape]),
            h("span", { style: { fontSize: "10px", opacity: 0.7 } }, open ? "▴" : "▾"))),
        open ? h("div", {
          style: { display: "flex", flexDirection: "column", gap: "6px", padding: "4px 8px 8px" },
          onClick: function (ev) { ev.stopPropagation(); },
        },
          h("div", { style: { display: "flex", flexWrap: "wrap", gap: "6px" } }, MARK_COLORS.map(swatch)),
          h("div", { style: { display: "flex", gap: "6px", alignItems: "center" } },
            MARK_SHAPES.map(shapeBtn),
            h("span", { style: { flex: "1 0 auto" } }),
            h("button", {
              type: "button",
              onClick: function (ev) {
                ev.stopPropagation();
                var d = readMark();
                delete d.groups[hit.g];
                writeMark(d);
                markSubs.slice().forEach(function (f) { try { f(); } catch (e) {} });
              },
              style: { background: "none", border: "0", color: "inherit", opacity: 0.7, fontSize: "11px", cursor: "pointer", padding: 0 },
            }, "恢复默认"),
            h("button", {
              type: "button",
              onClick: function (ev) {
                ev.stopPropagation();
                setOpen(false);
                try { if (menuApi && typeof menuApi[1] === "function") menuApi[1](false); } catch (e) { /* 菜单关不掉就算了 */ }
              },
              style: { background: "none", border: "0", color: "inherit", opacity: 0.7, fontSize: "11px", cursor: "pointer", padding: 0 },
            }, "完成"))) : null);
    }


    /* 切过去之后把「子 ← 母」记进宿主血统档：等新会话出现在列表里认领（10 次 × 0.5s） */
    function claimNewSession(sessions, before, parentSid) {
      if (!sessions || !sessions.list || typeof sessions.list.getSnapshot !== "function") return;
      var tries = 0;
      var timer = setInterval(function () {
        tries += 1;
        var ids = [];
        try {
          var snap = sessions.list.getSnapshot();
          ids = Object.keys((snap && snap.byId) || {});
        } catch (e) {
          ids = [];
        }
        var fresh = ids.filter(function (id) { return !before[id]; });
        if (fresh.length >= 1) {
          clearInterval(timer);
          fetch(API + "/lineage", {
            method: "POST",
            headers: { "content-type": "application/json" },
            credentials: "omit",
            body: JSON.stringify({ child: fresh[0], parent: parentSid }),
          })
            .then(function () { lineageRefresh(true); })
            .catch(function () { /* 记不上就等卡片兜底 */ });
          return;
        }
        if (tries >= 10) clearInterval(timer);
      }, 500);
    }

    /* 取客户端服务：优先 ctx.get，退回属性访问；都拿不到返回 null（fail-open） */
    function getService(name) {
      if (name === "uiWorkspace" && uiWorkspaceSvc) return uiWorkspaceSvc;
      try {
        if (hostCtx && typeof hostCtx.get === "function") {
          var s = hostCtx.get(name);
          if (s) return s;
        }
      } catch (e) {
        /* 继续退属性 */
      }
      try {
        return hostCtx ? hostCtx[name] || null : null;
      } catch (e) {
        return null;
      }
    }

    /* 转接判定（纯函数，给测试用）：返回去重 key 或 null。
       条件 = 该切（shouldSplit）+ 开关开 + 最后一轮正常完成 + 晚于武装点 + 交接卡就绪。
       注意：这里只「标记待转接」，真正的切换发生在用户下一次按发送时。 */
    function shouldTransfer(asw, session, hot) {
      if (!hot) return null;
      if (!asw || typeof asw !== "object" || !asw.on) return null;
      var te = asw.lastTurnEnd;
      if (!te || te.kind !== "completed") return null;
      if (typeof asw.armedTurn !== "number" || typeof te.turn !== "number") return null;
      if (!(te.turn > asw.armedTurn)) return null;
      if (!asw.cardReady) return null;
      return String(session || "") + ":" + te.turn;
    }

    /* 新会话首条消息：交接卡路径一行 + 用户这次输入（2026-10-29 用户定）。
       不内联卡全文（省 token，输入框也不爆），也不附复述要求。 */
    function buildSwitchPrompt(sid, card, userText) {
      var sid8 = String(sid || "").replace(/^session-/, "").slice(0, 8);
      var base = "接续上一个会话（" + sid8 + "）。先读交接卡：" + card.path;
      var extra = String(userText || "").trim();
      return extra ? base + "\n\n" + extra : base;
    }

    /* 从输入框往上找主 composer（容器里有我们的胶囊）；找不到就不是主输入框 */
    function inMainComposer(el) {
      var box = el;
      for (var i = 0; i < 8 && box; i++) {
        box = box.parentElement;
        if (!box) return false;
        try {
          if (box.querySelector('[data-composer-stat="tg-account"]')) return true;
        } catch (e) {
          return false;
        }
      }
      return false;
    }

    /* 新建会话 + 打开 + 预填输入框。不自动发送，按回车才花那一轮钱。
       uiWorkspace.startSession 内部自己 catch 失败（只 console.warn），
       所以这里的成功回调只代表「已发起」，不代表落地成功。 */
    function switchToNewSession(session, userText, done) {
      var ws = getService("uiWorkspace");
      if (!ws || typeof ws.startSession !== "function") {
        done(new Error("界面服务 uiWorkspace 未就绪（插件或前端版本不匹配）"));
        return;
      }
      fetch(API + "/card?session=" + encodeURIComponent(session), { credentials: "omit" })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          if (!j || !j.ok || !j.path) throw new Error((j && j.error) || "交接卡未就绪");
          var sessions = getService("sessions");
          var before = {};
          try {
            var snap = sessions && sessions.list && sessions.list.getSnapshot();
            Object.keys((snap && snap.byId) || {}).forEach(function (id) { before[id] = 1; });
          } catch (e) {
            before = {};
          }
          ws.startSession(undefined, {
            prompt: buildSwitchPrompt(j.sid || session, j, userText),
            clearPreviousDraft: true,
          });
          claimNewSession(sessions, before, j.sid || session);
          done(null);
        })
        .catch(function (e) { done(e || new Error("切换失败")); });
    }

    /* 交接卡状态：面板里常显一行（2026-10-30 用户要求）。
       以前「没有卡 + 宿主没判定该生成」时整块 return null——新用户只看到「什么都没有」，
       分不清是没跑完一轮、还是 handoff.py/memory_root 没配。现在五种状态各自说清。 */
    function cardState(card, ho, steps) {
      if (card) return { tone: "ok", label: "交接卡 · " + (card.chars || 0) + " 字", tip: card.path };
      if (!ho) return null;
      if (ho.unavailable) {
        return {
          tone: "off", label: "交接卡：未配置 handoff.py", tip: ho.reason || "",
          detail: "缺少 handoff.py：交接卡 / 切会话不可用，账目不受影响（见 _local/tg-account.json）",
        };
      }
      if (ho.state === "running") return { tone: "wait", label: "交接卡生成中…", tip: ho.cmd || "" };
      if (ho.state === "error") {
        return {
          tone: "warn", label: "交接卡生成失败", tip: ho.detail || ho.cmd || "",
          detail: String(ho.detail || "").trim() || ("可手动执行：" + (ho.cmd || "")),
        };
      }
      if (ho.state === "ok") return { tone: "wait", label: "交接卡已生成，路径未回传", tip: ho.cmd || "" };
      if (!(typeof steps === "number" && steps > 0)) {
        return { tone: "off", label: "交接卡：跑完一轮后生成", tip: "新会话还没有可交接的内容（至少跑完一步）" };
      }
      return { tone: "wait", label: "交接卡：等待生成", tip: ho.cmd || "" };
    }

    function CardBlock(props) {
      var ho = props.handoff;
      var card = props.card;
      var s = React.useState("idle");
      var state = s[0];
      var setState = s[1];
      var sw = React.useState("idle");
      var swState = sw[0];
      var setSw = sw[1];
      var timer = React.useRef(0);
      React.useEffect(function () {
        return function () {
          if (timer.current) clearTimeout(timer.current);
        };
      }, []);
      // hooks 必须在守卫之前：卡片会在轮询中从无到有，提前 return 会让 hook 数量变化。
      var st = cardState(card, ho, props.steps);
      if (!st) return null;
      function reset(ms) {
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(function () {
          setState("idle");
        }, ms);
      }
      function onCopy(e) {
        e.stopPropagation();
        if (state === "busy" || !card || !card.path) return;
        setState("busy");
        copyText(card.path)
          .then(function () {
            setState("ok");
            reset(2000);
          })
          .catch(function () {
            setState("fail");
            reset(2600);
          });
      }
      function onSwitch(e) {
        e.stopPropagation();
        if (swState === "busy" || !card || !card.path) return;
        setSw("busy");
        switchToNewSession(props.session, null, function (err) {
          if (err) {
            setSw("fail");
            if (timer.current) clearTimeout(timer.current);
            timer.current = setTimeout(function () { setSw("idle"); }, 2600);
            return;
          }
          setSw("ok");
        });
      }
      var label = st.label;
      var swText = swState === "busy" ? "切换中"
        : swState === "fail" ? "切换失败"
        : swState === "ok" ? "已切换"
        : "切过去 →";
      var swStyle = swState === "ok" ? BTN_OK
        : swState === "fail" ? BTN_BAD
        : Object.assign({}, BTN, {
          background: props.hot ? "#8a5a00" : "#1f5f8b",
          borderColor: props.hot ? "#b87800" : "#2b7cb5",
        });
      var btnText = state === "busy" ? "复制中"
        : state === "fail" ? "复制失败，请重试"
        : state === "ok" ? "已复制路径"
        : "复制路径";
      var btnStyle = state === "fail" ? BTN_BAD : state === "ok" ? BTN_OK : BTN;
      return h("div", {
        style: {
          marginTop: "8px", padding: "6px 8px", borderRadius: "6px",
          background: st.tone === "ok" ? "rgba(46,125,50,0.18)"
            : st.tone === "warn" ? "rgba(180,120,0,0.18)" : "rgba(255,255,255,0.06)",
          fontSize: "11px",
        },
        title: st.tip || (card ? card.path : ""),
      },
        h("div", { style: { display: "flex", alignItems: "center", gap: "6px" } },
          h("span", {
            style: {
              flex: "1 1 auto", minWidth: 0, fontFamily: "ui-monospace, monospace",
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", opacity: 0.9,
            },
          }, label),
          card ? h("button", {
            style: swStyle,
            onClick: onSwitch,
            title: "新建会话并把交接卡路径预填进输入框（不自动发送）",
          }, swText) : null,
          card ? h("button", { style: btnStyle, onClick: onCopy, title: card.path }, btnText) : null
        ),
        card ? h("div", {
          style: {
            marginTop: "2px", opacity: 0.5, fontFamily: "ui-monospace, monospace",
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          },
        }, shortPath(card.path)) : null,
        st.detail
          ? h("div", { style: { marginTop: "2px", opacity: 0.65, wordBreak: "break-all" } }, st.detail)
          : null
      );
    }

    function Panel(props) {
      var d = props.data;
      var tr = useTrim();
      var prof = tr[0];
      var flipMaster = tr[1];
      var flipRule = tr[2];
      var ex = React.useState(false);
      var openRules = ex[0];
      var setOpenRules = ex[1];
      var master = !!(prof && prof.master);
      var oo = React.useState(orderEnabled());
      var ordOn = oo[0];
      var setOrdOn = oo[1];
      var ddd = React.useState(orderDir());
      var ordDir = ddd[0];
      var setOrdDir = ddd[1];
      if (!d) {
        return h("div", { style: PANEL }, "词元桥不可用：/tg-account/api 无响应。重启后如仍未恢复，请检查插件加载状态。");
      }
      var rows = (d.detail || []).slice().reverse();
      var ts = trimStat(d);
      return h(
        "div",
        { style: PANEL, onClick: function (e) { e.stopPropagation(); } },
        h("div", { style: { display: "flex", justifyContent: "space-between", marginBottom: "8px" } },
          h("b", null, "词元掌控"),
          h("span", { style: { opacity: 0.6 } }, d.turns + " 轮 · " + d.steps + " 步")
        ),
        h("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px 12px", marginBottom: "10px" } },
          h("span", null, "本会话合计"), h("span", { style: { textAlign: "right", fontWeight: 600 } }, fmtCny3(d.cny)),
          (d.subagents && d.subagents.n)
            ? h(React.Fragment, null,
                h("span", { style: { opacity: 0.75 } }, "含 " + d.subagents.n + " 个子代理"),
                h("span", { style: { textAlign: "right", fontWeight: 600 } },
                  fmtCny3(typeof d.cnyWithChildren === "number" ? d.cnyWithChildren : d.cny + d.subagents.cny)))
            : null,
          /* 2026-10-30 用户要求：同类型的花费和省挨着——本会话（花费/省）、本会话链（花费/省），今日与累计垫后 */
          h("span", null, "本会话省"),
          h("span", { style: { textAlign: "right" }, title: selfTip(d) },
            fmtCny3(d.savedSelf && typeof d.savedSelf.cny === "number" ? d.savedSelf.cny : null)),
          h("span", { style: { opacity: 0.85 } }, "本会话链"),
          h("span", { style: { textAlign: "right" }, title: chainTip(d.chain) }, fmtChain(d.chain)),
          h("span", { style: { opacity: 0.85 } }, "本会话链省"),
          h("span", { style: { textAlign: "right" }, title: chainTip(d.chain) }, fmtChainSaved(d.chain)),
          h("span", null, "今日花费"),
          h("span", { style: { textAlign: "right" }, title: todayTip(d.today) },
            fmtCny3(d.today && typeof d.today.cny === "number" ? d.today.cny : null)),
          h("span", null, "今日省"),
          h("span", { style: { textAlign: "right" }, title: todaySavedTip(d.today) },
            fmtCny3(d.today && typeof d.today.saved === "number" ? d.today.saved : null)),
          h("span", null, "累计省"),
          h("span", { style: { textAlign: "right" }, title: savedTip(d.saved) },
            d.saved && typeof d.saved.cny === "number"
              ? fmtCny3(d.saved.cny)
              : (d.saved && d.saved.pending ? "统计中…" : "—")),
          h("span", null, "命中率"),
          h("span", { style: { textAlign: "right" }, title: "缓存命中 = 缓存读 tokens ÷ (输入 + 缓存读)；本会话全历史" },
            fmtRate(d.hit)),
          h("span", null, "未命中 / 步"), h("span", { style: { textAlign: "right" } }, fmtK(d.missPerStep)),
          h("span", null, "当前上下文"), h("span", { style: { textAlign: "right" } },
            fmtK(d.ctxTokens) + " / " + Math.round(d.window / 1000) + "k（" + (d.ctxRatio * 100).toFixed(0) + "%）"),
          h("span", null, "换会话回本步数"), h("span", { style: { textAlign: "right", fontWeight: 600 } },
            d.nStar === null || d.nStar === undefined ? "—" : d.nStar + " 步")
        ),
        (d.models && d.models.length ? h("div", { style: { marginBottom: "8px" } },
          h("div", { style: { opacity: 0.55, fontSize: "10px", marginBottom: "2px" } },
            "按模型" + (d.priceSource ? "（" + d.priceSource + "）" : "")),
          d.models.slice(0, 4).map(function (m) {
            return h("div", {
              key: m.provider + ":" + m.model,
              style: { display: "grid", gridTemplateColumns: "1fr 46px 64px", gap: "6px", fontSize: "10px",
                opacity: m.free ? 0.65 : 0.85 },
            },
              h("span", { style: { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },
                title: m.provider + ":" + m.model + " · 命中方式 " + m.how },
                (m.free ? "免费 " : "") + m.model),
              h("span", { style: { textAlign: "right", opacity: 0.6 } }, m.calls + " 次"),
              h("span", { style: { textAlign: "right" } }, m.free ? "¥0" : fmtCny(m.cny)));
          }),
          d.models.length > 4 ? h("div", { style: { opacity: 0.45, fontSize: "10px" } },
            "… 其余 " + (d.models.length - 4) + " 个模型") : null,
          (d.unpriced && d.unpriced.calls) ? h("div", { style: { color: "#e8a33d", fontSize: "10px", marginTop: "2px" } },
            "未定价 " + d.unpriced.calls + " 次（" + fmtK(d.unpriced.tokens) + " tokens）记 ¥0 —— 补 price 表后重算") : null
        ) : null),
        (d.subagents && d.subagents.n ? (function () {
          var sb = d.subagents;
          var sbd = sb.budget || { steps: 40, cny: 1 };
          var sa = sb.alert || {};
          var overC = sa.on ? "#e8a33d" : null;
          return h("div", { style: { marginBottom: "8px" } },
            h("div", { style: { opacity: sa.on ? 0.95 : 0.55, fontSize: "10px", marginBottom: "2px", color: overC || undefined } },
              "子代理 " + sb.n + " 个 · " + (sb.steps || 0) + " 步 · 合计 " + fmtCny(sb.cny) +
              "（今日 " + fmtCny(sb.today) + "）" +
              (sa.on ? " · 超预算 " + (sa.reasons || []).join("+") : "")),
            sb.list.slice(0, 6).map(function (k) {
              var over = (k.steps || 0) > sbd.steps || k.cny > sbd.cny;
              return h("div", {
                key: k.sid,
                style: {
                  display: "grid", gridTemplateColumns: "1fr 42px 62px 62px", gap: "6px", fontSize: "10px",
                  opacity: 0.82, color: over ? "#e8a33d" : undefined,
                },
              },
                h("span", null, k.sid),
                h("span", { style: { textAlign: "right", opacity: 0.65 } }, (k.steps || 0) + "步"),
                h("span", { style: { textAlign: "right", opacity: 0.6 } }, "今日 " + fmtCny(k.today)),
                h("span", { style: { textAlign: "right" } }, fmtCny(k.cny)));
            }),
            sb.list.length > 6
              ? h("div", { style: { opacity: 0.45, fontSize: "10px" } }, "… 其余 " + (sb.list.length - 6) + " 个")
              : null,
            h("div", { style: { opacity: 0.4, fontSize: "10px", marginTop: "2px" } },
              "预算：单个子代理 ≤" + sbd.steps + " 步 / ≤" + fmtCny(sbd.cny) + "（`_local/tg-budget.json` 可改）")
          );
        })() : null),
        h(CardBlock, { handoff: d.handoff, card: d.card, session: props.session, hot: d.shouldSplit, steps: d.steps }),
        h("div", {
          style: {
            display: "flex", alignItems: "center", justifyContent: "space-between",
            borderTop: "1px solid rgba(255,255,255,0.1)", paddingTop: "8px", marginTop: "6px",
          },
        },
          h("span", null, "母会话聚合"),
          h("span", { style: { display: "flex", alignItems: "center", gap: "6px" } },
            ordOn ? h("button", {
              type: "button",
              title: "链内顺序：顺叙（母在上）/ 倒叙（最近的先），点一下切换",
              onClick: function (e) {
                e.stopPropagation();
                var v = ordDir === "asc" ? "desc" : "asc";
                setOrdDir(v);
                setOrderDir(v);
              },
              style: {
                cursor: "pointer", borderRadius: "999px", border: "1px solid rgba(255,255,255,0.16)",
                background: "rgba(255,255,255,0.06)", color: "inherit",
                padding: "0 8px", fontSize: "10px", lineHeight: "16px",
              },
            }, ordDir === "asc" ? "链内顺叙" : "链内倒叙") : null,
            h("label", { style: { display: "flex", alignItems: "center", gap: "6px", cursor: "pointer" } },
              h("input", {
                type: "checkbox",
                checked: ordOn,
                onChange: function (e) {
                  var v = !!(e && e.target && e.target.checked);
                  setOrdOn(v);
                  setOrderEnabled(v);
                },
              }),
              h("span", { style: { fontSize: "11px", opacity: 0.65 } }, ordOn ? "开" : "最近更新")))),
        h("div", {
          style: {
            display: "flex", alignItems: "center", justifyContent: "space-between",
            borderTop: "1px solid rgba(255,255,255,0.1)", paddingTop: "8px", marginTop: "2px",
          },
        },
          h("span", { style: { display: "flex", alignItems: "center", gap: "8px" } },
            h("span", null, "输出瘦身"),
            h("span", {
              style: { fontSize: "10px", opacity: 0.55 },
              title: ts.tip,
            }, ts.text),
            h("button", {
              onClick: function (e) { e.stopPropagation(); setOpenRules(!openRules); },
              style: {
                cursor: "pointer", borderRadius: "999px", border: "1px solid rgba(255,255,255,0.16)",
                background: "rgba(255,255,255,0.06)", color: "inherit",
                padding: "0 8px", fontSize: "10px", lineHeight: "16px",
              },
            }, openRules ? "收起 ▴" : "展开 ▾")
          ),
          h("button", {
            onClick: flipMaster,
            style: {
              cursor: "pointer", borderRadius: "999px", border: "1px solid rgba(255,255,255,0.2)",
              background: master ? "#2e7d32" : "rgba(255,255,255,0.08)", color: "#fff",
              padding: "2px 12px", fontSize: "12px",
            },
          }, master ? "已启用" : "已停用")
        ),
        openRules ? h("div", { style: { marginTop: "6px" } },
          (function () {
            var meta = prof && prof.rulesMeta;
            if (!meta || !meta.length) {
              return h("div", { style: { opacity: 0.6, fontSize: "10px" } }, "宿主未返回规则清单，请重启 DSH 后重试");
            }
            var rules = (prof && prof.rules) || {};
            var onCount = meta.filter(function (r) { return rules[r.key] !== false; }).length;
            return h("div", null,
              meta.map(function (r) {
                var on = rules[r.key] !== false;
                return h("div", {
                  key: r.key,
                  onClick: function (e) { e.stopPropagation(); flipRule(r.key); },
                  title: r.desc || "",
                  style: {
                    display: "flex", alignItems: "baseline", gap: "6px", padding: "2px 0",
                    cursor: "pointer", opacity: master ? (on ? 1 : 0.4) : 0.28,
                  },
                },
                  h("span", { style: { flex: "0 0 12px", textAlign: "center", fontSize: "11px" } }, on ? "✓" : "○"),
                  h("span", { style: { flex: "0 0 auto", fontSize: "11px" } }, r.label),
                  h("span", {
                    style: {
                      flex: "1 1 auto", minWidth: 0, fontSize: "10px", opacity: 0.5,
                      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                    },
                  }, r.desc || "")
                );
              }),
              h("div", { style: { marginTop: "4px", fontSize: "10px", opacity: 0.45 } },
                (master ? "已启用 " + onCount + " / " + meta.length + " 项" : "总开关已关闭") + " · 改动于下一步生效")
            );
          })()
        ) : null,
        master ? null : h("div", { style: { opacity: 0.45, marginTop: "4px", fontSize: "10px" } }, "关闭后不注入这组规范，也不压缩工具输出（机械层同时停）"),
        h("div", { style: { marginTop: "10px", opacity: 0.85 } },
          h("div", { style: { marginBottom: "4px", opacity: 0.6 } }, "逐轮明细"),
          rows.map(function (t) {
            return h("div", {
              key: t.i,
              style: {
                display: "grid", gridTemplateColumns: "40px 52px 64px 1fr", gap: "6px",
                padding: "2px 0", borderBottom: "1px solid rgba(255,255,255,0.05)",
              },
            },
              h("span", { style: { opacity: 0.7 } }, "第" + t.i + "轮"),
              h("span", null, t.steps + " 步"),
              h("span", { style: { textAlign: "right" } }, fmtCny(t.cny)),
              h("span", { style: { textAlign: "right", opacity: 0.6 } },
                fmtK(t.input) + "/" + fmtK(t.cached) + "/" + fmtK(t.out))
            );
          })
        ),
        (d.caps && (d.caps.handoff === "missing" || d.caps.config === "missing" || d.caps.prices === "builtin"))
          ? h("div", { style: { marginTop: "6px", opacity: 0.55, fontSize: "10px" } },
              "可选依赖：" + [
                d.caps.handoff === "missing" ? "交接卡脚本缺失" : null,
                d.caps.config === "missing" ? "config.yaml 缺失" : null,
                d.caps.prices === "builtin" ? "价表用内置默认" : null,
              ].filter(Boolean).join(" · ") + "（改 _local/tg-account.json）")
          : null,
        h("div", { style: { marginTop: "8px", opacity: 0.45 } }, "输入/命中/输出 · " + (d.priceNote || ""))
      );
    }

    function Account(props) {
      var session = resolveSession(props);
      var data = useAccount(session, 5000);
      var s = React.useState(false);
      var open = s[0];
      var setOpen = s[1];
      React.useEffect(function () {
        if (!open) return undefined;
        function onDoc(e) {
          if (!e || !e.target || !e.target.closest) return;
          if (!e.target.closest("[data-composer-stat='tg-account']")) setOpen(false);
        }
        function onKey(e) {
          if (e && e.key === "Escape") setOpen(false);
        }
        document.addEventListener("pointerdown", onDoc);
        document.addEventListener("keydown", onKey);
        return function () {
          document.removeEventListener("pointerdown", onDoc);
          document.removeEventListener("keydown", onKey);
        };
      }, [open]);
      var asw = data && data.autoSwitch;
      var hot = !!(data && data.shouldSplit);
      var aswLocal = React.useState(null);
      var aswOn = aswLocal[0] === null ? !!(asw && asw.on) : aswLocal[0];
      var setAswOn = aswLocal[1];
      var pend = React.useState(null);
      var pending = pend[0];
      var setPending = pend[1];
      var aswErr = React.useState("");
      var aswErrText = aswErr[0];
      var setAswErr = aswErr[1];

      // 2026-10-29 用户口径：轮末只「检查该不该切」并标记待转接，不自动跳；
      // 真正转接发生在用户下一次按发送时（交接卡 + 这次输入一起带过去）。
      React.useEffect(function () {
        var key = shouldTransfer(asw, session, hot);
        if (!key || !aswOn) {
          setPending(null);
          return;
        }
        setPending(key);
      }, [data, aswOn, hot]);

      var guardRef = React.useRef({});
      guardRef.current = {
        pending: pending,
        session: session,
        aswOn: aswOn,
        setPending: setPending,
        setErr: setAswErr,
      };
      React.useEffect(function () {
        function onKeyDown(e) {
          var g = guardRef.current;
          if (!g || !g.pending || !g.aswOn) return;
          if (e.key !== "Enter" || e.shiftKey || e.altKey || e.ctrlKey || e.metaKey) return;
          if (e.isComposing) return; // 输入法组字中不拦
          var el = e.target && e.target.closest ? e.target.closest('[contenteditable="true"]') : null;
          if (!el || !inMainComposer(el)) return;
          var text = "";
          try { text = String(el.innerText || "").trim(); } catch (err) { text = ""; }
          if (!text) return; // 空输入不拦，照常发
          e.preventDefault();
          e.stopPropagation();
          g.pending = null;
          g.setPending(null);
          switchToNewSession(g.session, text, function (err) {
            if (err) {
              g.setErr("转接失败：" + (err.message || err) + "（输入还在原输入框，再按一次回车会正常发到旧会话）");
            } else {
              g.setErr("");
            }
          });
        }
        document.addEventListener("keydown", onKeyDown, true);
        return function () { document.removeEventListener("keydown", onKeyDown, true); };
      }, []);
      function toggleAsw(e) {
        e.stopPropagation();
        var next = !aswOn;
        setAswOn(next);
        fetch(API + "/autoswitch?session=" + encodeURIComponent(session), {
          method: "POST",
          headers: { "content-type": "application/json" },
          credentials: "omit",
          body: JSON.stringify({ on: next }),
        })
          .then(function (r) { return r.json(); })
          .then(function (j) { if (j && j.ok) setAswOn(!!j.on); else setAswOn(!next); })
          .catch(function () { setAswOn(!next); });
      }
      var capsMissing = !!(data && data.caps && data.caps.handoff === "missing");
      var alert = !!(data && data.subagents && data.subagents.alert && data.subagents.alert.on);
      var done = !!(asw && asw.transferred);
      var pillStyle = alert ? PILL_ALERT : done ? PILL_DONE : hot ? PILL_HOT : PILL;
      return h(
        "span",
        { "data-composer-stat": "tg-account", style: { position: "relative", display: "inline-flex" } },
        h("span", {
          style: pillStyle,
          title: data
            ? subTip(data) +
              (data.models && data.models.length > 1 ? " · " + data.models.length + " 个模型" : "") +
              (data.unpriced && data.unpriced.calls ? " · 未定价 " + data.unpriced.calls + " 次" : "")
            : "词元桥未就绪",
          onClick: function (e) { e.stopPropagation(); setOpen(!open); },
        }, "本 ", fmtCny(data && (typeof data.cnyWithChildren === "number" ? data.cnyWithChildren : data.cny)),
          (data && data.subagents && data.subagents.n)
            ? h("span", { style: { opacity: 0.7, fontSize: "10px" }, title: subTip(data) },
                "+" + data.subagents.n + "子")
            : null,
          alert ? h("span", { style: { fontWeight: 600, marginLeft: "2px" }, title: subTip(data) }, "超预算") : null,
          h("span", { style: { opacity: 0.72 }, title: todayTip(data && data.today) },
            "· 今日 " + (data && data.today && typeof data.today.cny === "number" ? fmtCny(data.today.cny) : "…")),
          h("span", { style: { opacity: 0.72 }, title: selfTip(data) },
            "· 省 " + (data && data.savedSelf ? fmt2(data.savedSelf.cny) : "—")),
          h("span", { style: { opacity: 0.72 }, title: savedTip(data && data.saved) },
            "· 累计省 " + (data && data.saved && typeof data.saved.cny === "number" ? fmtCny(data.saved.cny) : "计算中…")),
          asw && asw.transferred
            ? h("span", { style: { opacity: 0.95, fontWeight: 600 }, title: "这个对话已经转出到 " + asw.transferred.to + "，不再重复转接" }, "· 已转接")
            : hot ? h("span", { style: { opacity: 0.85 } }, "· 建议切换") : null,
          aswErrText ? h("span", { style: { color: "#ffb4ab" }, title: aswErrText }, "· 转接失败") : null,
          pending ? h("span", { style: { opacity: 0.85, color: "#ffd27d" } }, "· 待转接") : null),
        h("button", {
          type: "button",
          disabled: capsMissing,
          title: aswErrText
            ? "自动切换失败：" + aswErrText
            : capsMissing
              ? "自动切换不可用：没找到交接卡脚本 handoff.py（见 _local/tg-account.json）"
              : asw && asw.transferred
                ? "这个对话已经转出（到 " + asw.transferred.to + "）：只显示状态，不再重复转接；开关仍跟链走"
                : pending
                ? "待转接：这一轮已判定该切，下一条消息会自动带上交接卡转到新会话"
                : asw && asw.child && !aswOn
                  ? "整条链共用一个开关（链首才是母对话，子对话再切还是子对话）：现在是关，点一下整链开"
                  : aswOn
                    ? (asw && asw.child
                      ? "整条链共用一个开关：现在是开；点一下整链关。链首才是母对话"
                      : asw && asw.lastTurnEnd && asw.lastTurnEnd.kind === "completed" && !asw.cardReady
                        ? "自动切换已开：正在生成交接卡，好了就切"
                        : "自动切换已开：判定该切后，下一条消息会带交接卡转到新会话")
                    : "自动切换：判定该切后，下一条消息会带交接卡转到新会话",
          onClick: toggleAsw,
          style: {
            flex: "0 0 auto", marginLeft: "4px",
            cursor: capsMissing ? "not-allowed" : "pointer",
            opacity: capsMissing ? 0.45 : 1,
            borderRadius: "999px", border: "1px solid " + (aswOn && !capsMissing ? "#43a047" : "rgba(255,255,255,0.18)"),
            background: aswOn && !capsMissing ? "#2e7d32" : "rgba(255,255,255,0.07)",
            color: aswOn && !capsMissing ? "#fff" : "inherit", padding: "0 7px",
            fontSize: "10px", lineHeight: "18px", whiteSpace: "nowrap",
          },
        }, aswOn && !capsMissing ? "自动·开" : "自动"),
        open ? h(Panel, { data: data, session: session }) : null
      );
    }

    function apply(ctx) {
      hostCtx = ctx;
      // 会话切换服务：cordis 里必须 inject 才能 get；拿不到就不切，按钮会显示原因
      try {
        if (typeof ctx.inject === "function") {
          ctx.inject(["uiWorkspace"], function (scope) {
            try {
              uiWorkspaceSvc = scope && typeof scope.get === "function" ? scope.get("uiWorkspace") : null;
            } catch (e) {
              uiWorkspaceSvc = null;
            }
          });
        }
      } catch (e) {
        /* 服务缺失不影响账目 */
      }
      /* 侧栏会话行：行左内边距里的链标（颜色/形态按链统一，菜单里可改）。
         不再走 sidebar.session.row.leading 槽——那个 16×20 的槽会被「进行中」的状态圆点整格顶掉。 */
      try {
        installChainMarks();
      } catch (e) {
        /* DOM 还没就绪就等下次轮询 */
      }
      /* ⋯ 菜单里加一行「标识颜色与形态」（2026-10-26） */
      try {
        ctx.slots.inject("sidebar.workspaces.session.menu.item", function () {
          try {
            ctx.slots.register(
              { name: "sidebar.workspaces.session.menu.item", id: "tg-marker-style", order: 600 },
              function (props) { return h(MarkerMenuRow, props || {}); }
            );
          } catch (e) {
            /* 官方没这个插槽就跳过 */
          }
        });
      } catch (e) {
        /* 同上 */
      }
      ctx.slots.inject("conversation.composer.dock", function () {
        ctx.slots.register(
          { name: "conversation.composer.dock", id: "tg-account", order: 2 },
          function (props) { return h(Account, props || {}); }
        );
      });
    }

    module.exports = {
      inject: inject,
      apply: apply,
      // 仅供 tests/switch.test.mjs 的假宿主加载，不参与前端运行
      __test: {
        getService: getService,
        trimStat: trimStat,
        buildSwitchPrompt: buildSwitchPrompt,
        switchToNewSession: switchToNewSession,
        shouldTransfer: shouldTransfer,
        inMainComposer: inMainComposer,
        resolveSession: resolveSession,
        currentSessionId: currentSessionId,
        planOrder: planOrder,
        sid8Of: sid8Of,
        hueOf: hueOf,
        cardState: cardState,
      },
    };
    return module.exports;
  },
});
