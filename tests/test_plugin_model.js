#!/usr/bin/env node
// Tests for the panel model. They run without hardware and without the shell:
//
//   node tests/test_plugin_model.js

"use strict"

const assert = require("node:assert")
const fs = require("node:fs")
const path = require("node:path")

const source = fs
  .readFileSync(path.join(__dirname, "..", "Model.js"), "utf8")
  .replace(/^\.pragma library\s*/, "")

const model = {}
new Function(
  "exports",
  source +
    "\nexports.logRows = logRows" +
    "\nexports.logTone = logTone" +
    "\nexports.formatEventTime = formatEventTime" +
    "\nexports.logInterfaceLabel = logInterfaceLabel" +
    "\nexports.formatShortUptime = formatShortUptime" +
    "\nexports.sessionStats = sessionStats" +
    "\nexports.sessionLine = sessionLine" +
    "\nexports.sessionHoldLine = sessionHoldLine" +
    "\nexports.sessionRecoveryLine = sessionRecoveryLine"
)(model)

function event(type, timestamp, extra) {
  return Object.assign({ type: type, timestamp: timestamp, message: type }, extra || {})
}

function testLogRowsAreFilteredAndOrdered() {
  const status = {
    event_log: [
      event("config_loaded", "2026-10-31T11:58:00.000+00:00"),
      event("device_stable", "2026-10-31T11:58:01.000+00:00"),
      event("hid_interface_opened", "2026-10-31T11:58:02.000+00:00", { interface: "2" }),
      event("service_started", "2026-10-31T11:58:03.000+00:00"),
      event("usb_receiver_removed", "2026-10-31T12:09:12.000+00:00"),
      event("hid_interface_closed", "2026-10-31T12:09:20.100+00:00", {
        interface: "0",
        details: { reason: "node_missing" }
      }),
      event("hid_interface_closed", "2026-10-31T12:09:20.200+00:00", {
        interface: "1",
        details: { reason: "node_missing" }
      }),
      event("recovery_started", "2026-10-31T12:09:20.300+00:00", { interface: "0" }),
      event("recovery_succeeded", "2026-10-31T12:09:35.000+00:00", {
        interface: "0",
        details: { attempts: 2 }
      })
    ]
  }

  const rows = model.logRows(status, 20)
  assert.deepStrictEqual(
    rows.map((row) => row.title),
    [
      "Link recovered",
      "Recovery started",
      "Interface disconnected",
      "Interface disconnected",
      "USB receiver removed",
      "Sentinel started",
      "Interface opened"
    ]
  )
  assert.strictEqual(rows[0].tone, "accent")
  assert.strictEqual(rows[0].detail, "after 2 attempts")
  assert.strictEqual(rows[2].detail, "node missing")
  assert.strictEqual(rows[3].detail, "node missing")
  assert.strictEqual(model.logInterfaceLabel(rows[0]), "IF 0")
  assert.strictEqual(model.logInterfaceLabel(rows[2]), "IF 1")
  assert.strictEqual(model.logInterfaceLabel(rows[3]), "IF 0")
  assert.strictEqual(model.logInterfaceLabel(rows[4]), "")
  assert.strictEqual(model.logInterfaceLabel(rows[6]), "IF 2")
  assert.strictEqual(rows[5].tone, "muted")
  assert.strictEqual(rows[6].tone, "foreground")
  assert.strictEqual(model.logTone("hid_interface_opened"), "foreground")
}

function testCleanShutdownClosuresStayOut() {
  const status = {
    event_log: [
      event("hid_interface_closed", "2026-10-31T12:00:00.000+00:00", {
        interface: "0",
        details: { reason: "shutdown" }
      }),
      event("service_stopped", "2026-10-31T12:00:01.000+00:00")
    ]
  }
  const rows = model.logRows(status)
  assert.strictEqual(rows.length, 1)
  assert.strictEqual(rows[0].title, "Sentinel stopped")
}

function testOldSnapshotFallsBackToEvents() {
  const status = {
    events: [
      event("device_stable", "2026-10-31T12:00:00.000+00:00", { interface: "0" }),
      event("monitor_error", "2026-10-31T12:00:05.000+00:00", {
        details: { error: "boom" }
      })
    ]
  }
  const rows = model.logRows(status)
  assert.strictEqual(rows.length, 1)
  assert.strictEqual(rows[0].title, "Monitor error")
  assert.strictEqual(rows[0].detail, "boom")
}

function testLimitAndEmptyState() {
  assert.deepStrictEqual(model.logRows(null), [])
  assert.deepStrictEqual(model.logRows({ event_log: [] }), [])

  const events = []
  for (let second = 0; second < 30; second++) {
    events.push(
      event("monitor_error", "2026-10-31T12:00:" + String(second).padStart(2, "0") + ".000+00:00")
    )
  }
  assert.strictEqual(model.logRows({ event_log: events }, 20).length, 20)
}

function testShortUptimeAndSessionLine() {
  assert.strictEqual(model.formatShortUptime(45), "45s")
  assert.strictEqual(model.formatShortUptime(754), "12m")
  assert.strictEqual(model.formatShortUptime(9240), "2h 34m")
  assert.strictEqual(model.formatShortUptime(273600), "3d 4h")
  assert.strictEqual(model.formatShortUptime(null), "0s")

  const idle = {
    uptime_seconds: 9240,
    summary: { open: 5, total: 5 },
    session: { recoveries: 0, disconnects: 0, last_recovery_at: null }
  }
  assert.strictEqual(model.sessionLine(idle), "5 HELD OPEN · UP 2h 34m · PROTECTED (0 DROPS)")
  assert.strictEqual(model.sessionStats(idle).open, 5)
  assert.strictEqual(model.sessionStats(idle).recoveries, 0)

  const repaired = {
    uptime_seconds: 30,
    summary: { open: 1, total: 5 },
    session: { recoveries: 1, disconnects: 1, last_recovery_at: "2026-10-31T12:00:00.000+00:00" }
  }
  assert.strictEqual(model.sessionLine(repaired), "1 HELD OPEN · UP 30s · 1 RECOVERY")
  assert.strictEqual(model.sessionStats(repaired).lastRecovery, "2026-10-31T12:00:00.000+00:00")

  // A snapshot from an older service has no session block: show zeros, not NaN.
  assert.strictEqual(model.sessionLine({ summary: { open: 2 } }), "2 HELD OPEN · UP 0s · PROTECTED (0 DROPS)")
  assert.strictEqual(model.sessionLine(null), "0 HELD OPEN · UP 0s · PROTECTED (0 DROPS)")
}

function testLocalTimeFormatting() {
  const now = new Date(2026, 9, 31, 12, 30, 0)
  const sameDay = new Date(2026, 9, 31, 12, 9, 35)
  const older = new Date(2026, 9, 30, 8, 5, 0)
  assert.strictEqual(model.formatEventTime(sameDay.toISOString(), now.getTime()), "12:09:35")
  assert.strictEqual(model.formatEventTime(older.toISOString(), now.getTime()), "30/10 08:05")
  assert.strictEqual(model.formatEventTime("not a date", now.getTime()), "")
}

const tests = [
  testLogRowsAreFilteredAndOrdered,
  testCleanShutdownClosuresStayOut,
  testOldSnapshotFallsBackToEvents,
  testLimitAndEmptyState,
  testShortUptimeAndSessionLine,
  testLocalTimeFormatting
]

for (const test of tests) {
  test()
  console.log("ok " + test.name)
}
console.log(tests.length + " model tests passed")
