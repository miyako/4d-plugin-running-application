#!/usr/bin/env python3
"""Byte-preserving source version protocol for this project's coordinated policy."""

import argparse
from dataclasses import dataclass
from pathlib import Path
import plistlib
import re
import sys
import xml.etree.ElementTree as ET

PROJECT = Path("Running Application.xcodeproj/project.pbxproj")
PLIST = Path("Info.plist")
LOCALIZATIONS = (Path("English.lproj/InfoPlist.strings"),)
SOURCE_PATHS = (PROJECT, PLIST, *LOCALIZATIONS)
VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
REFERENCE = re.compile(
    r"\$(?:\((MARKETING_VERSION|CURRENT_PROJECT_VERSION)\)|"
    r"\{(MARKETING_VERSION|CURRENT_PROJECT_VERSION)\})\Z")


def canonical(value):
    if not isinstance(value, str) or not VERSION.fullmatch(value):
        raise ValueError(f"Noncanonical version: {value!r}")
    return value


@dataclass(frozen=True)
class Text:
    raw: bytes
    text: str
    encoding: str
    bom: bytes

    @classmethod
    def decode(cls, raw):
        for bom, encoding in ((b"\xff\xfe", "utf-16-le"),
                              (b"\xfe\xff", "utf-16-be"),
                              (b"\xef\xbb\xbf", "utf-8")):
            if raw.startswith(bom):
                return cls(raw, raw[len(bom):].decode(encoding), encoding, bom)
        if b"\0" in raw:
            if len(raw) % 2:
                raise ValueError("Invalid BOMless UTF-16")
            even, odd = raw[0::2].count(0), raw[1::2].count(0)
            if even == odd:
                raise ValueError("Ambiguous encoding")
            encoding = "utf-16-le" if odd > even else "utf-16-be"
        else:
            encoding = "utf-8"
        return cls(raw, raw.decode(encoding), encoding, b"")

    def render(self, replacements):
        text = self.text
        last = len(text) + 1
        for start, end, value in sorted(replacements, reverse=True):
            if not 0 <= start <= end < last:
                raise ValueError("Overlapping replacements")
            text = text[:start] + value + text[end:]
            last = start
        return self.bom + text.encode(self.encoding)


@dataclass(frozen=True)
class Token:
    value: str
    start: int
    end: int
    quoted: bool = False
    scope: tuple = ()


def tokens(text):
    pattern = re.compile(
        r'\s+|/\*[\s\S]*?\*/|//[^\r\n]*|"(?:\\.|[^"\\])*"|'
        r'\$(?:\([A-Z_]+\)|\{[A-Z_]+\})|[{}()=;,]|(?:(?!/\*|//)[^\s{}()=;,"])+'
    )
    result = []
    offset = 0
    for match in pattern.finditer(text):
        if match.start() != offset:
            raise ValueError(f"Unrecognized syntax at {offset}")
        offset = match.end()
        value = match.group()
        if value.isspace() or value.startswith(("/*", "//")):
            continue
        if value.startswith('"'):
            inner = value[1:-1]
            inner = re.sub(r'\\(["\\])', r'\1', inner)
            result.append(Token(inner, match.start() + 1, match.end() - 1, True))
        else:
            result.append(Token(value, match.start(), match.end()))
    if offset != len(text):
        raise ValueError(f"Unrecognized syntax at {offset}")
    return result


def assignments(text, wanted):
    items = tokens(text)
    scope, serial, result, seen = [], 0, [], set()
    for i, token in enumerate(items):
        if not token.quoted and token.value == "{":
            serial += 1
            scope.append(serial)
        elif not token.quoted and token.value == "}":
            if not scope:
                raise ValueError("Unbalanced dictionary")
            scope.pop()
        if token.value not in wanted or i + 3 >= len(items):
            continue
        equals, value, end = items[i + 1:i + 4]
        if equals.quoted or equals.value != "=":
            continue
        if end.quoted or end.value != ";" or (not value.quoted and value.value in "{}()=;,"):
            raise ValueError(f"Invalid scalar {token.value}")
        key = (tuple(scope), token.value)
        if key in seen:
            raise ValueError(f"Duplicate assignment {token.value}")
        seen.add(key)
        result.append((token.value, Token(value.value, value.start, value.end,
                                          value.quoted, tuple(scope))))
    if scope:
        raise ValueError("Unbalanced dictionary")
    return result


