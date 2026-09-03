const API = "https://bus-widget-api.onrender.com/api/next_bus";
const WEB = "https://bus-widget-api.onrender.com/";
const ROUTE = {"id":"to_station","title":"KIT → 金沢駅","badgeLabel":"発"};
const C = { bg: "#1c1c1e", text: "#ffffff", sub: "#d1d1d6", orange: "#ff9f0a", blue: "#32ade6", green: "#30d158", red: "#ff453a", yellow: "#ffd60a" };
const LABELS = ["次発", "次々", "3本目"];
const LABEL_COLORS = [C.orange, C.blue, C.green];
const STOPS = { A: "正門向い", B: "正門前", C: "四十万方向", D: "四十万から" };
const REQUEST_TIMEOUT_SECONDS = 30;
const REFRESH_MINUTES = 5;

function text(parent, value, color, font) {
  const item = parent.addText(String(value));
  item.textColor = new Color(color);
  item.font = font;
  return item;
}

function message(widget, value, color) {
  const item = text(widget, value, color, Font.boldSystemFont(16));
  item.lineLimit = 2;
  item.minimumScaleFactor = 0.7;
}

function fitOneLine(item, minimumScaleFactor) {
  item.lineLimit = 1;
  item.minimumScaleFactor = minimumScaleFactor || 0.65;
  return item;
}

function widgetFamily() {
  return ["small", "medium", "large"].includes(config.widgetFamily)
    ? config.widgetFamily
    : "medium";
}

function busLimit(family) {
  if (family === "small") return 1;
  if (family === "large") return 3;
  return 2;
}

function formatMinutesUntil(minutes) {
  if (!Number.isInteger(minutes) || minutes <= 1) return "まもなく";
  if (minutes < 60) return "あと" + minutes + "分";

  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes === 0
    ? "あと" + hours + "時間"
    : "あと" + hours + "時間" + remainingMinutes + "分";
}

function formatNextServiceDate(nextService) {
  if (nextService && nextService.days_ahead === 1) return "明日";
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(
    nextService && nextService.date ? String(nextService.date) : ""
  );
  return match ? Number(match[2]) + "/" + Number(match[3]) : "次回";
}

function formatDayType(dayType) {
  if (dayType === "weekday") return "平日";
  if (dayType === "weekend") return "土日祝";
  return "";
}

function normalizeBus(bus) {
  const stop = bus && bus.stop ? String(bus.stop) : "-";
  const stopName = bus && bus.stop_name
    ? String(bus.stop_name)
    : (STOPS[stop] || stop);
  const line = bus && bus.line ? String(bus.line) : "";
  const lineNumber = bus && bus.line_number !== null && bus.line_number !== undefined
    ? String(bus.line_number)
    : "";
  return {
    time: bus && bus.time ? String(bus.time) : "--:--",
    line: line,
    lineLabel: lineNumber ? lineNumber + "系統" : line,
    stop: stop,
    stopName: stopName,
    minutesUntil: bus && Number.isInteger(bus.minutes_until)
      ? bus.minutes_until
      : null
  };
}

function widgetError(kind, value) {
  const error = new Error(value);
  error.kind = kind;
  return error;
}

function errorMessage(error) {
  if (error && error.kind === "http") return "API接続エラー";
  if (error && error.kind === "json") return "応答データエラー";
  if (error && error.kind === "api") return "時刻表データエラー";
  if (error && error.kind === "response") return "応答データエラー";
  return "通信エラー";
}

function refreshUrl() {
  return "scriptable:///run?scriptName=" + encodeURIComponent(Script.name());
}

function apiUrl() {
  return API + "?dir=" + encodeURIComponent(ROUTE.id) + "&t=" + Date.now();
}

function addHeader(widget, family) {
  const row = widget.addStack();
  row.centerAlignContent();
  const title = text(row, ROUTE.title, C.text, Font.boldSystemFont(12));
  fitOneLine(title, family === "small" ? 0.45 : 0.65);
  if (family !== "small") {
    row.addSpacer();
    const refresh = text(row, "更新", C.blue, Font.boldSystemFont(12));
    refresh.url = refreshUrl();
  }
}

async function loadData() {
  const req = new Request(apiUrl());
  req.timeoutInterval = REQUEST_TIMEOUT_SECONDS;
  let body;
  try {
    body = await req.loadString();
  } catch (error) {
    throw widgetError("network", "Request failed");
  }

  const statusCode = req.response && req.response.statusCode;
  const isHttpError = statusCode && (statusCode < 200 || statusCode >= 300);
  let data;
  try {
    data = JSON.parse(body);
  } catch (error) {
    if (isHttpError) throw widgetError("http", "HTTP " + statusCode);
    throw widgetError("json", "Invalid JSON");
  }

  if (!data || typeof data !== "object") {
    if (isHttpError) throw widgetError("http", "HTTP " + statusCode);
    throw widgetError("response", "Invalid response");
  }
  if (data.status === "error") {
    throw widgetError("api", data.message || "API error");
  }
  if (isHttpError) throw widgetError("http", "HTTP " + statusCode);
  if (data.status === "success") {
    if (!Array.isArray(data.buses) || data.buses.length === 0) {
      throw widgetError("response", "Missing buses");
    }
    if (!data.buses.every((bus) => bus && /^\d{2}:\d{2}$/.test(String(bus.time || "")) && Number.isInteger(bus.minutes_until))) {
      throw widgetError("response", "Invalid buses");
    }
    return data;
  }
  if (data.status === "end") return data;
  throw widgetError("response", "Unknown status");
}

