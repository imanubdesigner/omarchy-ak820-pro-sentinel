.pragma library

// Parse the status snapshot written by the service. A missing or partial
// document is not an error: the panel simply reports that no data is available.
function parse(text) {
  try {
    var data = JSON.parse(String(text || ""))
    if (!data || typeof data !== "object" || Array.isArray(data)) return null
    return data
  } catch (error) {
    return null
  }
}

function stateOf(status) {
  if (!status) return "unknown"
  if (status.running === false) return "stopped"
  var receiver = status.receiver || {}
  var summary = status.summary || {}
  var interfaces = status.interfaces || []
  if (receiver.present === false || receiver.state === "disconnected") return "disconnected"
  if ((summary.errors || 0) > 0) return "error"
  if ((summary.recovering || 0) > 0) return "recovering"
  if ((summary.stable || 0) > 0) return "stable"
  if ((summary.open || 0) > 0) return "connected"
  for (var i = 0; i < interfaces.length; i++) {
    if (interfaces[i].state === "error") return "error"
    if (interfaces[i].state === "recovering") return "recovering"
  }
  return "detected"
}

// A cleanly stopped service keeps its file and reports stopped forever. A
// service that died without writing `running: false` leaves a file that stops
// changing, so an old timestamp is the only visible symptom.
function isStale(status, nowMs, limitSeconds) {
  if (!status || status.running === false) return false
  if (!status.updated_at) return false
  var updated = Date.parse(status.updated_at)
  if (isNaN(updated)) return false
  return nowMs - updated > limitSeconds * 1000
}

function stateLabel(state) {
  switch (state) {
    case "stable":
      return "CONNECTED"
    case "connected":
      return "CONNECTED"
    case "recovering":
      return "RECOVERING"
    case "disconnected":
      return "DISCONNECTED"
    case "detected":
      return "DETECTED"
    case "error":
      return "ERROR"
    case "stale":
      return "STALE"
    case "stopped":
      return "SERVICE STOPPED"
    default:
      return "NO DATA"
  }
}

// A readable sentence for the panel header and the bar tooltip. The product
// name is fixed: the USB receiver reports itself as "2.4G Dongle".
function statusSentence(state) {
  switch (state) {
    case "stable":
    case "connected":
      return "AK820 Pro connected"
    case "recovering":
      return "AK820 Pro recovering"
    case "detected":
      return "AK820 Pro detected"
    case "disconnected":
      return "AK820 Pro not detected"
    case "error":
      return "AK820 Pro error"
    case "stale":
      return "AK820 Pro status stale"
    case "stopped":
      return "AK820 Pro service stopped"
    default:
      return "AK820 Pro no data"
  }
}

// Colors always come from the active theme. The caller passes the theme
// values so QML can track them as binding dependencies.
function stateColor(state, accent, foreground, muted, urgent) {
  switch (state) {
    case "stable":
    case "connected":
      return accent
    case "detected":
      return foreground
    case "recovering":
    case "disconnected":
    case "error":
    case "stale":
      return urgent
    case "stopped":
      return muted
    default:
      return muted
  }
}

// Human readable titles for the significant events kept in status.event_log.
// The backend already drops routine noise; this map also filters snapshots
// written by an older service that only had the raw events array.
var LOG_TITLES = {
  "usb_receiver_removed": "USB receiver removed",
  "usb_receiver_detected": "USB receiver detected",
  "hid_interface_opened": "Interface opened",
  "hid_interface_closed": "Interface disconnected",
  "hid_open_failed": "Cannot open interface",
  "hid_read_error": "Read error",
  "hid_select_error": "Select error",
  "recovery_started": "Recovery started",
  "recovery_succeeded": "Link recovered",
  "monitor_error": "Monitor error",
  "wired_connected": "Cable connected, charging",
  "wired_removed": "Cable removed",
  "service_started": "Sentinel started",
  "service_stopped": "Sentinel stopped"
}

// Tone names are resolved by the panel against the active theme. There is no
// fixed hue here: accent for a good outcome, urgent for a problem, muted for
// service context.
function logTone(type, event) {
  if (type === "recovery_succeeded" || type === "usb_receiver_detected"
      || type === "wired_connected") return "accent"
  if (type === "hid_interface_opened") {
    var details = (event && event.details) || {}
    if (details.is_lifeline || (event && (event.interface === "3" || event.interface === "4"))) {
      return "accent"
    }
    return "foreground"
  }
  if (type === "service_started" || type === "service_stopped") return "muted"
  return "urgent"
}