def plist_fields(raw):
    if raw.startswith(b"bplist"):
        data = plistlib.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Expected plist dictionary")
        return data, None, {}
    source = Text.decode(raw)
    parse_text = re.sub(r'(<\?xml[^>]*encoding=)["\'][^"\']+["\']',
                        r'\1"UTF-8"', source.text, count=1)
    data = plistlib.loads(parse_text.encode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Expected plist dictionary")
    root = ET.fromstring(source.text)
    dictionary = root.find("dict")
    if dictionary is None:
        raise ValueError("Expected plist dictionary")
    keys = [node.text for node in dictionary if node.tag == "key"]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate plist keys")
    spans = {}
    lexical_xml = re.sub(r"<!--[\s\S]*?-->", lambda match: " " * len(match[0]), source.text)
    for match in re.finditer(r"<key>([^<]+)</key>\s*<string>([^<]*)</string>", lexical_xml):
        if match[1] in ("CFBundleShortVersionString", "CFBundleVersion"):
            spans[match[1]] = (match.start(2), match.end(2))
    return data, source, spans


def resolve(value, settings, visiting=()):
    match = REFERENCE.fullmatch(value) if isinstance(value, str) else None
    if match:
        key = match[1] or match[2]
        if key in visiting or key not in settings:
            raise ValueError("Unknown or cyclic build reference")
        return resolve(settings[key], settings, (*visiting, key))
    return canonical(value)


def localized_fields(raw):
    source = Text.decode(raw)
    fields = assignments(source.text, {"CFBundleShortVersionString", "CFBundleVersion",
                                       "CFBundleGetInfoString"})
    result = []
    for key, token in fields:
        if key == "CFBundleGetInfoString":
            matches = list(re.finditer(r"\bversion ([^,\s]+)", token.value))
            if len(matches) != 1:
                raise ValueError("Expected one localized GetInfo version")
            match = matches[0]
            result.append((key, Token(match[1], token.start + match.start(1),
                                      token.start + match.end(1))))
        else:
            result.append((key, token))
    if not any(key == "CFBundleShortVersionString" for key, _ in result):
        raise ValueError("Missing localized short version")
    return source, result


def read(root=Path("."), overrides=None):
    overrides = overrides or {}
    raw = {path: overrides[path] if path in overrides else (root / path).read_bytes()
           for path in SOURCE_PATHS}
    project = Text.decode(raw[PROJECT])
    fields = assignments(project.text, {"MARKETING_VERSION", "CURRENT_PROJECT_VERSION"})
    scopes = {}
    for key, token in fields:
        scopes.setdefault(token.scope, set()).add(key)
    if any(keys != {"MARKETING_VERSION", "CURRENT_PROJECT_VERSION"}
           for keys in scopes.values()):
        raise ValueError("Coordinated version keys must share each configuration scope")
    grouped = {key: [token.value for name, token in fields if name == key]
               for key in ("MARKETING_VERSION", "CURRENT_PROJECT_VERSION")}
    if any(not values or len(set(values)) != 1 for values in grouped.values()):
        raise ValueError("Missing or drifting project versions")
    settings = {key: values[0] for key, values in grouped.items()}
    version = resolve(settings["MARKETING_VERSION"], settings, ("MARKETING_VERSION",))
    if resolve(settings["CURRENT_PROJECT_VERSION"], settings, ("CURRENT_PROJECT_VERSION",)) != version:
        raise ValueError("Coordinated CURRENT_PROJECT_VERSION drift")
    data, plist_text, spans = plist_fields(raw[PLIST])
    if "CFBundleShortVersionString" not in data:
        raise ValueError("Missing plist short version")
    for key in ("CFBundleShortVersionString", "CFBundleVersion"):
        if key in data and resolve(data[key], settings) != version:
            raise ValueError(f"Source plist drift: {key}")
    localized = {}
    for path in LOCALIZATIONS:
        source, local_fields = localized_fields(raw[path])
        for key, token in local_fields:
            if resolve(token.value, settings) != version:
                raise ValueError(f"Source localization drift: {path} {key}")
        localized[path] = (source, local_fields)
    return version, settings, (project, fields), (data, plist_text, spans), localized


def bump(mode, root=Path(".")):
    version, settings, project, plist, localized = read(root)
    major, minor, patch = map(int, version.split("."))
    if mode == "patch":
        patch += 1
    elif mode == "minor":
        minor, patch = minor + 1, 0
    elif mode == "major":
        major, minor, patch = major + 1, 0, 0
    else:
        raise ValueError("Unknown bump mode")
    new = f"{major}.{minor}.{patch}"
    replacements = [(token.start, token.end, new) for _, token in project[1]
                    if not REFERENCE.fullmatch(token.value)]
    renders = {PROJECT: project[0].render(replacements)}
    data, text, spans = plist
    if text is None:
        raise ValueError("Source plist must be editable XML")
    replacements = []
    for key in ("CFBundleShortVersionString", "CFBundleVersion"):
        if key in data and not REFERENCE.fullmatch(data[key]):
            if key not in spans:
                raise ValueError(f"Unsupported source plist representation: {key}")
            replacements.append((*spans[key], new))
    renders[PLIST] = text.render(replacements)
    for path, (source, fields) in localized.items():
        renders[path] = source.render([(token.start, token.end, new) for _, token in fields
                                      if not REFERENCE.fullmatch(token.value)])
    if read(root, renders)[0] != new:
        raise ValueError("Rendered version mismatch")
    for path, raw in renders.items():
        if raw != (root / path).read_bytes():
            (root / path).write_bytes(raw)
    return new


def validate_built(bundle, root=Path(".")):
    version, settings, _, source_plist, localized = read(root)
    built, _, _ = plist_fields((bundle / "Contents/Info.plist").read_bytes())
    if built.get("CFBundleShortVersionString") != version:
        raise ValueError("Built plist short version differs from source")
    source_data = source_plist[0]
    if "CFBundleVersion" in source_data:
        if built.get("CFBundleVersion") != resolve(source_data["CFBundleVersion"], settings):
            raise ValueError("Built plist build version differs from source")
    elif "CFBundleVersion" in built:
        raise ValueError("Built plist unexpectedly introduces build version")
    for path in LOCALIZATIONS:
        built_path = bundle / "Contents/Resources" / path
        raw = built_path.read_bytes()
        expected_keys = {key for key, _ in localized[path][1]}
        if raw.startswith(b"bplist") or Text.decode(raw).text.lstrip().startswith(("<plist", "<?xml")):
            data, _, _ = plist_fields(raw)
            actual_keys = set(data) & {"CFBundleShortVersionString", "CFBundleVersion",
                                       "CFBundleGetInfoString"}
            values = [data.get("CFBundleShortVersionString")]
            if "CFBundleGetInfoString" in data:
                matches = re.findall(r"\bversion ([^,\s]+)", data["CFBundleGetInfoString"])
                if len(matches) != 1:
                    raise ValueError("Invalid built localized GetInfo")
                values += matches
            if "CFBundleVersion" in data:
                values.append(data["CFBundleVersion"])
        else:
            _, fields = localized_fields(raw)
            actual_keys = {key for key, _ in fields}
            values = [token.value for _, token in fields]
        if actual_keys != expected_keys or any(value != version for value in values):
            raise ValueError("Built localization differs from source")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("read", "bump", "built"))
    parser.add_argument("--mode", choices=("patch", "minor", "major"))
    parser.add_argument("--bundle", type=Path)
    args = parser.parse_args()
    if args.command == "bump":
        if args.mode is None:
            parser.error("--mode is required")
        print(bump(args.mode))
    elif args.command == "built":
        if args.bundle is None:
            parser.error("--bundle is required")
        validate_built(args.bundle)
    else:
        print(read()[0])


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, ET.ParseError, plistlib.InvalidFileException) as error:
        sys.exit(f"Version validation failed: {error}")
