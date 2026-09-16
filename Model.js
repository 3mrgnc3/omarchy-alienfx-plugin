// Pure logic for the AlienFX panel.
//
// Kept out of Panel.qml so it can be reasoned about (and unit-tested) without
// a running shell. Nothing here touches hardware or the filesystem.

// Glyphs, all verified present in the Nerd Font Omarchy ships. Each entry has
// a fallback so a missing glyph degrades to another real icon rather than
// rendering an empty bar slot.
var ICON = {
  alien:       "󰢚",
  alienAlt:    "",
  palette:     "",
  link:        "󰌷",
  unlink:      "󰌺",
  effects:     "󰁨",
  profile:     "󰉋",
  save:        "󰆓",
  keyboard:    "󰌌",
  check:       "󰄬",
  update:      ""
};

var ZONE_LABELS = {
  kbd:  "Keyboard",
  pbtn: "PowerButton",
  tpd:  "Touchpad",
  logo: "Logo"
};

// Order is the order the requirements name them in.
var ZONE_ORDER = ["kbd", "pbtn", "tpd", "logo"];

var EFFECT_LABELS = {
  gradient:   "Gradient",
  wave:       "Wave",
  pulse:      "Pulse",
  nightrider: "Nightrider",
  solid:      "Solid"
};

var EFFECT_ORDER = ["gradient", "wave", "pulse", "nightrider", "solid"];

function clamp255(value) {
  var n = Math.round(Number(value));
  if (isNaN(n)) return 0;
  return Math.max(0, Math.min(255, n));
}

function hexToRgb(hex) {
  var text = String(hex || "").replace("#", "").trim();
  if (text.length === 3) {
    text = text[0] + text[0] + text[1] + text[1] + text[2] + text[2];
  }
  if (!/^[0-9a-fA-F]{6}$/.test(text)) return { r: 255, g: 255, b: 255 };
  return {
    r: parseInt(text.substring(0, 2), 16),
    g: parseInt(text.substring(2, 4), 16),
    b: parseInt(text.substring(4, 6), 16)
  };
}

function pad2(value) {
  var text = clamp255(value).toString(16);
  return text.length < 2 ? "0" + text : text;
}

function rgbToHex(r, g, b) {
  return pad2(r) + pad2(g) + pad2(b);
}

// The colour a zone is currently set to, honouring zone sync.
function zoneHex(state, zone) {
  if (!state || !state.zones) return "ffffff";
  var key = (state.zonesync !== false) ? "kbd" : zone;
  var entry = state.zones[key] || state.zones.kbd;
  return (entry && entry.color) ? String(entry.color) : "ffffff";
}

// Shown next to the intensity slider. Signed, so the neutral centre reads as a
// deliberate "0" rather than looking like an empty or broken value.
function intensityLabel(value) {
  var n = Math.round(Number(value) || 0);
  if (n === 0) return "0";
  return (n > 0 ? "+" : "") + n;
}

function brightnessPercent(value) {
  return Math.round((Number(value) || 0) / 255 * 100);
}

function zoneOptions(experimentalPbtn) {
  var out = [];
  for (var i = 0; i < ZONE_ORDER.length; i++) {
    var key = ZONE_ORDER[i];
    var option = { value: key, label: ZONE_LABELS[key] };
    if (key === "pbtn" && experimentalPbtn) {
      option.tooltip = "Experimental: the firmware often overrides this zone";
    }
    out.push(option);
  }
  return out;
}

function effectOptions() {
  var out = [];
  for (var i = 0; i < EFFECT_ORDER.length; i++) {
    var key = EFFECT_ORDER[i];
    out.push({ value: key, label: EFFECT_LABELS[key] });
  }
  return out;
}

function profileOptions(names) {
  var list = Array.isArray(names) ? names : [];
  var out = [];
  for (var i = 0; i < list.length; i++) {
    out.push({ value: String(list[i]), label: String(list[i]) });
  }
  return out;
}

// Parse `alienfx-ctl state --json`. Returns null on anything unparseable so
// the caller can keep showing the last good state instead of blanking out.
// A "#rrggbb" string QML can assign to a colour property.
function hexColor(hex) {
  var text = String(hex || "").replace("#", "").trim();
  if (!/^[0-9a-fA-F]{6}$/.test(text)) return "#000000";
  return "#" + text;
}

function rgbToHsv(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  var max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
  var h = 0;
  if (d !== 0) {
    if (max === r) h = ((g - b) / d) % 6;
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h /= 6;
    if (h < 0) h += 1;
  }
  return { h: h, s: max === 0 ? 0 : d / max, v: max };
}

function hsvToRgb(h, s, v) {
  var i = Math.floor(h * 6), f = h * 6 - i;
  var p = v * (1 - s), q = v * (1 - f * s), t = v * (1 - (1 - f) * s);
  var r, g, b;
  switch (i % 6) {
    case 0: r = v; g = t; b = p; break;
    case 1: r = q; g = v; b = p; break;
    case 2: r = p; g = v; b = t; break;
    case 3: r = p; g = q; b = v; break;
    case 4: r = t; g = p; b = v; break;
    default: r = v; g = p; b = q;
  }
  return { r: Math.round(r * 255), g: Math.round(g * 255), b: Math.round(b * 255) };
}

// The far end the CLI would derive when the user has not chosen one, so the
// greyed-out swatch previews the colour that would actually be used rather
// than showing nothing. Mirrors colors.complement().
function complementHex(hex) {
  var rgb = hexToRgb(hex);
  var hsv = rgbToHsv(rgb.r, rgb.g, rgb.b);
  var out = hsvToRgb((hsv.h + 0.5) % 1.0, Math.max(hsv.s, 0.35), Math.max(hsv.v, 0.25));
  return rgbToHex(out.r, out.g, out.b);
}

// Stream commands are newline-delimited and space-separated, so only simple
// tokens can travel that way. Anything with whitespace or a quote goes out as a
// one-shot argv instead, where the shell is never involved.
function streamSafe(args) {
  if (!Array.isArray(args) || args.length === 0) return false;
  for (var i = 0; i < args.length; i++) {
    if (!/^[A-Za-z0-9_,.:%=@#-]+$/.test(String(args[i]))) return false;
  }
  return true;
}

function parseState(text) {
  if (!text) return null;
  try {
    var parsed = JSON.parse(text);
    return (parsed && typeof parsed === "object") ? parsed : null;
  } catch (e) {
    return null;
  }
}
