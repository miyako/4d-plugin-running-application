![version](https://img.shields.io/badge/version-16%2B-8331AE)
![platform](https://img.shields.io/static/v1?label=platform&message=mac-intel%20|%20mac-arm&color=blue)
[![license](https://img.shields.io/github/license/miyako/4d-plugin-running-application)](LICENSE)
![downloads](https://img.shields.io/github/downloads/miyako/4d-plugin-running-application/total)

# 4d-plugin-running-application

This plugin is a thin wrapper around macOS's Cocoa application-management APIs (`NSWorkspace`, `NSRunningApplication`, `NSBundle`). It lets you enumerate, inspect, and control other applications running on the same Mac — list every running app, get an app's icon as a `Picture`, hide/show/activate/quit an app, and resolve an app's bundle identifier to a filesystem path, whether or not that app is currently running.

Every command that targets a specific application addresses it by **bundle identifier** (e.g. `"com.4d.4d"`), not by name or process ID.

---

## Summary table

| Command | Returns | Purpose |
|---|---|---|
| [App LIST](#app-list) | — (3 arrays) | List every currently running application |
| [App TERMINATE](#app-terminate) | — | Ask an application to quit normally |
| [App FORCE TERMINATE](#app-force-terminate) | — | Kill an application immediately |
| [App Is active](#app-is-active) | Longint | Is the application the active (frontmost) app? |
| [App ACTIVATE](#app-activate) | — | Bring an application to the front |
| [App Get icon](#app-get-icon) | Picture | Get an application's icon |
| [App HIDE](#app-hide) | — | Hide an application's windows |
| [App SHOW](#app-show) | — | Unhide an application |
| [App Is hidden](#app-is-hidden) | Longint | Is the application currently hidden? |
| [App Get path](#app-get-path) | Text | Path to a *running* application's bundle/executable |
| [App Get localized name](#app-get-localized-name) | Text | The application's localized display name |
| [App Find path](#app-find-path) | Text | Path to an *installed* application (need not be running) |

**Platforms:** macOS only (Intel and Apple Silicon). There is no Windows implementation.

---

## Requirements & platform notes

- **macOS only.** Every command is built on AppKit/Cocoa (`NSWorkspace`, `NSRunningApplication`, `NSBundle`); there's no Windows counterpart.
- **Not thread-safe.** All twelve commands are declared `threadSafe: false` in the plugin manifest. They call directly into AppKit, which is not safe to touch from arbitrary worker threads/processes, so 4D will always run these on the main process. Don't expect to gain concurrency by calling them from a preemptive method.
- **Addressing model — running vs. installed.** Every command except `App LIST` and `App Find path` locates its target through the list of *currently running* applications (`NSRunningApplication`), matched by exact bundle identifier. If no running app has that identifier, the command does nothing and returns an empty/zero result — **it does not raise a 4D error.** `App Find path` is the one exception: it resolves an installed application via the system's bundle registration and does **not** require the app to be running.
- **The `"__CURRENT__"` special value.** Anywhere a command takes a `bundleID` parameter, passing the literal text `"__CURRENT__"` targets the current process — i.e., the 4D application itself — instead of looking up a bundle identifier. This is an exact, case-sensitive string match.
- **`App Get icon` always renders at 1024×1024** and returns TIFF-encoded picture data, regardless of the application's actual icon resolution. If you call it in a loop over many applications, expect this to be the slowest command in the plugin.

---

## App LIST

### Syntax

```4d
App LIST ( aNames ; aBundleIDs ; aPIDs )
```

| Parameter | Type | Description |
|---|---|---|
| `aNames` | Array Text | Populated with the localized display name of every currently running application |
| `aBundleIDs` | Array Text | Populated with each application's bundle identifier, in the same order as `aNames` |
| `aPIDs` | Array Longint | Populated with each application's process ID (PID), in the same order as `aNames` |

This command has no function result; it fills the three arrays you pass in.

### Description

Enumerates every application `NSWorkspace` currently reports as running (foreground and background) at the moment the command executes, and fills all three arrays with one entry per application, index-aligned — `aNames{1}`, `aBundleIDs{1}`, and `aPIDs{1}` all describe the same application. All three arrays are resized to hold exactly one row per running application (any previous content is discarded).

If a running process has no bundle identifier (this does happen for some non-bundled or helper processes), the corresponding entry in `aBundleIDs` is an empty string rather than being skipped — the three arrays always stay the same length.

### Example

```4d
ARRAY TEXT($names; 0)
ARRAY TEXT($bundleIDs; 0)
ARRAY LONGINT($pids; 0)

App LIST($names; $bundleIDs; $pids)

For ($i; 1; Size of array($names))
	LOG EVENT(Into system standard outputs; $names{$i}+" ("+$bundleIDs{$i}+") pid="+String($pids{$i}))
End for
```

```4d
// Find the PID of a specific running app
ARRAY TEXT($names; 0)
ARRAY TEXT($bundleIDs; 0)
ARRAY LONGINT($pids; 0)

App LIST($names; $bundleIDs; $pids)

$pos:=Find in array($bundleIDs; "com.apple.finder")
If ($pos>=0)
	$finderPID:=$pids{$pos}
End if
```

---

## App TERMINATE

### Syntax

```4d
App TERMINATE ( bundleID )
```

| Parameter | Type | Description |
|---|---|---|
| `bundleID` | Text | Bundle identifier of the running application to close, or `"__CURRENT__"` for the current process |

No return value.

### Description

Asks the target application to quit normally — equivalent to the user choosing **Quit** from the app's menu, or `[NSRunningApplication terminate]`. The application gets a normal chance to respond (e.g. prompting to save unsaved documents) and **can decline or delay the request**; this command does not guarantee the process actually exits.

If no running application matches `bundleID`, the command silently does nothing.

### Example

```4d
App TERMINATE("com.apple.TextEdit")
```

---

## App FORCE TERMINATE

### Syntax

```4d
App FORCE TERMINATE ( bundleID )
```

| Parameter | Type | Description |
|---|---|---|
| `bundleID` | Text | Bundle identifier of the running application to kill, or `"__CURRENT__"` for the current process |

No return value.

### Description

Immediately terminates the target application (`[NSRunningApplication forceTerminate]`), equivalent to Force Quit — the application gets no opportunity to intervene, prompt, or save. Use this only when a normal `App TERMINATE` isn't appropriate, since any unsaved state in the target app is lost.

If no running application matches `bundleID`, the command silently does nothing.

### Example

```4d
If (Not(App TERMINATE("com.example.frozenapp"))) //(a normal quit isn't guaranteed to succeed)
	DELAY PROCESS(Current process; 300)
	App FORCE TERMINATE("com.example.frozenapp")
End if
```

Note: `App TERMINATE` has no return value in this plugin, so it cannot itself confirm success — the pattern above is illustrative only; you'd need `App LIST` or `App Is active` in a real script to check whether the app is still running before force-terminating it.

---

## App Is active

### Syntax

```4d
App Is active ( bundleID ) -> Result
```

| Parameter | Type | Description |
|---|---|---|
| `bundleID` | Text | Bundle identifier of the running application to check, or `"__CURRENT__"` |
| `Result` | Longint | `1` if the application is currently active (frontmost/key), `0` otherwise |

### Description

Returns whether the target application is the currently active application (`[NSRunningApplication isActive]`). If no running application matches `bundleID`, `Result` is `0` — the same value as "running but not active" — so this command cannot by itself distinguish "not the active app" from "not running at all."

### Example

```4d
If (App Is active("com.apple.finder")=1)
	ALERT("Finder is in front.")
End if
```

---

## App ACTIVATE

### Syntax

```4d
App ACTIVATE ( bundleID )
```

| Parameter | Type | Description |
|---|---|---|
| `bundleID` | Text | Bundle identifier of the running application to bring to the front, or `"__CURRENT__"` |

No return value.

### Description

Brings the target application's windows to the front (`NSApplicationActivateIgnoringOtherApps`), regardless of which application is currently frontmost. If no running application matches `bundleID`, the command silently does nothing.

### Example

```4d
App ACTIVATE("com.apple.finder")
```

---

## App Get icon

### Syntax

```4d
App Get icon ( bundleID ) -> Result
```

| Parameter | Type | Description |
|---|---|---|
| `bundleID` | Text | Bundle identifier of the running application whose icon to fetch, or `"__CURRENT__"` |
| `Result` | Picture | The application's icon, rendered up to 1024×1024 and TIFF-encoded |

### Description

Fetches the target application's icon (`[NSRunningApplication icon]`) and renders it into a `Picture` at up to 1024×1024 pixels, encoded as TIFF. `Result` is an empty picture if the application isn't running, has no icon, or if icon rendering fails at any stage (a defensive fallback, not something you should expect to hit in normal use).

Since every call renders and re-encodes a full-size TIFF, calling this in a tight loop (e.g. once per row after `App LIST`) is the most expensive command in this plugin — consider caching results per `bundleID` rather than re-fetching on every refresh.

### Example

```4d
$icon:=App Get icon("com.apple.finder")
FORM SET INPUT($icon)
```

```4d
// Build an icon for each running app (can be slow with many apps — see note above)
ARRAY TEXT($names; 0)
ARRAY TEXT($bundleIDs; 0)
ARRAY LONGINT($pids; 0)
ARRAY PICTURE($icons; 0)

App LIST($names; $bundleIDs; $pids)
ARRAY PICTURE($icons; Size of array($names))

For ($i; 1; Size of array($bundleIDs))
	$icons{$i}:=App Get icon($bundleIDs{$i})
End for
```

---

## App HIDE

### Syntax

```4d
App HIDE ( bundleID )
```

| Parameter | Type | Description |
|---|---|---|
| `bundleID` | Text | Bundle identifier of the running application to hide, or `"__CURRENT__"` |

No return value.

### Description

Hides all of the target application's windows (`[NSRunningApplication hide]`), equivalent to the user pressing Cmd+H while that app is active. If no running application matches `bundleID`, the command silently does nothing.

### Example

```4d
App HIDE("com.apple.finder")
```

---

## App SHOW

### Syntax

```4d
App SHOW ( bundleID )
```

| Parameter | Type | Description |
|---|---|---|
| `bundleID` | Text | Bundle identifier of the running application to unhide, or `"__CURRENT__"` |

No return value.

### Description

Unhides the target application (`[NSRunningApplication unhide]`), reversing `App HIDE`. If no running application matches `bundleID`, the command silently does nothing.

### Example

```4d
App SHOW("com.apple.finder")
```

---

## App Is hidden

### Syntax

```4d
App Is hidden ( bundleID ) -> Result
```

| Parameter | Type | Description |
|---|---|---|
| `bundleID` | Text | Bundle identifier of the running application to check, or `"__CURRENT__"` |
| `Result` | Longint | `1` if the application is currently hidden, `0` otherwise |

### Description

Returns whether the target application is currently hidden (`[NSRunningApplication isHidden]`). If no running application matches `bundleID`, `Result` is `0` — the same value as "running but not hidden" — so this command cannot by itself distinguish "not hidden" from "not running at all."

### Example

```4d
If (App Is hidden("com.apple.finder")=1)
	App SHOW("com.apple.finder")
End if
```

---

## App Get path

### Syntax

```4d
App Get path ( bundleID ) -> Result
```

| Parameter | Type | Description |
|---|---|---|
| `bundleID` | Text | Bundle identifier of the *running* application to locate, or `"__CURRENT__"` |
| `Result` | Text | Filesystem path to the application's bundle (or executable, as a fallback) |

### Description

Returns the filesystem path of the target application's `.app` bundle. If the running application has no bundle URL (rare — e.g. a bare executable with no `.app` wrapper), the command falls back to the executable's own path instead. `Result` is an empty string if no running application matches `bundleID`, or if neither a bundle nor an executable path could be resolved.

This command only finds applications that are **currently running** — see [App Find path](#app-find-path) for locating an installed application whether or not it's running.

The path is built with the (deprecated) `kCFURLHFSPathStyle` constant; on current macOS this constant no longer produces classic colon-separated HFS paths and instead yields an ordinary POSIX-style path (e.g. `/Applications/Finder.app`) — if you see something unexpected, check the exact formatting on your OS version.

### Example

```4d
$path:=App Get path("com.apple.finder")
If ($path#"")
	// e.g. "/System/Library/CoreServices/Finder.app"
End if
```

---

## App Get localized name

### Syntax

```4d
App Get localized name ( bundleID ) -> Result
```

| Parameter | Type | Description |
|---|---|---|
| `bundleID` | Text | Bundle identifier of the *running* application to look up, or `"__CURRENT__"` |
| `Result` | Text | The application's localized display name |

### Description

Returns the target application's localized display name (`[NSRunningApplication localizedName]`) — the same name shown in the Dock/menu bar, respecting the system's current language. `Result` is an empty string if no running application matches `bundleID`.

### Example

```4d
$name:=App Get localized name("com.apple.finder")
```

---

## App Find path

### Syntax

```4d
App Find path ( bundleID ) -> Result
```

| Parameter | Type | Description |
|---|---|---|
| `bundleID` | Text | Bundle identifier of the *installed* application to locate |
| `Result` | Text | Filesystem path to the application's bundle |

### Description

Resolves `bundleID` to an installed application's bundle path via `NSBundle bundleWithIdentifier:`, which is backed by the system's application registration (Launch Services) rather than the list of running processes. **Unlike every other command in this plugin, the application does not need to be running** — this is the command to use to check whether/where an application is installed at all.

`Result` is an empty string if no installed bundle matches `bundleID`. Note `"__CURRENT__"` is **not** a recognized value here (it's only handled by the running-application lookup used by the other commands) — pass a real bundle identifier.

Like `App Get path`, the path is built with the deprecated `kCFURLHFSPathStyle` constant, which on current macOS yields an ordinary POSIX-style path rather than a classic HFS path.

### Example

```4d
$path:=App Find path("com.4d.4d")
```

```4d
// Check whether an app is installed before trying to work with it
$path:=App Find path("com.example.myapp")
If ($path="")
	ALERT("myapp is not installed on this machine.")
End if
```

---

## Error handling & troubleshooting

- **Most failures are silent, not 4D errors.** Every command that targets a running application (all except `App LIST` and `App Find path`) simply does nothing, or returns an empty string / `0`, when `bundleID` doesn't match a currently running app. Check `App LIST` or `App Is active` first if you need to distinguish "not running" from a genuine failure.
- **`0` is overloaded for `App Is active` and `App Is hidden`.** Both return `0` for "running but not active/hidden" and for "not running at all" — there's no separate signal for the two cases from these commands alone.
- **`App Get path` only sees running applications; `App Find path` only sees installed ones.** These are not interchangeable — an installed-but-not-running app returns a path from `App Find path` but an empty string from `App Get path`, and vice versa is not possible (a running app is always installed).
- **`"__CURRENT__"` is exact and case-sensitive**, and is only understood by the running-application commands (not by `App Find path`).
- **`App Get icon` can return an empty `Picture`.** This happens if the application isn't running, has no icon, or if any step of the TIFF rendering pipeline fails — always safe to check for an empty result before using it.
- **These commands are not thread-safe** and always run on the main process; don't route them through a preemptive process expecting concurrency gains.
- **`App Get path` / `App Find path` path formatting.** Both use the deprecated `kCFURLHFSPathStyle` constant, which behaves as a plain POSIX path on current macOS rather than a classic HFS colon-path — worth a quick check if your code parses the returned string.

---

## Quick reference

```4d
// Enumerate running apps
ARRAY TEXT($names; 0)
ARRAY TEXT($bundleIDs; 0)
ARRAY LONGINT($pids; 0)
App LIST($names; $bundleIDs; $pids)

// Inspect / control one app by bundle ID
$id:="com.apple.finder"
$isRunning:=(Find in array($bundleIDs; $id)>=0)
$isActive:=(App Is active($id)=1)
$isHidden:=(App Is hidden($id)=1)
$icon:=App Get icon($id)
$name:=App Get localized name($id)
$runningPath:=App Get path($id)      // "" if not running
$installedPath:=App Find path($id)   // "" if not installed at all

App ACTIVATE($id)
App HIDE($id)
App SHOW($id)
App TERMINATE($id)        // graceful, may be declined by the app
App FORCE TERMINATE($id)  // immediate, no chance to intervene

// Target the current 4D process itself
App ACTIVATE("__CURRENT__")
```