function logTitle(event) {
  if (!event) return ""
  if (event.type === "hid_interface_opened") {
    var details = event.details || {}
    if (details.is_lifeline || event.interface === "3" || event.interface === "4") {
      return "Lifeline interface opened"
    }
  }
  return LOG_TITLES[event.type] || event.type
}

function logDetail(event) {
  var details = event.details || {}
  if (event.type === "recovery_succeeded") {
    var attempts = Number(details.attempts || 0)
    if (attempts > 0) {
      return "after " + attempts + (attempts === 1 ? " attempt" : " attempts")
    }
  }
  if (event.type === "hid_interface_opened") {
    var dev = details.device || ""
    if (details.is_lifeline || event.interface === "3" || event.interface === "4") {
      return dev ? dev + " (lifeline keepalive)" : "lifeline keepalive"
    }
    return dev ? dev + " (held open)" : "held open"
  }
  if (event.type === "hid_interface_closed" && details.reason) {
    return String(details.reason).replace(/_/g, " ")
  }
  if (details.error) return String(details.error)
  return ""
}

// Newest first. Every event gets its own row, so the log shows exactly which
// interface disconnected, reopened or recovered: the receiver exposes five HID
// interfaces and a single unplug produces one row per interface.
function logRows(status, limit) {
  if (!status) return []
  var events = Array.isArray(status.event_log) ? status.event_log : status.events
  if (!Array.isArray(events)) return []

  var rows = []
  for (var i = events.length - 1; i >= 0; i--) {
    var event = events[i]
    if (!event || !LOG_TITLES[event.type]) continue
    if (
      event.type === "hid_interface_closed" &&
      event.details &&
      event.details.reason === "shutdown"
    ) {
      continue
    }
    rows.push({
      type: event.type,
      timestamp: event.timestamp,
      title: logTitle(event),
      detail: logDetail(event),
      tone: logTone(event.type, event),
      interface: event.interface === null || event.interface === undefined
        ? ""
        : String(event.interface)
    })
  }

  var cap = limit === undefined ? 20 : Math.max(0, Number(limit) || 0)
  return cap > 0 ? rows.slice(0, cap) : rows
}

function logInterfaceLabel(row) {
  if (!row || !row.interface) return ""
  return "IF " + row.interface
}

// Local time for a log row. Older events also show the day and month, which
// matters when the service has been up for more than a day.
function formatEventTime(timestamp, nowMs) {
  var date = new Date(String(timestamp || ""))
  if (isNaN(date.getTime())) return ""
  var now = new Date(Number(nowMs) || Date.now())
  var hours = pad(date.getHours())
  var minutes = pad(date.getMinutes())
  var seconds = pad(date.getSeconds())
  var sameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate()
  if (sameDay) return hours + ":" + minutes + ":" + seconds
  return pad(date.getDate()) + "/" + pad(date.getMonth() + 1) + " " + hours + ":" + minutes
}

function formatUptime(seconds) {
  var total = Math.max(0, Math.floor(Number(seconds) || 0))
  var hours = Math.floor(total / 3600)
  var minutes = Math.floor((total % 3600) / 60)
  var rest = total % 60
  return pad(hours) + ":" + pad(minutes) + ":" + pad(rest)
}

function pad(value) {
  return value < 10 ? "0" + value : String(value)
}

// Compact duration for the log header: 45s, 12m, 2h 34m, 3d 4h.
function formatShortUptime(seconds) {
  var total = Math.max(0, Math.floor(Number(seconds) || 0))
  var days = Math.floor(total / 86400)
  var hours = Math.floor((total % 86400) / 3600)
  var minutes = Math.floor((total % 3600) / 60)
  if (days > 0) return days + "d " + hours + "h"
  if (hours > 0) return hours + "h " + minutes + "m"
  if (minutes > 0) return minutes + "m"
  return total + "s"
}

