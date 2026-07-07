// Aelvoxim Desktop Gateway — Photoshop read-only snapshot script
// Outputs canvas state as JSON to a temp file for the Gateway to consume.
// 
// Usage: Photoshop > File > Scripts > Browse > select this file
// Or configure Gateway to call: Photoshop.exe -r photoshop.jsx

#target photoshop
#strict on

// ── Config ──────────────────────────────────────────────
var TEMP_DIR = Folder("/tmp/aelvoxim_gateway");
var PREFIX = "aelvoxim_snapshot_";

// ── Helpers ─────────────────────────────────────────────
function ensureDir(dir) {
  if (!dir.exists) dir.create();
}

function randomId() {
  var chars = "abcdefghijklmnopqrstuvwxyz0123456789";
  var result = "";
  for (var i = 0; i < 8; i++)
    result += chars[Math.floor(Math.random() * chars.length)];
  return result;
}

function layerToObj(l) {
  try {
    return {
      name: l.name,
      visible: l.visible,
      locked: l.locked,
      kind: l.kind,
      opacity: l.opacity / 100.0,
      bounds: [
        Math.round(l.bounds[0].value),
        Math.round(l.bounds[1].value),
        Math.round(l.bounds[2].value),
        Math.round(l.bounds[3].value)
      ],
      // Text layer content
      text: l.kind == LayerKind.TEXT ? l.textItem.contents : null
    };
  } catch (e) {
    return { name: l.name, error: String(e) };
  }
}

// ── Snapshot ────────────────────────────────────────────
function buildSnapshot() {
  var snapshot = {
    software: "photoshop",
    timestamp: new Date().toISOString(),
    canvas: null,
    layers: [],
    selection: null,
    guides: [],
    error: null
  };

  if (app.documents.length === 0) {
    snapshot.error = "No document open";
    return snapshot;
  }

  var doc = app.activeDocument;
  snapshot.canvas = {
    width: doc.width.value,
    height: doc.height.value,
    resolution: doc.resolution,
    colorMode: doc.mode.toString()
  };

  // Layers (top to bottom)
  for (var i = 0; i < doc.layers.length; i++) {
    snapshot.layers.push(layerToObj(doc.layers[i]));
  }

  // Selection
  try {
    if (doc.selection && doc.selection.bounds) {
      var b = doc.selection.bounds;
      snapshot.selection = {
        bounds: [
          Math.round(b[0].value), Math.round(b[1].value),
          Math.round(b[2].value), Math.round(b[3].value)
        ]
      };
    }
  } catch (e) {
    // No selection or error
  }

  // Guides
  try {
    for (var g = 0; g < doc.guides.length; g++) {
      snapshot.guides.push({
        direction: doc.guides[g].direction.toString(),
        position: doc.guides[g].position.value
      });
    }
  } catch (e) { /* older PS versions may not support */ }

  return snapshot;
}

// ── Main ────────────────────────────────────────────────
ensureDir(TEMP_DIR);
var outputFile = TEMP_DIR + "/" + PREFIX + randomId() + ".json";
var snapshot = buildSnapshot();

var f = new File(outputFile);
f.open("w");
f.write(snapshot.toSource ? snapshot.toSource() : JSON.stringify(snapshot, null, 2));
f.close();

// Clean up old snapshots (keep last 20)
var existing = TEMP_DIR.getFiles(PREFIX + "*.json");
existing.sort(function(a, b) { return a.modified.getTime() - b.modified.getTime(); });
while (existing.length > 20) {
  existing[0].remove();
  existing.shift();
}
