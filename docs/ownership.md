# Native ownership audit

## Immutable inputs and build boundary

Baseline: `53da23011c26c054e6d1379901b2e6819e7554ec`.
Guidance: `miyako/skills`, `4d-skills/skills/4dplugin/SKILL.md` at
`e6b492fcb81e5e1a7f7db723594b420e63bf6eb9`, read through the immutable
GitHub contents API, without another checkout.

The bundled API identifies itself as `rev : v14`; that comment is not an
upstream release certification. Its exact Git blobs, all unchanged:

| Path under `4D Plugin API/` | Blob |
|---|---|
| `4DPluginAPI.c` | `62f745e91d81ef2c3c901c656ea8e175d3b173e8` |
| `4DPluginAPI.h` | `528bf1d2552df08d6a8b1bc95011a9b72934ad5c` |
| `4DPluginAPI.def` | `5bbdb82fb724b6f8e307feb01d9f158b802de89d` |
| `EntryPoints.h` | `da428d5f6ca23b0a30371c7288a01d29aa3c466a` |
| `Flags.h` | `7a5a35539f27430a3fd9cb711d6b789051316471` |
| `PrivateTypes.h` | `a3098cb662cdbc43ba574fdef72a059c4c6d1cd4` |
| `PublicTypes.h` | `9ae20b001bff9128f690fff3e6c3f873b6238333` |

Both existing workflows (`bump-version.yml`, `release.yml`) are manual-only.
The Xcode scheme selects Deployment for launch/profile/archive; its Deployment
shell phase signs, packages, notarizes and staples. No project build, manual
workflow dispatch, release, signing, packaging, installation or binary change
is part of this audit. Visual Studio has only API and dispatcher source
membership and no Windows implementation of this Cocoa-only dispatcher.
Its configuration/link settings are not evidence of working Windows support.

## Complete tracked native-source inventory

This inventory includes dormant files rather than treating project membership,
an indexed search absence, or manifest declarations as proof of safety.
The harness adds no production source membership.

| Files | Membership and ownership surfaces |
|---|---|
| `4DPlugin-Running-Application.cpp`, `.h` | Xcode dispatcher, all 12 command selectors; also named by the stale Visual Studio project. `_getApp` copies/releases its bundle-identifier NSString. List copies Cocoa text before its autorelease pool ends. Scalar input is copied into C++ storage; outputs use the SDK content setter. Icon creates a picture and hands its sole owner to the return slot. |
| `4D Plugin API/Classes/ARRAY_TEXT.cpp`, `.h` | Xcode; C++ vector copies borrowed array elements before replacement. Writer creates a fresh owned string for every element, including index zero, and does not dispose after accepted transfer. UTF-8/UTF-16, Cocoa string and path helpers inspected, including methods not called by the dispatcher. One declared Unistring indexed-setter overload has no definition or call site. |
| `4D Plugin API/Classes/C_TEXT.cpp`, `.h` | Xcode; scalar parameter copies, content-setter output, UTF conversion and Cocoa path helpers. C++ and NSString allocations are independent of array allocations. |
| `4D Plugin API/Classes/ARRAY_LONGINT.cpp`, `.h` | Xcode; array/handle creation and resizing, no string owner. |
| `4D Plugin API/Classes/C_LONGINT.cpp`, `.h` | Xcode; numeric parameters/returns, no string allocation. |
| `4D Plugin API/Classes/C_BLOB.cpp`, `.h` | Dormant in both projects; handle/byte storage, text encoders/decoders. Compiled and a nonempty synthetic blob roundtrip executed by the harness. No CPicture class or picture converter exists in this target. |
| `4D Plugin API/Classes/C_TYPES.h` | Shared C++ owning string/vector aliases and platform includes, not an engine implementation. |
| `support/4DPlugin-JSON.cpp`, `.h` | Dormant in both projects; no production call site. All wide/narrow scalar string setters, picture/object/collection setters, property getters, key conversion and stringify inspected. Confirmed defects below. Baseline implementation blob `a5122d68e881476b97332a6a4f40a7b3e3adeb3f`. |
| `4D Plugin API/4DPluginAPI.c`, `.h`, `PrivateTypes.h`, `PublicTypes.h`, `EntryPoints.h`, `Flags.h`, `4DPluginAPI.def` | Complete unchanged SDK, engine/ABI headers and sole Windows export `FourDPackex`. Exact ownership implementations below; no SDK rewrites. SDK-internal generic callback/command/array capability is not a production plugin call site. |
| `include/json/{allocator,assertions,autolink,config,features,forwards,json,reader,value,version,writer}.h` | Dormant jsoncpp declarations, no PA string/reference ownership calls. Prebuilt `a/libjsoncpp.a`, `lib/windows/jsoncpp.lib`, `lib/windows64/jsoncpp.lib` are not linked by the Xcode source target or loaded by the harness. Not rebuilt or certified. |

No other tracked C/C++/Objective-C source paths exist at the baseline.
Build membership comes from `Running Application.xcodeproj/project.pbxproj`,
`Running Application.vcxproj` and its filters, not from filename inference.
Tracked ZIP/DMG, plugin bundles, Windows binaries and 4D database fixtures were
neither executed nor modified. The 4D test project contains a manifest-update
method, not native ownership regression coverage.

`manifest.json` has selector 1 `App LIST(&Y;&Y;&Y)` (two text arrays and one
longint array); selectors 2-12 take scalar `&T`. Selectors 4 and 9 return
longint, 6 returns picture, and 10-12 return scalar text. No object, collection,
callback or internal text-array call is hidden behind another plugin command.
All exposed selectors are exercised offline; the legacy list convention
(first value at index zero; logical count N-1 for N nonempty fixture rows)
is deliberately preserved, not repaired as part of this ownership change.

