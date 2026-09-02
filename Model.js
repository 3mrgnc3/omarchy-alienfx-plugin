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
  palette:     "󰚵",
  link:        "󰌷",
  unlink:      "󰌺",
  effects:     "󰁨",
  profile:     "󰉋",
  save:        "󰆓",
  keyboard:    "󰌌",
  brightness:  "󰃞",
  check:       "󰄬",
  touchpad:    "󰴟",
  power:       "󰐥"
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

if (typeof module !== "undefined") {
  module.exports = {
    ICON: ICON, ZONE_LABELS: ZONE_LABELS, ZONE_ORDER: ZONE_ORDER,
    EFFECT_LABELS: EFFECT_LABELS, EFFECT_ORDER: EFFECT_ORDER,
    clamp255: clamp255, hexToRgb: hexToRgb, rgbToHex: rgbToHex,
    zoneHex: zoneHex, brightnessPercent: brightnessPercent,
    zoneOptions: zoneOptions, effectOptions: effectOptions,
    profileOptions: profileOptions, parseState: parseState,
    streamSafe: streamSafe
  };
}
