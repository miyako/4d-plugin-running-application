![version](https://img.shields.io/badge/version-16%2B-8331AE)
![platform](https://img.shields.io/static/v1?label=platform&message=mac-intel%20|%20mac-arm&color=blue)
[![license](https://img.shields.io/github/license/miyako/4d-plugin-running-application)](LICENSE)
![downloads](https://img.shields.io/github/downloads/miyako/4d-plugin-running-application/total)

**Note**: for v17 and earlier, move `manifest.json` to `Contents`

4d-plugin-running-application
=============================

Collection of OS X native application manager commands.

### Commands

```
// --- App
App_LIST
App_TERMINATE
App_FORCE_TERMINATE
App_Is_active
App_ACTIVATE
App_Get_icon
App_HIDE
App_SHOW
App_Is_hidden
App_Get_path
App_Get_localized_name
App_Find_path
```

### Examples

```4d
$path:=App Find path ("com.4d.4d")
```