// Session counters come from the service and survive routine stability, so
// they answer "has it ever repaired something?" without reading the journal.
function sessionStats(status) {
  var session = (status && status.session) || {}
  var summary = (status && status.summary) || {}
  return {
    open: Number(summary.open || 0),
    total: Number(summary.total || 0),
    uptime: formatShortUptime(status ? status.uptime_seconds : 0),
    recoveries: Number(session.recoveries || 0),
    disconnects: Number(session.disconnects || 0),
    lastRecovery: session.last_recovery_at || null
  }
}

// "5 HELD OPEN · UP 2h 34m ·" plus the recovery count on the next call, so the
// panel can tint just the number.
function sessionHoldLine(status) {
  var stats = sessionStats(status)
  return stats.open + " HELD OPEN · UP " + stats.uptime + " ·"
}

function sessionRecoveryLine(status) {
  var stats = sessionStats(status)
  if (stats.recoveries === 0) {
    return "PROTECTED (0 DROPS)"
  }
  return stats.recoveries + " " + (stats.recoveries === 1 ? "RECOVERY" : "RECOVERIES")
}

function sessionLine(status) {
  return sessionHoldLine(status) + " " + sessionRecoveryLine(status)
}

function receiverValue(status) {
  if (!status) return "No data"
  var receiver = status.receiver || {}
  return receiver.present === true ? "Detected" : "Not detected"
}

function receiverDetail(status) {
  if (!status) return ""
  var receiver = status.receiver || {}
  var vendor = String(receiver.vendor_id || "").toUpperCase()
  var product = String(receiver.product_id || "").toUpperCase()
  if (vendor && product) return vendor + ":" + product
  return ""
}

function interfacesValue(status) {
  if (!status) return "No data"
  var summary = status.summary || {}
  var open = summary.open || 0
  var total = summary.total || 0
  if (total === 0) return "None"
  return open + " of " + total + " open"
}

function recoveryValue(status) {
  if (!status) return "Idle"
  var recovery = status.recovery || {}
  var pending = recovery.pending || []
  if (pending.length === 0) {
    if (recovery.active === true) return "Stabilizing"
    var summary = status.summary || {}
    if (summary.open >= 2) return "Active (lifeline protecting)"
    return "Idle"
  }
  var first = pending[0]
  var retry = first.retry_in_seconds
  var suffix = retry === null || retry === undefined ? "" : " in " + formatDelay(retry)
  return "Attempt " + (first.attempts || 0) + suffix
}

function formatDelay(seconds) {
  var value = Math.max(0, Math.ceil(Number(seconds) || 0))
  return value + "s"
}

function lastEventValue(status) {
  if (!status) return "No recent events"
  var event = status.last_event
  if (!event || !event.message) return "No recent events"
  return capitalize(String(event.message))
}

function capitalize(text) {
  if (!text) return text
  return text.charAt(0).toUpperCase() + text.slice(1)
}

function lastEventType(status) {
  if (!status || !status.last_event) return ""
  return String(status.last_event.type || "").toUpperCase()
}

function uptimeValue(status) {
  if (!status) return "00:00:00"
  return formatUptime(status.uptime_seconds)
}

function rows(status, stateKey) {
  var state = stateKey || stateOf(status)
  return [
    {
      label: "STATUS",
      value: stateLabel(state),
      tone: state
    },
    {
      label: "USB RECEIVER",
      value: receiverValue(status),
      detail: receiverDetail(status)
    },
    {
      label: "HID INTERFACES",
      value: interfacesValue(status)
    },
    {
      label: "BATTERY",
      value: chargingValue(status),
      tone: chargingTone(status)
    },
    {
      label: "RECOVERY ENGINE",
      value: recoveryValue(status)
    },
    {
      label: "LAST EVENT",
      value: lastEventValue(status)
    },
    {
      label: "UPTIME",
      value: uptimeValue(status)
    }
  ]
}

// The keyboard shows up as a second USB device when the cable is plugged in.
function isCharging(status) {
  return !!(status && status.wired && status.wired.present === true)
}

function chargingValue(status) {
  if (!isCharging(status)) return "Not charging"
  var since = Date.parse(String(status.wired.since || ""))
  var updated = Date.parse(String(status.updated_at || ""))
  if (isNaN(since) || isNaN(updated)) return "Charging (cable connected)"
  return "Charging for " + formatShortUptime(Math.max(0, (updated - since) / 1000))
}

function chargingTone(status) {
  return isCharging(status) ? "accent" : "muted"
}