function addBus(widget, bus, index, family) {
  const safeBus = normalizeBus(bus);
  const remaining = formatMinutesUntil(safeBus.minutesUntil);

  if (family === "small") {
    fitOneLine(text(widget, "次発", C.orange, Font.boldSystemFont(11)), 0.7);
    const primary = widget.addStack();
    primary.centerAlignContent();
    fitOneLine(text(primary, safeBus.time, C.text, Font.boldSystemFont(30)), 0.75);
    primary.addSpacer(6);
    fitOneLine(text(primary, remaining, C.orange, Font.boldSystemFont(12)), 0.55);
    widget.addSpacer(4);
    const details = widget.addStack();
    details.centerAlignContent();
    fitOneLine(text(details, safeBus.lineLabel, C.sub, Font.boldSystemFont(10)), 0.55);
    return;
  }

  const row = widget.addStack();
  row.centerAlignContent();
  fitOneLine(text(row, LABELS[index] || "", LABEL_COLORS[index] || C.sub, Font.boldSystemFont(11)), 0.7);
  row.addSpacer(6);
  fitOneLine(text(row, safeBus.time, C.text, index === 0 ? Font.boldSystemFont(21) : Font.boldSystemFont(17)), 0.75);
  row.addSpacer(6);
  fitOneLine(text(row, remaining, index === 0 ? C.orange : C.sub, Font.boldSystemFont(index === 0 ? 11 : 10)), 0.55);
  row.addSpacer(6);
  fitOneLine(text(row, safeBus.lineLabel, C.sub, Font.systemFont(10)), 0.55);
  row.addSpacer(6);
  fitOneLine(text(row, "[" + ROUTE.badgeLabel + ": " + safeBus.stopName + "]", C.yellow, Font.boldSystemFont(9)), 0.5);
}

function addNextService(widget, nextService, family) {
  message(widget, "本日の運行は終了しました", C.red);
  const nextBus = nextService && nextService.bus;
  if (!nextBus || !/^\d{2}:\d{2}$/.test(String(nextBus.time || ""))) return;

  const safeBus = normalizeBus(nextBus);
  const dateLabel = formatNextServiceDate(nextService);
  const dayType = formatDayType(nextService.day_type);
  widget.addSpacer(family === "small" ? 6 : 8);
  fitOneLine(text(widget, "次のバス", C.sub, Font.boldSystemFont(11)), 0.7);

  const timeRow = widget.addStack();
  timeRow.centerAlignContent();
  fitOneLine(text(timeRow, dateLabel + " " + safeBus.time, C.text, Font.boldSystemFont(family === "small" ? 22 : 20)), 0.65);
  timeRow.addSpacer(8);
  fitOneLine(text(timeRow, safeBus.lineLabel, C.yellow, Font.boldSystemFont(11)), 0.55);

  if (family !== "small") {
    widget.addSpacer(4);
    const details = widget.addStack();
    details.centerAlignContent();
    const detailText = [safeBus.stopName, dayType].filter(Boolean).join("・");
    fitOneLine(text(details, detailText, C.sub, Font.systemFont(10)), 0.55);
  }
}

async function createWidget(family) {
  const widget = new ListWidget();
  widget.backgroundColor = new Color(C.bg);
  const padding = family === "small" ? 14 : 16;
  widget.setPadding(padding, padding, padding, padding);
  widget.url = WEB;
  addHeader(widget, family);
  widget.addSpacer(8);

  try {
    const data = await loadData();
    if (data.status === "success") {
      const limit = busLimit(family);
      const buses = data.buses.slice(0, limit);
      buses.forEach((bus, index) => {
        addBus(widget, bus, index, family);
        if (index < buses.length - 1) widget.addSpacer(6);
      });
    } else if (data.status === "end") {
      addNextService(widget, data.next_service, family);
    }
  } catch (error) {
    console.error(error);
    message(widget, errorMessage(error), C.orange);
  }

  widget.refreshAfterDate = new Date(Date.now() + 1000 * 60 * REFRESH_MINUTES);
  return widget;
}

const family = widgetFamily();
const widget = await createWidget(family);
if (config.runsInApp) {
  if (family === "small") await widget.presentSmall();
  else if (family === "large") await widget.presentLarge();
  else await widget.presentMedium();
  App.close();
}
Script.setWidget(widget);
Script.complete();
