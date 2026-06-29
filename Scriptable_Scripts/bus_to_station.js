const API = "https://bus-widget-api.onrender.com/api/next_bus";
const WEB = "https://bus-widget-api.onrender.com/";
const ROUTE = {"id":"to_station","title":"金沢駅行き","badgeLabel":"発"};
const C = { bg: "#1c1c1e", text: "#ffffff", sub: "#d1d1d6", orange: "#ff9f0a", blue: "#32ade6", green: "#30d158", red: "#ff453a", yellow: "#ffd60a" };
const LABELS = ["次発", "次々", "3本目"];
const LABEL_COLORS = [C.orange, C.blue, C.green];
const STOPS = { A: "正門向い", B: "正門前", C: "四十万方向", D: "四十万から" };

function text(parent, value, color, font) {
  const item = parent.addText(String(value));
  item.textColor = new Color(color);
  item.font = font;
  return item;
}

function message(widget, value, color) {
  const item = text(widget, value, color, Font.boldSystemFont(16));
  item.lineLimit = 2;
}

function refreshUrl() {
  return "scriptable:///run?scriptName=" + encodeURIComponent(Script.name());
}

function apiUrl() {
  return API + "?dir=" + encodeURIComponent(ROUTE.id) + "&t=" + Date.now();
}

function addHeader(widget) {
  const row = widget.addStack();
  row.centerAlignContent();
  text(row, ROUTE.title, C.text, Font.boldSystemFont(12));
  row.addSpacer();
  const refresh = text(row, "更新", C.blue, Font.boldSystemFont(12));
  refresh.url = refreshUrl();
}

async function loadData() {
  const req = new Request(apiUrl());
  req.timeoutInterval = 10;
  const data = await req.loadJSON();
  const statusCode = req.response && req.response.statusCode;
  if (statusCode && (statusCode < 200 || statusCode >= 300)) throw new Error("HTTP " + statusCode);
  if (!data || data.status === "error") throw new Error((data && data.message) || "API error");
  return data;
}

function addBus(widget, bus, index) {
  const safeBus = {
    time: bus && bus.time ? String(bus.time) : "--:--",
    line: bus && bus.line ? String(bus.line) : "",
    stop: bus && bus.stop ? String(bus.stop) : "-"
  };
  const row = widget.addStack();
  row.centerAlignContent();
  text(row, LABELS[index] || "", LABEL_COLORS[index] || C.sub, Font.boldSystemFont(12));
  row.addSpacer(8);
  text(row, safeBus.time, C.text, index === 0 ? Font.boldSystemFont(22) : Font.boldSystemFont(18));
  row.addSpacer(8);
  text(row, "[" + ROUTE.badgeLabel + ": " + (STOPS[safeBus.stop] || safeBus.stop) + "]", C.yellow, Font.boldSystemFont(10));
  row.addSpacer(6);
  const line = text(row, safeBus.line, C.sub, Font.systemFont(11));
  line.lineLimit = 1;
}

async function createWidget() {
  const widget = new ListWidget();
  widget.backgroundColor = new Color(C.bg);
  widget.setPadding(16, 16, 16, 16);
  widget.url = WEB;
  addHeader(widget);
  widget.addSpacer(8);

  try {
    const data = await loadData();
    if (data.status === "success" && Array.isArray(data.buses) && data.buses.length > 0) {
      data.buses.slice(0, 3).forEach((bus, index) => {
        addBus(widget, bus, index);
        if (index < Math.min(data.buses.length, 3) - 1) widget.addSpacer(6);
      });
    } else if (data.status === "end") {
      message(widget, "本日のバスは終了しました", C.red);
    } else {
      message(widget, "時刻表データを確認してください", C.orange);
    }
  } catch (error) {
    message(widget, "時刻表を取得できません", C.orange);
  }

  widget.refreshAfterDate = new Date(Date.now() + 1000 * 60);
  return widget;
}

const widget = await createWidget();
if (config.runsInApp) {
  widget.presentMedium();
  App.close();
}
Script.setWidget(widget);
Script.complete();
