4d-plugin-running-application
=============================

Collection of OS X native application manager commands.

### Platform

| carbon | cocoa | win32 | win64 |
|:------:|:-----:|:---------:|:---------:|
||<img src="https://cloud.githubusercontent.com/assets/1725068/22371562/1b091f0a-e4db-11e6-8458-8653954a7cce.png" width="24" height="24" />|||

### Version

<img width="32" height="32" src="https://user-images.githubusercontent.com/1725068/73986501-15964580-4981-11ea-9ac1-73c5cee50aae.png"> <img src="https://user-images.githubusercontent.com/1725068/73987971-db2ea780-4984-11ea-8ada-e25fb9c3cf4e.png" width="32" height="32" />

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