## Exact SDK contracts and fixes

`PA_CreateVariable(eVK_Unistring)` creates an owned empty Unistring
(API lines 4029-4033); `PA_SetStringVariable` (4254-4259) overwrites the
descriptor shallowly. The four dormant JSON text-setter overloads now start
Undefined instead of leaking that initialized empty owner. The picture setter
likewise starts Undefined instead of losing its initial picture. The adopted
string/reference is still cleared once after `PA_SetObjectProperty` copies the
value; cleanup has not been removed indiscriminately.

`PA_GetObjectProperty` (637-652) explicitly returns a variable to clear.
All five JSON getters now clean successful results, including wrong-kind
strings, pictures, objects and collections. Matching object/collection getters
still hand the returned owner to the caller: the descriptor guard is disarmed,
not the underlying reference destroyed. Reference-valued setters retain their
existing consuming contract when object/value are non-null; callers must
supply an owner, not an independently borrowed reference.

Property access and command errors are checked before kind inspection,
adoption or cleanup of their unspecified output. Key guards clean independently
valid keys during exceptions. `PA_ExecuteCommandByID` (5778-5812) returns
zero-initialized raw type zero if its argument-handle allocation fails;
`UnifyVarKind` (1713-1736) maps zero to string. A kind check alone is unsafe.
Stringify therefore executes OB Copy directly to check its error before
adopting the output, captures stringify's error before clearing the independent
synchronous argument owner, and only then guards the successful result.
Successful wrong-kind command output is cleared and reported by exception.
Failed `PA_GetStringVariable` output is not inspected either.
The dormant helpers report these failures with `std::runtime_error`; there is
no existing helper logging/error channel, and no new dispatcher catch/fallback.

`PA_GetStringInArray` (4738-4755) is borrowed.
`PA_SetStringInArray` (4933-4947) disposes the old element and shallowly assigns
the new owner only for Unicode-array kind, non-null data, index in
`0..fNbElements`, and non-null descriptor. Index zero is accepted. Rejection
does not transfer. The production writer already follows the successful
transfer contract and is unchanged. `PA_SetUnistring` (289-299) forwards a
UTF-16 buffer to the engine content setter; it is not array struct transfer.
There are no plugin-owned callback arguments or CPicture wrappers to patch.

## Reproducible offline evidence

On macOS, without project build phases:

```sh
python3 .github/tests/test_ownership.py
python3 .github/tests/test_ownership.py --baseline
```

The same final harness compiles nine actual translation units: complete
`4DPluginAPI.c`, all five wrapper implementations (including dormant C_BLOB),
JSON helpers, actual dispatcher and host. It checks all seven protected SDK
blob identities before compilation. Baseline mode extracts the immutable Git
archive into a temporary directory, using the final harness unchanged.
ASan and UBSan are enabled with fail-fast diagnostics; leak checking is by
explicit string/picture/object/collection/handle owner balances because macOS
ASan does not supply the needed LeakSanitizer coverage here.

Before any translation unit is compiled, a forced include redirects all
application/workspace/bundle/image classes, URL path operations and image
destination operations to synthetic implementations. Objective-C stubs expose
only the selectors used by actual source; unexpected SDK selectors abort.
Object-file imports are checked for original platform class/function names
and process/shell imports before linking. Real Cocoa/CoreFoundation string and
container allocation/conversion remains available; no actual applications,
process enumeration, launches, termination, activation, UI, platform discovery,
filesystem URLs, shells from plugin code, or private data are accessed.
The exported `FourDPackex` entry and real PluginMain route all 12 selectors.

The baseline fails six named groups: scalar-setters (13 leaked strings with
allocated empties), picture-setter (one leaked picture), property-getters
(three leaked strings), property-mismatches (strings, pictures and references),
property-errors (missing failure notification), and stringify-errors (failed
command output accepted). Five controls pass: stringify-success, arrays,
text, dispatcher and blob. Fixed sources pass all eleven groups, with exact
owner balances and unchanged control results.

Fixtures cover ASCII, BMP, supplementary surrogate pairs, empty UTF-16 strings
under both allocated-empty and null-empty engines, exact lengths/capacity/
terminators, distinct element allocations, borrowed-copy-before-replace,
20 replacement cycles, shrink/clear, and every setter rejection predicate.
Property tests include matching owning object/collection handoffs, owning
wrong-kind results, property existence/fetch errors and poisoned string getter
outputs. Stringify tests include both command-handle allocation failures,
both command errors, successful wrong kinds and string getter errors. Failed
outputs are poisoned/raw-zero; reading or clearing them is rejected by the host.

## Explicit deferrals

This proves the named paths under the synthetic host contracts, not actual
4D host/engine ABI behavior, Windows execution, or a reproduced Windows crash.
Engine reference-copy behavior is modeled at the SDK boundary, not a claim
to have executed proprietary 4D internals. SDK-internal helpers are unchanged,
including their own generic failure/ownership limitations.

Array OOM preservation is not certified: `PA_ResizeArray` (4566-4604) updates
the logical count even when physical resize fails; its void wrapper has no
safe failure-preserving boundary here. Creation/lock/resize failures in legacy
array/BLOB wrappers and conversion allocation failures need a separately
designed error/transaction contract. In particular dormant CBytes disposes an
old output handle before allocating its replacement. This audit does not
silently replace failed output with empty success or rewrite SDK metadata.
Successful array/handle controls do not establish OOM safety. Nor does compiling
all wrapper methods certify unexecuted encoders, declared-only methods or
binary-only dependencies.

The new Ownership workflow is unsigned and path-restricted to relevant source
and harness paths, on the exact audit branch and master. It runs only these
two harness commands, never existing release workflows or publication tools.
